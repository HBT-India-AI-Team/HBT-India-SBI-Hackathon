"""Tests for DELETE /admin/agents/{agent_id} (backend/admin.py's delete_agent)
and the cache-eviction fix it depends on (agent_platform/composition/loader.py's
evict()).
"""
import pytest
from fastapi import HTTPException

import capabilities_impl  # noqa: F401  (registers mock tools)
import agent_platform.composition.loader as loader
import backend.admin as admin
from agent_platform.composition import list_agents, load_agent
from backend.admin import NewAgentPayload


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


def test_delete_agent_removes_directory_and_delists_it(tmp_agent_dirs):
    admin.create_agent(NewAgentPayload(agent_id="to_delete", purpose="p", template_id="blank"))
    assert "to_delete" in list_agents()

    result = admin.delete_agent("to_delete")

    assert result == {"status": "deleted", "agent_id": "to_delete"}
    assert "to_delete" not in list_agents()


def test_delete_agent_evicts_the_cache(tmp_agent_dirs):
    admin.create_agent(NewAgentPayload(agent_id="to_delete", purpose="p", template_id="blank"))
    load_agent("to_delete")  # populate the cache

    admin.delete_agent("to_delete")

    with pytest.raises(FileNotFoundError):
        load_agent("to_delete")  # not force_reload — must not serve a stale cached bundle


def test_delete_unknown_agent_is_404(tmp_agent_dirs):
    with pytest.raises(HTTPException) as exc_info:
        admin.delete_agent("does_not_exist")
    assert exc_info.value.status_code == 404


def test_delete_leaves_shared_skill_untouched(tmp_agent_dirs):
    admin.create_agent(NewAgentPayload(
        agent_id="agent_a", skill_id="shared_skill", purpose="a", template_id="gates_scoring",
    ))
    admin.create_agent(NewAgentPayload(
        agent_id="agent_b", skill_id="shared_skill", purpose="b", template_id="blank",
    ))
    _, skills_dir = tmp_agent_dirs
    skill_yaml_before = (skills_dir / "shared_skill" / "skill.yaml").read_text(encoding="utf-8")

    admin.delete_agent("agent_a")

    assert (skills_dir / "shared_skill").exists()
    assert (skills_dir / "shared_skill" / "skill.yaml").read_text(encoding="utf-8") == skill_yaml_before
    assert "agent_b" in list_agents()
