"""Tests for the multi-skill admin editor endpoints (backend/admin.py):
GET/PUT .../files' nested-by-skill-id shape, GET /admin/skills, and the
unified add/remove skill endpoints — one skill type, optionally with rules,
no more separate deterministic/dynamic split. Uses the same tmp_agent_dirs
fixture pattern as tests/test_delete_agent.py / tests/test_agent_templates.py.
"""
import pytest
from fastapi import HTTPException

import capabilities_impl  # noqa: F401  (registers mock tools)
import agent_platform.composition.loader as loader
import backend.admin as admin
from agent_platform.composition import load_agent
from backend.admin import AddSkillPayload, AgentFilesPayload, NewAgentPayload, SkillFiles


@pytest.fixture
def tmp_agent_dirs(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills_library"
    agents_dir.mkdir()
    skills_dir.mkdir()
    (skills_dir / "shared").mkdir()
    (skills_dir / "shared" / "compliance_guardrails.md").write_text("Test guardrails.\n", encoding="utf-8")

    monkeypatch.setattr(loader, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(admin, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(admin, "SKILLS_DIR", skills_dir)
    loader._registry.clear()
    return agents_dir, skills_dir


def _make_gates_scoring_agent(agent_id="a1", skill_id=None):
    admin.create_agent(NewAgentPayload(
        agent_id=agent_id, skill_id=skill_id, purpose="test agent", template_id="gates_scoring",
    ))


def test_get_agent_files_single_skill_shape(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")

    files = admin.get_agent_files("a1")

    assert set(files["skills"].keys()) == {"a1"}
    assert "rules/gates.yaml" not in files["skills"]["a1"]  # not a raw key; rules are nested
    assert set(files["skills"]["a1"]["rules"].keys()) == {"gates", "factors", "composite", "product_fit"}


def test_add_skill_scaffold_with_rules_appends_to_skills_list_and_is_visible(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")

    result = admin.add_skill("a1", AddSkillPayload(
        skill_id="a1_extra", mode="scaffold", has_rules=True, template_id="gates_scoring", purpose="extra rules",
    ))

    assert result["status"] == "ok"
    files = admin.get_agent_files("a1")
    assert set(files["skills"].keys()) == {"a1", "a1_extra"}
    assert files["skills"]["a1_extra"]["output_contract_json"]  # a real rules-bearing skill

    bundle = load_agent("a1", force_reload=True)
    assert set(bundle.skills.keys()) == {"a1", "a1_extra"}
    assert bundle.skills["a1_extra"].has_rules is True


def test_add_skill_scaffold_without_rules_creates_guidance_only_skill(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")

    result = admin.add_skill("a1", AddSkillPayload(
        skill_id="explain_x", mode="scaffold", has_rules=False, description="explain thing x",
    ))

    assert result["status"] == "ok"
    bundle = load_agent("a1", force_reload=True)
    assert set(bundle.skills.keys()) == {"a1", "explain_x"}
    assert bundle.skills["explain_x"].has_rules is False
    assert bundle.skills["explain_x"].description.strip() == "explain thing x"


def test_add_skill_scaffold_rejects_existing_skill_dir(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    _make_gates_scoring_agent("a2", skill_id="a2")  # creates skills_library/a2

    with pytest.raises(HTTPException) as exc_info:
        admin.add_skill("a1", AddSkillPayload(skill_id="a2", mode="scaffold"))
    assert exc_info.value.status_code == 400
    assert "attach_existing" in exc_info.value.detail


def test_add_skill_attach_existing_reuses_files(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    _make_gates_scoring_agent("a2", skill_id="a2")
    _, skills_dir = tmp_agent_dirs
    gates_before = (skills_dir / "a2" / "rules" / "gates.yaml").read_text(encoding="utf-8")

    result = admin.add_skill("a1", AddSkillPayload(skill_id="a2", mode="attach_existing"))

    assert result["status"] == "ok"
    gates_after = (skills_dir / "a2" / "rules" / "gates.yaml").read_text(encoding="utf-8")
    assert gates_before == gates_after  # untouched, not re-scaffolded


def test_add_skill_attach_existing_allows_a_guidance_only_skill(tmp_agent_dirs):
    """No more deterministic/procedural rejection on attach — any existing
    skill can be attached to any agent regardless of whether it has rules.
    """
    _make_gates_scoring_agent("a1")
    _make_gates_scoring_agent("a2", skill_id="a2")
    admin.add_skill("a2", AddSkillPayload(skill_id="proc1", mode="scaffold", has_rules=False))  # not referenced by a1

    result = admin.add_skill("a1", AddSkillPayload(skill_id="proc1", mode="attach_existing"))

    assert result["status"] == "ok"
    bundle = load_agent("a1", force_reload=True)
    assert "proc1" in bundle.skills
    assert bundle.skills["proc1"].has_rules is False


def test_add_skill_rejects_already_referenced_id(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")

    with pytest.raises(HTTPException) as exc_info:
        admin.add_skill("a1", AddSkillPayload(skill_id="a1", mode="scaffold"))
    assert exc_info.value.status_code == 400


def test_remove_skill_allows_removing_the_first_declared_skill(tmp_agent_dirs):
    """No more protected "primary" skill — every skill in the list is
    equally removable, including the first-declared one.
    """
    _make_gates_scoring_agent("a1")
    admin.add_skill("a1", AddSkillPayload(skill_id="a1_extra", mode="scaffold", template_id="gates_scoring"))

    result = admin.remove_skill("a1", "a1")

    assert result["status"] == "ok"
    files = admin.get_agent_files("a1")
    assert set(files["skills"].keys()) == {"a1_extra"}


def test_remove_skill_leaves_skill_package_on_disk(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    admin.add_skill("a1", AddSkillPayload(skill_id="a1_extra", mode="scaffold", template_id="gates_scoring"))
    _, skills_dir = tmp_agent_dirs

    result = admin.remove_skill("a1", "a1_extra")

    assert result["status"] == "ok"
    files = admin.get_agent_files("a1")
    assert set(files["skills"].keys()) == {"a1"}
    assert (skills_dir / "a1_extra").exists()  # skill package left on disk


def test_remove_skill_unknown_is_404(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    with pytest.raises(HTTPException) as exc_info:
        admin.remove_skill("a1", "not_referenced")
    assert exc_info.value.status_code == 404


def test_list_skills_infers_kind_structurally(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    admin.add_skill("a1", AddSkillPayload(skill_id="explain_x", mode="scaffold", has_rules=False))

    catalog = {s["skill_id"]: s["kind"] for s in admin.list_skills()["skills"]}

    assert catalog["a1"] == "deterministic"
    assert catalog["explain_x"] == "procedural"


def test_save_agent_files_round_trip_edits_persist(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    files = admin.get_agent_files("a1")

    payload = AgentFilesPayload(
        agent_yaml=files["agent_yaml"],
        skills={
            "a1": SkillFiles(
                skill_yaml=files["skills"]["a1"]["skill_yaml"],
                instructions_md="Edited instructions.\n",
                task_prompt_md=files["skills"]["a1"]["task_prompt_md"],
                output_contract_json=files["skills"]["a1"]["output_contract_json"],
                rules=files["skills"]["a1"]["rules"],
            ),
        },
    )
    result = admin.save_agent_files("a1", payload)

    assert result["status"] == "ok"
    assert admin.get_agent_files("a1")["skills"]["a1"]["instructions_md"] == "Edited instructions.\n"


def test_save_agent_files_rejects_skill_key_mismatch(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    files = admin.get_agent_files("a1")

    payload = AgentFilesPayload(
        agent_yaml=files["agent_yaml"],
        skills={
            "wrong_key": SkillFiles(
                skill_yaml=files["skills"]["a1"]["skill_yaml"],
                rules=files["skills"]["a1"]["rules"],
            ),
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        admin.save_agent_files("a1", payload)
    assert exc_info.value.status_code == 400
    assert "payload.skills keys" in exc_info.value.detail


def test_save_agent_files_rejects_guidance_only_skill_yaml_id_mismatch(tmp_agent_dirs):
    _make_gates_scoring_agent("a1")
    admin.add_skill("a1", AddSkillPayload(skill_id="explain_x", mode="scaffold", has_rules=False))
    files = admin.get_agent_files("a1")

    bad_guidance_skill = SkillFiles(
        skill_yaml=files["skills"]["explain_x"]["skill_yaml"].replace(
            "skill_id: explain_x", "skill_id: something_else"
        ),
        instructions_md=files["skills"]["explain_x"]["instructions_md"],
    )
    payload = AgentFilesPayload(
        agent_yaml=files["agent_yaml"],
        skills={"a1": SkillFiles(**files["skills"]["a1"]), "explain_x": bad_guidance_skill},
    )
    with pytest.raises(HTTPException) as exc_info:
        admin.save_agent_files("a1", payload)
    assert exc_info.value.status_code == 400
    assert "declares skill_id" in exc_info.value.detail
