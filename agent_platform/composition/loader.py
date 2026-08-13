"""AgentLoader: turns YAML/Markdown on disk into a callable AgentBundle.

Deliberately small. This is the "Agent Composer" from the architecture
diagram, scoped down to what a demo needs: read an agent's config, read its
skill package, combine them, cache the result. No factory hierarchy, no
plugin system. Adding a new agent = adding a new agents/<id>/agent.yaml and
a new skills_library/<skill_id>/ directory; this file does not change.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import AgentBundle, AgentDefinition, Skill

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
SKILLS_DIR = REPO_ROOT / "skills_library"

_registry: dict[str, AgentBundle] = {}


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_skill(skill_id: str) -> Skill:
    skill_dir = SKILLS_DIR / skill_id
    manifest = _read_yaml(skill_dir / "skill.yaml")

    instructions_text = (skill_dir / manifest["instructions"]).read_text(encoding="utf-8")

    shared_text = "\n\n".join(
        (SKILLS_DIR / rel).read_text(encoding="utf-8")
        for rel in manifest.get("shared_includes", [])
    )

    task_prompt_text = ""
    if manifest.get("task_prompt"):
        task_prompt_text = (skill_dir / manifest["task_prompt"]).read_text(encoding="utf-8")

    output_contract = None
    if manifest.get("output_contract"):
        output_contract_path = skill_dir / manifest["output_contract"]
        output_contract = json.loads(output_contract_path.read_text(encoding="utf-8"))

    rules = None
    if manifest.get("rules"):
        rules = {
            rule_name: _read_yaml(skill_dir / rel_path)
            for rule_name, rel_path in manifest["rules"].items()
        }

    return Skill(
        skill_id=manifest["skill_id"],
        version=manifest["version"],
        description=manifest.get("description", ""),
        instructions_text=instructions_text,
        shared_text=shared_text,
        task_prompt_text=task_prompt_text,
        output_contract=output_contract,
        rules=rules,
        archetype=manifest.get("archetype"),
    )


def load_agent(agent_id: str, *, force_reload: bool = False) -> AgentBundle:
    """Load (and cache) an agent by id. Reads agents/<agent_id>/agent.yaml,
    resolves every declared skill, and returns a ready-to-run AgentBundle.
    """
    if not force_reload and agent_id in _registry:
        return _registry[agent_id]

    definition_path = AGENTS_DIR / agent_id / "agent.yaml"
    raw_definition = _read_yaml(definition_path)
    definition = AgentDefinition.model_validate(raw_definition)

    if definition.agent_id != agent_id:
        raise ValueError(
            f"agent.yaml at {definition_path} declares agent_id="
            f"'{definition.agent_id}', expected '{agent_id}'"
        )

    skills = {skill_id: _load_skill(skill_id) for skill_id in definition.skills}

    bundle = AgentBundle(definition=definition, skills=skills)

    _registry[agent_id] = bundle
    return bundle


def list_agents() -> list[str]:
    """All agent_ids discoverable under agents/ (each subdir with an
    agent.yaml)."""
    if not AGENTS_DIR.exists():
        return []
    return sorted(
        p.parent.name for p in AGENTS_DIR.glob("*/agent.yaml")
    )


def evict(agent_id: str) -> None:
    """Drop a cached bundle so a deleted agent stops being invocable via a
    stale in-memory reference once its files are gone from disk.
    """
    _registry.pop(agent_id, None)
