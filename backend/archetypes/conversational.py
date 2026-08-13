"""The conversational/advisor archetype: no gates, no scoring, no
capabilities — just a described input/output shape and an LLM that answers
or advises against it directly (pipeline: load_input -> reason_llm ->
validate_output -> explain, the same shape as agent_templates.py's "blank"
template, now reachable through AI generation instead of only a static
hand-edit starting point).

Leans entirely on runtime support that already exists: a guidance-only
skill with no rules/*.yaml files (has_rules is False) — evaluate_rules
simply isn't in this archetype's pipeline, so it's never invoked, and
reason_llm/validate_output already tolerate a skill with no rules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .base import Archetype, DEFAULT_MODEL, read_if_exists, register_archetype

_FIELD_TYPES = ["number", "string", "boolean"]

_INPUT_FIELD_SCHEMA = {
    "type": "object",
    "required": ["path", "type", "description", "required"],
    "properties": {
        "path": {"type": "string"},
        "type": {"type": "string", "enum": _FIELD_TYPES},
        "description": {"type": "string"},
        "required": {"type": "boolean", "description": "True only if the agent cannot proceed without this."},
    },
}

_OUTPUT_FIELD_SCHEMA = {
    "type": "object",
    "required": ["path", "type", "description"],
    "properties": {
        "path": {"type": "string"},
        "type": {"type": "string", "enum": _FIELD_TYPES},
        "description": {"type": "string"},
    },
}

CONVERSATIONAL_SPEC_SCHEMA = {
    "type": "object",
    "required": ["purpose", "input_fields", "output_fields", "guidance"],
    "properties": {
        "purpose": {"type": "string"},
        "input_fields": {"type": "array", "items": _INPUT_FIELD_SCHEMA, "minItems": 1, "maxItems": 8},
        "output_fields": {"type": "array", "items": _OUTPUT_FIELD_SCHEMA, "minItems": 1, "maxItems": 6},
        "guidance": {
            "type": "string",
            "description": "How the agent should reason and answer — tone, what to prioritize, what to "
                            "do when information is missing, when to escalate to a human.",
        },
    },
}


# -- validation — no cross-referencing needed, nothing here references anything else ----

def validate_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    input_fields = spec.get("input_fields") or []
    if not input_fields:
        errors.append("input_fields must not be empty")
    input_paths = [f.get("path") for f in input_fields]
    if len(input_paths) != len(set(input_paths)):
        errors.append("input_fields paths must be unique")
    for f in input_fields:
        if f.get("type") not in _FIELD_TYPES:
            errors.append(f"input_field '{f.get('path')}' has invalid type '{f.get('type')}'")

    output_fields = spec.get("output_fields") or []
    if not output_fields:
        errors.append("output_fields must not be empty")
    output_paths = [f.get("path") for f in output_fields]
    if len(output_paths) != len(set(output_paths)):
        errors.append("output_fields paths must be unique")
    for f in output_fields:
        if f.get("type") not in _FIELD_TYPES:
            errors.append(f"output_field '{f.get('path')}' has invalid type '{f.get('type')}'")

    if not (spec.get("guidance") or "").strip():
        errors.append("guidance must not be empty")

    return errors


def auto_repair(spec: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Nothing here is safe to mechanically patch without judgment about
    intent (unlike qualification's missing-fallback-product case) — every
    validation failure needs the LLM to see its own mistake and reconsider.
    """
    return spec


def fallback_spec(purpose: str, agent_id: str) -> dict[str, Any]:
    """Deterministic, no LLM call, always passes validate_spec by
    construction — the conversational-archetype equivalent of
    qualification.fallback_spec's safety floor.
    """
    return {
        "purpose": purpose.strip() or f"Generated agent {agent_id} — description unavailable.",
        "input_fields": [
            {"path": "question", "type": "string", "description": "Placeholder — replace with a real field.", "required": True},
        ],
        "output_fields": [
            {"path": "answer", "type": "string", "description": "Placeholder — replace with a real field."},
        ],
        "guidance": (
            "Placeholder — describe how this agent should reason and answer, then replace this text. "
            "Answer directly and concisely; say so if you don't know rather than guessing."
        ),
    }


