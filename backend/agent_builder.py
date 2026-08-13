"""Agent Builder: turns a plain-language description into a real agent —
full custom rules/schema, not a template with placeholders filled in.

What shape the generated agent takes (gates/scoring, conversational/
advisor, ...) is decided by an Archetype (see backend/archetypes/) — this
module owns the parts that are the same regardless of archetype: deciding
whether a description needs one skill or several (decompose_purpose), and
the generate/refine orchestration loop, dispatched to whichever archetype
the caller asked for.

The critical safety property: the LLM only ever fills a JSON schema (each
Archetype's spec_schema). It never writes YAML text — each archetype's own
render_agent_yaml/render_skill_files deterministically turns a validated
spec into files.

generate_spec() never raises for a bad LLM output — it always returns a
GeneratedSpec, falling all the way back to the archetype's own deterministic,
always-valid fallback spec if generation can't be made internally
consistent. Every agent this module produces is marked draft: true,
routable: false — validation here guarantees "won't crash the pipeline,"
not "the content is sensible," so nothing it writes is ever trusted
un-reviewed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from agent_platform.llm import OllamaAdapter, OllamaContentError, OllamaError

from .archetypes import get_archetype
# Re-exported for backward compatibility — these are the qualification
# archetype's own implementations (backend/archetypes/qualification.py),
# kept importable from here since that's where callers/tests knew them
# before the archetype system existed. Not aliases of a copy — the same
# function/schema objects, always in sync with archetypes/qualification.py.
from .archetypes.qualification import (  # noqa: F401
    AGENT_SPEC_SCHEMA, auto_repair, render_agent_yaml, render_skill_files, validate_spec,
)
from .archetypes.qualification import fallback_spec as fallback_blank_spec  # noqa: F401

# Both point at gemma4:12b — the model actually present on OLLAMA_HOST (granite4.1:3b and
# qwen2.5-coder:14b were only ever tested against a developer's own local Ollama, not the real
# configured server; qwen 503'd there entirely). gemma4:12b is also what every hand-built demo
# agent already uses, and live-tested clean: zero validate_spec errors on both the split decision
# and full rule generation, in ~20-30s per call. Kept as two separate constants/adapter builders
# (even though they're equal today) so either can be swapped independently later.
_BUILDER_MODEL = "gemma4:12b"
_DECOMPOSE_MODEL = "gemma4:12b"


# -- generation ----

# Admin-facing generation/refine calls, not end-user-facing — an admin
# clicking "Fix with AI" can tolerate a long wait far more than a live chat
# user waiting on a decision (see each agent.yaml's separate, much shorter
# timeout_seconds for that path). Set generously for production headroom;
# must stay >= the wrapper's own /ollama read timeout (currently 300s), or
# we time out client-side first and report our own generic error instead of
# whatever the wrapper/Ollama actually did.
_BUILDER_TIMEOUT_SECONDS = 600


def _build_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OllamaAdapter(host=host, model=_BUILDER_MODEL, timeout_seconds=_BUILDER_TIMEOUT_SECONDS, seed=7)


def _build_decompose_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OllamaAdapter(host=host, model=_DECOMPOSE_MODEL, timeout_seconds=_BUILDER_TIMEOUT_SECONDS, seed=7)


@dataclass(frozen=True)
class GeneratedSpec:
    spec: dict[str, Any]
    attempts: int
    used_fallback: bool


def generate_spec(*, purpose: str, agent_id: str, archetype_id: str = "qualification") -> GeneratedSpec:
    """One generation attempt, one cheap mechanical auto-repair if needed —
    no automatic LLM re-prompt. If it's still not valid after that, falls
    back to the archetype's safe placeholder spec rather than silently
    retrying the LLM again with no human visibility into what happened. A
    human reviewing a fallback-shaped draft is expected to either edit the
    YAML directly or use refine_spec() to describe what they actually want.
    """
    archetype = get_archetype(archetype_id)
    adapter = _build_adapter()
    errors: list[str] = []
    spec: dict[str, Any] = {}
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=archetype.build_system_prompt(),
            user_prompt=archetype.build_user_prompt(purpose),
            schema=archetype.spec_schema,
            temperature=0.2,
        )
        spec = parsed
        errors = archetype.validate(spec)
        if errors:
            spec = archetype.auto_repair(spec, errors)
            errors = archetype.validate(spec)
    except OllamaError:
        errors = errors or ["LLM call failed"]

    if errors:
        return GeneratedSpec(spec=archetype.fallback_spec(purpose, agent_id), attempts=1, used_fallback=True)
    return GeneratedSpec(spec=spec, attempts=1, used_fallback=False)


# -- multi-skill decomposition: does this purpose need more than one rule set? ----

_DECOMPOSE_SCHEMA = {
    "type": "object",
    "required": ["skills"],
    "properties": {
        "skills": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["skill_id", "description", "scope"],
                "properties": {
                    "skill_id": {"type": "string", "description": "short, snake_case, unique"},
                    "description": {
                        "type": "string",
                        "description": "One sentence a routing model will use to pick this skill over the others.",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Self-contained description of just this skill's own rules — written "
                                       "as if it were the entire purpose for this one skill.",
                    },
                },
            },
        },
    },
}


def _decompose_system_prompt() -> str:
    return (
        "You are deciding how many distinct rule sets (\"skills\") an agent needs to cover a "
        "plain-language description. Most requests should stay as ONE skill — only split into "
        "multiple when the description clearly describes genuinely distinct evaluation paths that "
        "don't share one gate/scoring/product structure (e.g. different rules for different "
        "applicant types, request types, or products). Output ONLY the structured fields requested "
        "— no YAML, no code, no commentary.\n\n"
        "Rules:\n"
        "- If in doubt, prefer ONE skill.\n"
        "- Each skill_id must be short, snake_case, and distinct from the others.\n"
        "- Each description is one sentence used later by a routing model to pick between skills at "
        "runtime — make it clearly distinguish this skill from the others.\n"
        "- Each scope must be a self-contained description of just that skill's own rules — write it "
        "as if it were the entire purpose for that one skill, since it will be used standalone, "
        "without the other skills' context, to generate that skill's rules."
    )


def decompose_purpose(purpose: str) -> list[dict[str, str]]:
    """Returns [{"skill_id", "description", "scope"}, ...], always length >= 1.
    On any LLM outage or malformed/empty response, returns a single-item
    list describing the whole purpose as one skill — decomposition failure
    must never block generation, only skip the split.
    """
    single_skill_fallback = [{"skill_id": "", "description": purpose.strip()[:200], "scope": purpose}]
    adapter = _build_decompose_adapter()
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=_decompose_system_prompt(),
            user_prompt=f"Description: {purpose}",
            schema=_DECOMPOSE_SCHEMA,
            temperature=0.0,
        )
    except OllamaError:
        return single_skill_fallback

    skills = parsed.get("skills") if isinstance(parsed, dict) else None
    if not isinstance(skills, list) or not skills:
        return single_skill_fallback

    cleaned = [
        item for item in skills
        if isinstance(item, dict) and (item.get("scope") or "").strip()
    ]
    return cleaned or single_skill_fallback


def _sanitize_skill_id(raw: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-z0-9_]", "_", (raw or "").strip().lower()).strip("_")
    return candidate or fallback


@dataclass(frozen=True)
class GeneratedSkill:
    skill_id: str
    description: str
    result: GeneratedSpec


@dataclass(frozen=True)
class MultiSkillGeneration:
    skills: list[GeneratedSkill]
    split: bool


def generate_agent_skills(
    *, purpose: str, agent_id: str, archetype_id: str = "qualification",
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> MultiSkillGeneration:
    """Decides (via decompose_purpose) whether this description needs one
    skill or several, then generates each one independently through the
    same proven generate_spec pipeline — never asks the model to author
    multiple full rule sets in a single call. One archetype applies to
    every skill this call produces — not mixed per-skill.

    on_progress, if given, is called with a small event dict before/after
    each LLM call so a caller (e.g. an SSE endpoint) can report real
    progress instead of guessing at timing.
    """
    def _report(event: dict[str, Any]) -> None:
        if on_progress is not None:
            on_progress(event)

    _report({"step": "decompose", "status": "start"})
    decomposed = decompose_purpose(purpose)
    _report({"step": "decompose", "status": "done", "count": len(decomposed)})

    if len(decomposed) <= 1:
        _report({"step": "generate_skill", "status": "start", "skill_id": agent_id, "index": 1, "total": 1})
        spec_result = generate_spec(purpose=purpose, agent_id=agent_id, archetype_id=archetype_id)
        description = purpose.strip()[:200] or agent_id
        _report({
            "step": "generate_skill", "status": "done", "skill_id": agent_id,
            "index": 1, "total": 1, "used_fallback": spec_result.used_fallback,
        })
        return MultiSkillGeneration(
            skills=[GeneratedSkill(skill_id=agent_id, description=description, result=spec_result)],
            split=False,
        )

    taken: set[str] = set()
    skills: list[GeneratedSkill] = []
    total = len(decomposed)
    for idx, item in enumerate(decomposed, start=1):
        skill_id = _sanitize_skill_id(item.get("skill_id", ""), f"{agent_id}_skill_{idx}")
        base_id = skill_id
        suffix = 2
        while skill_id in taken:
            skill_id = f"{base_id}_{suffix}"
            suffix += 1
        taken.add(skill_id)

        description = (item.get("description") or "").strip() or skill_id
        scope = (item.get("scope") or "").strip() or purpose
        _report({"step": "generate_skill", "status": "start", "skill_id": skill_id, "index": idx, "total": total})
        result = generate_spec(purpose=scope, agent_id=skill_id, archetype_id=archetype_id)
        skills.append(GeneratedSkill(skill_id=skill_id, description=description, result=result))
        _report({
            "step": "generate_skill", "status": "done", "skill_id": skill_id,
            "index": idx, "total": total, "used_fallback": result.used_fallback,
        })
    return MultiSkillGeneration(skills=skills, split=True)


# -- human-in-the-loop refinement: describe what's wrong, AI corrects the existing draft ----

@dataclass(frozen=True)
class RefineResult:
    ok: bool
    spec: dict[str, Any] | None
    errors: list[str]


def refine_spec(
    *, archetype_id: str, current_content: dict[str, str], feedback: str, skill_id: str, skill_description: str,
    target_section: str | None = None,
) -> RefineResult:
    """Unlike generate_spec, this never falls back to a placeholder on
    failure — silently discarding a human's in-progress draft (and their
    specific feedback) into a generic placeholder would be worse than just
    telling them it didn't work, so they can retry with clearer wording or
    edit the YAML directly instead.

    current_content is whatever archetype.read_refine_context(skill_dir)
    produced — its shape/keys are archetype-specific (qualification: rule-
    group name -> YAML text; conversational: filename -> text), interpreted
    only by that archetype's own build_refine_user_prompt.

    target_section, if given, is one of current_content's own keys —
    scopes the correction to that one part (e.g. just "factors") while the
    model still sees every other part as context, so a change that
    genuinely requires a matching change elsewhere (a new factor category
    needing a composite weight) can still happen. The caller (admin.py's
    per-file refine endpoint) only writes back whichever files actually
    came out different from what's on disk, so an untouched part is truly
    left alone rather than just asked nicely to be.
    """
    archetype = get_archetype(archetype_id)
    adapter = _build_adapter()
    user_prompt = archetype.build_refine_user_prompt(
        current_content, feedback, skill_id=skill_id, skill_description=skill_description,
        target_section=target_section,
    )
    system_prompt = archetype.build_refine_system_prompt()
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=system_prompt, user_prompt=user_prompt, schema=archetype.spec_schema, temperature=0.2,
        )
    except OllamaContentError:
        # The HTTP call succeeded but the model returned empty/non-JSON
        # content — seen in practice when a "thinking"-capable model spends
        # its whole output budget on reasoning and never emits a final
        # answer. Not a transport failure (already retried inside
        # _post_chat), so it needs its own one-shot retry here rather than
        # being treated the same as a genuine, already-exhausted outage.
        try:
            parsed, _meta = adapter.generate_structured(
                system_prompt=system_prompt, user_prompt=user_prompt, schema=archetype.spec_schema, temperature=0.2,
            )
        except OllamaError as exc:
            return RefineResult(ok=False, spec=None, errors=[f"LLM call failed: {exc}"])
    except OllamaError as exc:
        return RefineResult(ok=False, spec=None, errors=[f"LLM call failed: {exc}"])

    errors = archetype.validate(parsed)
    if errors:
        parsed = archetype.auto_repair(parsed, errors)
        errors = archetype.validate(parsed)

    if errors and archetype.repair_with_context is not None:
        parsed = archetype.repair_with_context(parsed, current_content)
        errors = archetype.validate(parsed)

    if errors:
        return RefineResult(ok=False, spec=None, errors=errors)
    return RefineResult(ok=True, spec=parsed, errors=[])
