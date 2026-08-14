"""The archetype registry: each Archetype bundles everything the AI
"describe it" generator and "Fix with AI" refiner need to produce one shape
of agent — its own JSON schema, prompts, validation, and file rendering.

Adding a new archetype means writing one new module (see qualification.py /
conversational.py for the two that exist) and registering it here — nothing
elsewhere in the generation pipeline (agent_builder.py, admin.py) needs to
change, the same way adding a new pipeline stage only means adding a new
@register_stage function (agent_platform/runtime/pipeline.py).

Deliberately NOT "generate arbitrary pipelines/capabilities from a
description" — pipeline stages and capabilities are Python code
(agent_platform/runtime/pipeline.py's STAGE_REGISTRY,
agent_platform/capabilities/tool.py's DEFAULT_REGISTRY), not something an
LLM can safely author at generation time. An archetype is a fixed, curated
pipeline shape + schema; the LLM only ever fills in the schema's fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Shared by every archetype's render_agent_yaml — not archetype-specific,
# just the model that is actually available in this environment.
DEFAULT_MODEL = "gemma4:12b"


def read_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


@dataclass(frozen=True)
class Archetype:
    id: str
    label: str
    description: str

    spec_schema: dict[str, Any]

    build_system_prompt: Callable[[], str]
    build_refine_system_prompt: Callable[[], str]
    build_user_prompt: Callable[[str], str]  # purpose -> generation user prompt
    # (current_content, feedback, *, skill_id, skill_description) -> refine user prompt
    build_refine_user_prompt: Callable[..., str]

    validate: Callable[[dict[str, Any]], list[str]]
    auto_repair: Callable[[dict[str, Any], list[str]], dict[str, Any]]
    fallback_spec: Callable[[str, str], dict[str, Any]]  # purpose, agent_id -> always-valid spec

    render_agent_yaml: Callable[[str, list[str], dict[str, Any]], str]
    render_skill_files: Callable[[str, dict[str, Any]], dict[str, str]]

    # Which field (a list of {path, ...} dicts, same convention across
    # archetypes) gets merged across skills when combining a multi-skill
    # generation into one agent.yaml's declared fields.
    merge_field: str

    # Reads whatever "current spec on disk" a refine call needs as context,
    # keyed however build_refine_user_prompt expects (archetype-specific —
    # qualification reads its 4 rules/*.yaml files by rule-group name,
    # conversational reads instructions.md/output_contract.json directly).
    read_refine_context: Callable[[Path], dict[str, str]]
    # Subset of render_skill_files(...)'s output keys that a successful
    # refine is allowed to overwrite on disk.
    refine_write_keys: list[str]

    # Extra top-level spec keys render_agent_yaml needs that aren't merged
    # across skills (unlike merge_field) — copied verbatim from the primary
    # skill's own spec only. Qualification needs none (its top-level
    # agent.yaml output_schema is a fixed decision shape, not derived from
    # any skill's spec); conversational needs output_fields, since its
    # top-level output_schema genuinely reflects one skill's own contract.
    primary_only_fields: list[str] = field(default_factory=list)

    # Refine-only repair, run (if present) after auto_repair still leaves errors — separate
    # from auto_repair because it needs current_content (what the rules looked like *before*
    # this edit) to safely tell "the model dropped something that was legitimately there" apart
    # from "the model invented/typo'd a field name", which auto_repair alone can't distinguish
    # during fresh generation (no prior content exists then). None for archetypes that don't
    # need it (conversational/dialogue have no cross-referencing to reconcile).
    repair_with_context: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]] | None = None


ARCHETYPES: dict[str, Archetype] = {}


def register_archetype(archetype: Archetype) -> None:
    ARCHETYPES[archetype.id] = archetype


def get_archetype(archetype_id: str) -> Archetype:
    if archetype_id not in ARCHETYPES:
        raise KeyError(f"Unknown archetype_id '{archetype_id}'. Known: {sorted(ARCHETYPES)}")
    return ARCHETYPES[archetype_id]


def list_archetypes() -> list[dict[str, str]]:
    return [{"id": a.id, "label": a.label, "description": a.description} for a in ARCHETYPES.values()]