# -- prompts ----

def _system_prompt() -> str:
    return (
        "You design a conversational/advisory agent for a banking platform, given a plain-language "
        "description of what it should help with. This agent does NOT make a qualification decision or "
        "compute a score — it answers questions or gives guidance directly, grounded in what the caller "
        "provides. Output ONLY the structured fields requested — no YAML, no code, no commentary.\n\n"
        "Rules:\n"
        "- input_fields describes what the caller provides when starting a conversation (e.g. a "
        "question, a document excerpt, an account id) — prefer simple, flat paths.\n"
        "- output_fields describes what the answer must contain beyond a generic summary/confidence — "
        "e.g. 'answer', 'citations', 'escalation_needed'. Keep it small and focused on what this "
        "specific agent's answer actually needs; most agents only need one or two.\n"
        "- Mark an input_field required: true only if the agent genuinely cannot proceed without it.\n"
        "- guidance is free-text instructions for how you should reason and answer — tone, what sources "
        "to prioritize, what to do when information is missing, when to escalate to a human.\n"
        "- Do not invent gates, scores, decisions, or products — this archetype never produces one."
    )


def _refine_system_prompt() -> str:
    return (
        _system_prompt()
        + "\n\nYou are CORRECTING an existing conversational agent based on a human's feedback about "
          "what's wrong with it, not designing one from scratch. Preserve anything the feedback doesn't "
          "mention as a problem — don't invent unrelated changes. Output the full corrected spec, not a "
          "partial patch."
    )


def _build_user_prompt(purpose: str) -> str:
    return f"Description: {purpose}"


def _refine_user_prompt(current_content: dict[str, str], feedback: str, *, skill_id: str, skill_description: str,
                         target_section: str | None = None) -> str:
    # Nothing to scope by here — this archetype's refine already only ever
    # touches instructions.md (its one refine_write_keys entry), so a
    # per-file target has no second file to distinguish from.
    del target_section
    instructions = current_content.get("instructions.md", "")
    output_contract = current_content.get("output_contract.json", "")
    # Unlike qualification, input_fields live in the agent's agent.yaml
    # (shared across skills), not in this skill's own files, so refine only
    # ever corrects guidance/output_fields for a given skill — a schema-
    # valid input_fields guess is still required in the model's output
    # (the schema requires it), but it's simply never written back (see
    # refine_write_keys), so an unseen/re-guessed value here is harmless.
    return (
        f"You are correcting the conversational agent skill '{skill_id}' ({skill_description}).\n\n"
        f"Current guidance (instructions.md):\n{instructions}\n\n"
        f"Current output contract:\n{output_contract}\n\n"
        f"A human reviewed the whole agent and said: {feedback}\n\n"
        f"Only apply the parts of this feedback that are relevant to '{skill_id}'. "
        f"Ignore anything that clearly belongs to a different skill. Since you can't see this skill's "
        f"current input fields, don't try to change them — focus your correction on the guidance and "
        f"output_fields."
    )


# -- rendering ----

