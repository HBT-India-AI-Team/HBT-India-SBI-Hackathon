"""load_skills: resolves which of an agent's declared skills should be
active for this run, before evaluate_rules/reason_llm read
bundle.active_skills(ctx). Replaces the old two-mechanism split
(select_skill picking exactly one deterministic skill, plus a separate
tool-calling loop inside reason_llm for guidance-only dynamic skills) with
one mechanism: any number of skills can load, whether or not they carry
rules.

Mirrors agent_platform/workflows/agent_router.py's agent-selection pattern
one level down (skill instead of agent) for the explicit-override and
single-candidate fast paths. For the "let the AI decide" path, reuses
OllamaAdapter.run_tool_loop — the same bounded (max_turns=4), tool-calling
mechanism the old dynamic-skill loader already used, just no longer
restricted to guidance-only skills. Never resolves to zero active skills
when there's more than one candidate: a rule-bearing skill needs
*something* to drive evaluate_rules/decide, so "the AI didn't call
load_skill for anything" falls back to the first declared skill rather
than leaving evidence unevaluated.
"""
from __future__ import annotations

import os

from agent_platform.llm import OllamaAdapter, OllamaError
from agent_platform.runtime.pipeline import register_stage


def _build_adapter(bundle) -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    llm_config = bundle.definition.llm
    return OllamaAdapter(
        host=host,
        model=llm_config.model,
        timeout_seconds=llm_config.timeout_seconds,
        seed=llm_config.seed,
    )


def _load_skill_tool(candidate_ids: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Mark a skill as relevant and load it for this request.",
            "parameters": {
                "type": "object",
                "required": ["skill_id"],
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "enum": candidate_ids,
                        "description": "Which skill to load — see the catalog above.",
                    },
                },
            },
        },
    }


def _build_system_prompt(bundle, candidate_ids: list[str]) -> str:
    lines = [f"- {sid}: {bundle.skills[sid].description}" for sid in candidate_ids]
    return (
        "Decide which of the skills below are relevant to this request. Call load_skill once "
        "for each relevant skill — call it more than once if more than one applies. Skills you "
        "don't call are not used for this request.\n\nAvailable skills:\n" + "\n".join(lines)
    )


@register_stage("load_skills")
def load_skills(ctx, bundle, logger) -> None:
    candidate_ids = list(bundle.skills.keys())

    if len(candidate_ids) <= 1:
        ctx.active_skill_ids = candidate_ids
        return

    explicit = ctx.raw_input.get("skill_ids")
    if explicit is None and ctx.raw_input.get("skill_id") is not None:
        explicit = [ctx.raw_input["skill_id"]]
    if explicit is not None:
        requested = explicit if isinstance(explicit, list) else [explicit]
        resolved = [sid for sid in requested if sid in candidate_ids]
        ctx.active_skill_ids = resolved or candidate_ids[:1]
        ctx.active_skill_reasoning = "explicit override — caller specified skill_id(s)"
        logger.event(ctx, "skills_loaded", active_skill_ids=ctx.active_skill_ids,
                     reasoning=ctx.active_skill_reasoning)
        return

    def resolve_tool(name: str, arguments: dict) -> str:
        skill_id = arguments.get("skill_id")
        if skill_id not in candidate_ids:
            return f"Unknown skill_id '{skill_id}'"
        return f"Noted — '{skill_id}' will be used for this request."

    adapter = _build_adapter(bundle)
    try:
        calls = adapter.run_tool_loop(
            system_prompt=_build_system_prompt(bundle, candidate_ids),
            user_prompt=f"Request: {ctx.raw_input}",
            tools=[_load_skill_tool(candidate_ids)],
            resolve_tool=resolve_tool,
        )
    except OllamaError as exc:
        ctx.active_skill_ids = candidate_ids[:1]
        ctx.active_skill_reasoning = f"skill-loading LLM call failed: {exc}"
        logger.event(ctx, "skills_load_fallback", level="WARNING", reasoning=ctx.active_skill_reasoning)
        return

    loaded_ids: list[str] = []
    for call in calls:
        skill_id = call["arguments"].get("skill_id")
        if skill_id in candidate_ids and skill_id not in loaded_ids:
            loaded_ids.append(skill_id)

    ctx.active_skill_ids = loaded_ids or candidate_ids[:1]
    ctx.active_skill_reasoning = (
        f"AI loaded: {', '.join(loaded_ids)}" if loaded_ids
        else "AI loaded none — falling back to the first declared skill"
    )
    logger.event(ctx, "skills_loaded", active_skill_ids=ctx.active_skill_ids,
                 reasoning=ctx.active_skill_reasoning)
