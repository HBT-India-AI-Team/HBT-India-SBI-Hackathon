"""Tests for the New Agent start-from-template feature — backend/agent_templates.py
and backend/admin.py's create_agent(). The round-trip test is the important
one: it protects the "gates_scoring" placeholder rule content from silently
rotting into something that crashes on first run.
"""
import yaml
import pytest
from fastapi import HTTPException

import capabilities_impl  # noqa: F401  (registers mock tools)
import agent_platform.composition.loader as loader
import backend.admin as admin
from agent_platform.composition import load_agent
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages
from backend import agent_templates
from backend.admin import NewAgentPayload

from fakes import FakeAdapter


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


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


def test_list_templates_includes_blank_and_gates_scoring():
    ids = {t["id"] for t in agent_templates.list_templates()}
    assert ids == {"blank", "gates_scoring"}


def test_blank_template_matches_todays_create_agent_output():
    agent_yaml_text = agent_templates.get_template("blank").render_agent_yaml("acme", "acme", "purpose")
    parsed = yaml.safe_load(agent_yaml_text)
    assert parsed["pipeline"] == ["load_input", "reason_llm", "validate_output", "explain"]
    assert parsed["capabilities"] == []


def test_gates_scoring_renders_all_four_rule_groups():
    files = agent_templates.get_template("gates_scoring").render_skill_files("x", "purpose")
    assert set(files) >= {
        "skill.yaml", "instructions.md", "output_contract.json",
        "rules/gates.yaml", "rules/factors.yaml", "rules/composite.yaml", "rules/product_fit.yaml",
    }

    gates = yaml.safe_load(files["rules/gates.yaml"])
    gate = gates["gates"][0]
    assert {"id", "field", "operator", "value", "on_fail"} <= gate.keys()
    assert {"decision", "reason"} <= gate["on_fail"].keys()

    factors = yaml.safe_load(files["rules/factors.yaml"])
    factor = factors["categories"]["overall"]["factors"][0]
    assert {"id", "field", "weight", "bands"} <= factor.keys()
    assert len(factor["bands"]) > 0

    composite = yaml.safe_load(files["rules/composite.yaml"])
    assert "qualified_min" in composite["thresholds"]
    assert "conditional_min" in composite["thresholds"]

    product_fit = yaml.safe_load(files["rules/product_fit.yaml"])
    assert any(p.get("when") == [] for p in product_fit["products"])


def test_gates_scoring_agent_yaml_declares_full_pipeline():
    template = agent_templates.get_template("gates_scoring")
    parsed = yaml.safe_load(template.render_agent_yaml("x", "x", "purpose"))
    assert parsed["pipeline"] == template.pipeline
    assert parsed["capabilities"] == []
    assert parsed["input_schema"]["required"] == []


def test_create_agent_rejects_unknown_template_id(tmp_agent_dirs):
    with pytest.raises(HTTPException) as exc_info:
        admin.create_agent(NewAgentPayload(agent_id="whatever", template_id="nope"))
    assert exc_info.value.status_code == 400


def test_create_agent_gates_scoring_end_to_end_round_trip(tmp_agent_dirs):
    agent_id = "rt_template_agent"
    result = admin.create_agent(NewAgentPayload(
        agent_id=agent_id, purpose="Round-trip test agent", template_id="gates_scoring",
    ))
    assert result["status"] == "created"
    assert result["template_id"] == "gates_scoring"

    load_agent(agent_id, force_reload=True)  # must parse cleanly

    ctx_empty = invoke_agent(agent_id, {"evidence": {}})
    assert ctx_empty.error is None
    assert ctx_empty.decision["outcome"] == "NOT_QUALIFIED"
    assert ctx_empty.decision["composite_score"] == 30
    assert any(
        p["id"] == "EXAMPLE_PRODUCT"
        for p in ctx_empty.rule_results[ctx_empty.governing_skill_id]["products"]
    )

    ctx_qualified = invoke_agent(agent_id, {"evidence": {"score": 90}})
    assert ctx_qualified.error is None
    assert ctx_qualified.decision["outcome"] == "QUALIFIED"

    ctx_gated = invoke_agent(agent_id, {"evidence": {"flagged": True}})
    assert ctx_gated.error is None
    assert ctx_gated.decision["outcome"] == "NOT_QUALIFIED"
    assert len(ctx_gated.rule_results[ctx_gated.governing_skill_id]["gates"]["failures"]) > 0


def test_create_agent_reusing_existing_skill_skips_template_rendering(tmp_agent_dirs):
    admin.create_agent(NewAgentPayload(
        agent_id="agent_a", skill_id="shared_skill", purpose="a", template_id="gates_scoring",
    ))
    _, skills_dir = tmp_agent_dirs
    gates_before = (skills_dir / "shared_skill" / "rules" / "gates.yaml").read_text(encoding="utf-8")

    admin.create_agent(NewAgentPayload(
        agent_id="agent_b", skill_id="shared_skill", purpose="b", template_id="blank",
    ))
    gates_after = (skills_dir / "shared_skill" / "rules" / "gates.yaml").read_text(encoding="utf-8")

    assert gates_before == gates_after