def render_agent_yaml(agent_id: str, skill_ids: list[str], spec: dict[str, Any]) -> str:
    purpose = (spec.get("purpose") or "").strip() or "Generated agent — describe further via editor."

    input_fields = spec.get("input_fields", [])
    input_schema = {
        "type": "object",
        "required": [f["path"] for f in input_fields if f.get("required")],
        "properties": {
            f["path"]: {"type": f["type"], "description": f["description"]} for f in input_fields
        },
    }

    output_fields = spec.get("output_fields", [])
    output_schema = {
        "type": "object",
        "required": [f["path"] for f in output_fields] + ["confidence"],
        "properties": {
            **{f["path"]: {"type": f["type"]} for f in output_fields},
            "confidence": {"type": "number"},
        },
    }

    # Every LLM-authored string here (purpose, field descriptions) goes into the
    # doc as a plain Python value and is escaped by yaml.safe_dump — never spliced
    # into YAML text directly, so it can't break structure or inject sibling keys.
    doc = {
        "agent_id": agent_id,
        "version": "1.0.0",
        "purpose": purpose,
        "skills": list(skill_ids),
        "pipeline": ["load_input", "reason_llm", "validate_output", "explain"],
        "capabilities": [],
        "governance": {
            "hitl_conditions": ["low_confidence", "validation_degraded"],
            "confidence_threshold": 0.6,
            "max_llm_retries": 1,
        },
        "llm": {"model": DEFAULT_MODEL, "temperature": 0.0, "seed": 7, "timeout_seconds": 120},
        "draft": True,
        "routable": False,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }
    rendered = yaml.safe_dump(doc, sort_keys=False)
    yaml.safe_load(rendered)  # parse-and-validate before this is ever written to disk
    return rendered


def render_skill_files(skill_id: str, spec: dict[str, Any]) -> dict[str, str]:
    purpose = (spec.get("purpose") or "").strip() or "Generated skill."
    purpose_inline = " ".join(purpose.split())
    guidance = (spec.get("guidance") or "").strip() or "Answer directly and concisely."
    output_fields = spec.get("output_fields", [])

    output_contract = {
        "type": "object",
        "properties": {
            **{f["path"]: {"type": f["type"], "description": f.get("description", "")} for f in output_fields},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [f["path"] for f in output_fields] + ["confidence"],
    }

    instructions_md = (
        f"# {skill_id}\n\n{guidance}\n\n"
        "Use ONLY the facts under `evidence`/the caller's input in the prompt payload — don't invent "
        "information you weren't given. If you're not confident in an answer, say so rather than "
        "guessing, and reflect that in `confidence`.\n\n"
        f"---\n_Generated by Agent Builder from: {purpose_inline}_\n"
    )

    skill_doc = {
        "skill_id": skill_id,
        "version": "1.0.0",
        "kind": "procedural",
        "archetype": "conversational",
        "description": purpose,
        "instructions": "instructions.md",
        "output_contract": "output_contract.json",
        "shared_includes": ["shared/compliance_guardrails.md"],
        "rules": {},
    }
    skill_yaml = yaml.safe_dump(skill_doc, sort_keys=False)
    yaml.safe_load(skill_yaml)  # parse-and-validate before this is ever written to disk

    return {
        "skill.yaml": skill_yaml,
        "instructions.md": instructions_md,
        "output_contract.json": json.dumps(output_contract, indent=2),
    }


def _read_refine_context(skill_dir: Path) -> dict[str, str]:
    return {
        "instructions.md": read_if_exists(skill_dir / "instructions.md"),
        "output_contract.json": read_if_exists(skill_dir / "output_contract.json"),
    }


register_archetype(Archetype(
    id="conversational",
    label="Conversational / Advisor",
    description=(
        "No gates, no scoring, no capabilities — just a described input/"
        "output shape and an LLM that answers or advises directly against "
        "it. Use this for Q&A, guidance, or advisory agents that don't make "
        "a qualification decision."
    ),
    spec_schema=CONVERSATIONAL_SPEC_SCHEMA,
    build_system_prompt=_system_prompt,
    build_refine_system_prompt=_refine_system_prompt,
    build_user_prompt=_build_user_prompt,
    build_refine_user_prompt=_refine_user_prompt,
    validate=validate_spec,
    auto_repair=auto_repair,
    fallback_spec=fallback_spec,
    render_agent_yaml=render_agent_yaml,
    render_skill_files=render_skill_files,
    merge_field="input_fields",
    primary_only_fields=["output_fields"],
    read_refine_context=_read_refine_context,
    refine_write_keys=["instructions.md", "output_contract.json"],
))
