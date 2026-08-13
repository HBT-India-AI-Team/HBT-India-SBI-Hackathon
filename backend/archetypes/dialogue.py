"""The dialogue archetype: an agent that writes plain-text lines for one or
more "voices" (e.g. an NPC, a companion character, a narrator) — never a
decision, never JSON. Each voice becomes its own skill, generated the same
way qualification's multi-skill split works (backend/agent_builder.py's
decompose_purpose already splits "distinct request types" into separate
skills; a description enumerating several voices splits the same way,
just landing here instead of in gates/scoring).

Leans entirely on runtime support built for this: agent_platform/stages/
pipeline_stages.py's reason_llm_text (no JSON-format constraint on the
Ollama call) and validate_text_output (strips wrapping artifacts only,
never touches wording), plus load_skills' existing explicit-override path
(a caller-supplied skill_id deterministically selects the active voice,
no LLM routing call, no risk of voices blending).
"""
from __future__ import annotations

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
        "required": {"type": "boolean", "description": "True only if this voice cannot proceed without it."},
    },
}

DIALOGUE_SPEC_SCHEMA = {
    "type": "object",
    "required": ["purpose", "input_fields", "guidance"],
    "properties": {
        "purpose": {"type": "string"},
        "input_fields": {"type": "array", "items": _INPUT_FIELD_SCHEMA, "minItems": 1, "maxItems": 8},
        "guidance": {
            "type": "string",
            "description": "How this voice should write its line(s) — persona/tone, what to react to, "
                            "sentence or line-count limits, and any output-format rules (e.g. 'first line "
                            "is a question, each following line is one option'). Still plain text output — "
                            "describe the shape in words, never as a JSON schema.",
        },
    },
}


# -- validation — no cross-referencing needed, one voice is self-contained ----

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

    if not (spec.get("guidance") or "").strip():
        errors.append("guidance must not be empty")

    return errors


def auto_repair(spec: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Nothing here is safe to mechanically patch without judgment about
    intent — same reasoning as conversational.auto_repair.
    """
    return spec


def fallback_spec(purpose: str, agent_id: str) -> dict[str, Any]:
    """Deterministic, no LLM call, always passes validate_spec by
    construction — the dialogue-archetype equivalent of the other
    archetypes' safety floor.
    """
    return {
        "purpose": purpose.strip() or f"Generated voice {agent_id} — description unavailable.",
        "input_fields": [
            {"path": "context", "type": "string", "description": "Placeholder — replace with a real field.", "required": True},
        ],
        "guidance": (
            "Placeholder — describe this voice's persona, what it reacts to, and its sentence/line "
            "limits, then replace this text. Output plain text only, one to two sentences."
        ),
    }


# -- prompts ----

def _system_prompt() -> str:
    return (
        "You design one VOICE of a multi-voice dialogue-generation agent (e.g. one character, one "
        "narrator, one specific kind of line an app needs to write), given a plain-language "
        "description of what this voice should say. This voice never makes a decision, never scores "
        "or qualifies anything, and never outputs JSON, markdown, or any structured format — its "
        "entire output is the plain-text line(s) a human or player will actually see on screen. "
        "Output ONLY the structured fields requested for defining the voice — no YAML, no code, no "
        "commentary.\n\n"
        "Rules:\n"
        "- input_fields describes what context this voice is given when asked to write a line (e.g. "
        "a topic, a recent event, an exact fact to rephrase) — prefer simple, flat paths.\n"
        "- Mark an input_field required: true only if this voice genuinely cannot write a line "
        "without it.\n"
        "- guidance is free-text instructions for how this voice should write — persona/tone, what "
        "to react to, sentence or line-count limits, and any output-format rules. If the description "
        "implies a structured plain-text shape (e.g. a question followed by selectable options, one "
        "per line), describe that shape in guidance as plain-text formatting instructions — never as "
        "a JSON schema, since the model's actual output is always raw text.\n"
        "- Never invent scoring, decisions, gates, or products — this archetype never produces one.\n"
        "- If the description enumerates several distinct voices/characters/line-types, you are only "
        "designing the ONE voice described in this call's Description — the other voices are handled "
        "by separate calls."
    )


def _refine_system_prompt() -> str:
    return (
        _system_prompt()
        + "\n\nYou are CORRECTING an existing voice based on a human's feedback about what's wrong "
          "with it, not designing one from scratch. Preserve anything the feedback doesn't mention as "
          "a problem — don't invent unrelated changes. Output the full corrected spec, not a partial "
          "patch."
    )


def _build_user_prompt(purpose: str) -> str:
    return f"Description: {purpose}"


def _refine_user_prompt(current_content: dict[str, str], feedback: str, *, skill_id: str, skill_description: str,
                         target_section: str | None = None) -> str:
    del target_section  # only one refine_write_keys entry — nothing to scope between
    instructions = current_content.get("instructions.md", "")
    # Same reasoning as conversational._refine_user_prompt: input_fields live in the agent's
    # agent.yaml (shared across every voice's skill_id enum), not in this skill's own files, so
    # refine only ever corrects guidance for a given voice.
    return (
        f"You are correcting the dialogue voice '{skill_id}' ({skill_description}).\n\n"
        f"Current guidance (instructions.md):\n{instructions}\n\n"
        f"A human reviewed the whole agent and said: {feedback}\n\n"
        f"Only apply the parts of this feedback that are relevant to '{skill_id}'. "
        f"Ignore anything that clearly belongs to a different voice. Since you can't see this voice's "
        f"current input fields, don't try to change them — focus your correction on the guidance."
    )


# -- rendering ----

def render_agent_yaml(agent_id: str, skill_ids: list[str], spec: dict[str, Any]) -> str:
    purpose = (spec.get("purpose") or "").strip() or "Generated dialogue agent — describe further via editor."

    # Every input field is documented for the caller, but never marked required at the agent
    # level — each one only applies to whichever voice(s) declared it, and a request for a
    # different voice must not be rejected for omitting a field that voice never uses. Only
    # skill_id (the voice selector) is required on every call.
    input_fields = spec.get("input_fields", [])
    properties = {
        "skill_id": {
            "type": "string",
            "enum": list(skill_ids),
            "description": (
                "Selects which voice generates this response. Required on every call — no LLM "
                "routing happens, so an unrecognized value is rejected rather than silently "
                "falling back to a different voice."
            ),
        },
        **{f["path"]: {"type": f["type"], "description": f["description"]} for f in input_fields},
    }

    # Every LLM-authored string here (purpose, field descriptions) goes into the doc as a plain
    # Python value and is escaped by yaml.safe_dump — never spliced into YAML text directly.
    doc = {
        "agent_id": agent_id,
        "version": "1.0.0",
        "purpose": purpose,
        "skills": list(skill_ids),
        "pipeline": ["load_input", "load_skills", "reason_llm_text", "validate_text_output", "hitl_gate", "explain"],
        "capabilities": [],
        "governance": {
            "hitl_conditions": ["validation_degraded"],
            "confidence_threshold": 0.6,
            "max_llm_retries": 1,
        },
        "llm": {"model": DEFAULT_MODEL, "temperature": 0.7, "seed": 7, "timeout_seconds": 60},
        "draft": True,
        "routable": False,
        "input_mode": "form",
        "input_schema": {
            "type": "object",
            "required": ["skill_id"],
            "properties": properties,
        },
        "output_schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The plain-text line(s) to display — never JSON, never markdown.",
                },
            },
        },
    }
    rendered = yaml.safe_dump(doc, sort_keys=False)
    yaml.safe_load(rendered)  # parse-and-validate before this is ever written to disk
    return rendered


def render_skill_files(skill_id: str, spec: dict[str, Any]) -> dict[str, str]:
    purpose = (spec.get("purpose") or "").strip() or "Generated voice."
    purpose_inline = " ".join(purpose.split())
    guidance = (spec.get("guidance") or "").strip() or "Write one to two plain sentences."
    input_fields = spec.get("input_fields", [])

    field_lines = "\n".join(
        f"- `{f['path']}` ({f['type']}): {f['description']}" for f in input_fields
    ) or "(no input fields declared)"

    instructions_md = (
        f"# {skill_id}\n\n{guidance}\n\n"
        f"You are given:\n{field_lines}\n\n"
        "Output plain text only — never JSON, never markdown, never a quotation mark wrapping the "
        "whole response, no labels or commentary before or after the line. Your entire response is "
        "exactly what should appear on screen. Never break character, never exceed the sentence/line "
        "limits described above.\n\n"
        f"---\n_Generated by Agent Builder from: {purpose_inline}_\n"
    )

    skill_doc = {
        "skill_id": skill_id,
        "version": "1.0.0",
        "kind": "procedural",
        "archetype": "dialogue",
        "description": purpose,
        "instructions": "instructions.md",
        "rules": {},
    }
    skill_yaml = yaml.safe_dump(skill_doc, sort_keys=False)
    yaml.safe_load(skill_yaml)  # parse-and-validate before this is ever written to disk

    return {
        "skill.yaml": skill_yaml,
        "instructions.md": instructions_md,
    }


def _read_refine_context(skill_dir: Path) -> dict[str, str]:
    return {"instructions.md": read_if_exists(skill_dir / "instructions.md")}


register_archetype(Archetype(
    id="dialogue",
    label="Dialogue / Multi-Voice",
    description=(
        "One or more \"voices\" (a character, a narrator, a specific kind of line) that write "
        "plain text only — never JSON, never a decision. The caller selects which voice with an "
        "explicit skill_id on every call, so voices never blend. Use this for game/app dialogue, "
        "character lines, or any agent whose job is writing text a human will actually read on "
        "screen, not scoring or narrating a decision."
    ),
    spec_schema=DIALOGUE_SPEC_SCHEMA,
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
    read_refine_context=_read_refine_context,
    refine_write_keys=["instructions.md"],
))
