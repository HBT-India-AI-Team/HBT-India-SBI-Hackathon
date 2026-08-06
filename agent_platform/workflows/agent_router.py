"""START -> build_catalog -> select_agent (LLM, or explicit override) ->
{NEEDS_CLARIFICATION: stop | invoke chosen agent} -> END

Lets a caller hit one generic entry point without knowing our agent_id
catalog: either give it a raw request and an LLM decides which registered
agent should handle it, or pass `agent_id` directly to skip the LLM and
invoke that agent explicitly. Either way the result is persisted and
explained through the exact same RunContext/save_run machinery as any
other agent or workflow run.

Like commercial_leadgen_demo.py, this file only knows agents by their
agent_id and the generic RunContext shape (.decision, .error) — it has no
knowledge of what any individual agent actually does.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from agent_platform.composition import list_agents, load_agent
from agent_platform.composition.models import LLMConfig
from agent_platform.llm import OllamaAdapter, OllamaError
from agent_platform.observability import get_logger
from agent_platform.runtime.executor import invoke_agent
from agent_platform.state import save_run

from .executor import new_workflow_context, run_node
from .registry import register_workflow

WORKFLOW_ID = "agent_router"
WORKFLOW_VERSION = "1.0.0"

CONFIDENCE_THRESHOLD = 0.6
_ROUTER_LLM = LLMConfig(model="gemma4:12b", temperature=0.0, seed=7, timeout_seconds=60)

_ROUTING_SCHEMA_TEMPLATE = {
    "type": "object",
    "required": ["agent_id", "confidence", "reasoning"],
    "properties": {
        "agent_id": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_catalog() -> list[dict]:
    catalog = []
    for agent_id in list_agents():
        bundle = load_agent(agent_id)
        if bundle.definition.routable:
            catalog.append({"agent_id": agent_id, "purpose": bundle.definition.purpose.strip()})
    return catalog


def _build_router_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OllamaAdapter(
        host=host,
        model=_ROUTER_LLM.model,
        timeout_seconds=_ROUTER_LLM.timeout_seconds,
        seed=_ROUTER_LLM.seed,
    )


def _build_routing_schema(catalog: list[dict]) -> dict:
    schema = {**_ROUTING_SCHEMA_TEMPLATE, "properties": dict(_ROUTING_SCHEMA_TEMPLATE["properties"])}
    schema["properties"] = {
        **schema["properties"],
        "agent_id": {"type": "string", "enum": [c["agent_id"] for c in catalog]},
    }
    return schema


def _build_system_prompt(catalog: list[dict]) -> str:
    agent_lines = "\n".join(f"- {c['agent_id']}: {c['purpose']}" for c in catalog)
    return (
        "You are a routing assistant for an agent platform. Given a caller's "
        "request, choose exactly one agent_id from the list below best suited "
        "to handle it. If you are not confident any agent is a good fit, "
        "still pick your best guess but reflect that in a low confidence "
        "score.\n\nAvailable agents:\n" + agent_lines
    )


def _select_agent_via_llm(raw_input: dict, catalog: list[dict]) -> dict:
    adapter = _build_router_adapter()
    parsed, _meta = adapter.generate_structured(
        system_prompt=_build_system_prompt(catalog),
        user_prompt=f"Request: {raw_input}",
        schema=_build_routing_schema(catalog),
        temperature=_ROUTER_LLM.temperature,
    )
    return parsed


@register_workflow(WORKFLOW_ID)
def run(raw_input: dict, correlation_id: str | None = None) -> dict:
    ctx = new_workflow_context(WORKFLOW_ID, WORKFLOW_VERSION, raw_input, correlation_id)
    logger = get_logger()
    logger.event(ctx, "workflow_started", workflow_id=WORKFLOW_ID, workflow_version=WORKFLOW_VERSION)

    response: dict = {
        "workflow_id": WORKFLOW_ID,
        "run_id": ctx.run_id,
        "status": "COMPLETED",
        "chosen_agent_id": None,
        "routing_confidence": None,
        "routing_reasoning": None,
        "agent_run_id": None,
        "decision": None,
        "explanation": None,
    }

    try:
        catalog = run_node(ctx, logger, "build_catalog", _build_catalog)
        if not catalog:
            raise ValueError("no routable agents are registered")
        catalog_ids = {c["agent_id"] for c in catalog}

        selection: dict | None = None
        clarification_reason: str | None = None
        explicit_agent_id = raw_input.get("agent_id")

        if explicit_agent_id is not None:
            if explicit_agent_id in catalog_ids:
                selection = {
                    "agent_id": explicit_agent_id,
                    "confidence": 1.0,
                    "reasoning": "explicit override — caller specified agent_id directly",
                }
            else:
                clarification_reason = f"'{explicit_agent_id}' is not a known routable agent_id"
                logger.event(ctx, "agent_route_rejected", level="WARNING",
                             attempted_agent_id=explicit_agent_id)
        else:
            try:
                candidate = run_node(ctx, logger, "select_agent",
                                      lambda: _select_agent_via_llm(raw_input, catalog))
            except OllamaError as exc:
                candidate = None
                clarification_reason = f"routing LLM call failed: {exc}"
                logger.warning(ctx, clarification_reason)

            if candidate is not None:
                logger.event(ctx, "agent_route_selected",
                             chosen_agent_id=candidate.get("agent_id"),
                             confidence=candidate.get("confidence"),
                             reasoning=candidate.get("reasoning"))
                if candidate.get("agent_id") not in catalog_ids:
                    clarification_reason = "LLM selected an agent_id outside the known catalog"
                elif (candidate.get("confidence") or 0) < CONFIDENCE_THRESHOLD:
                    clarification_reason = (
                        f"routing confidence {candidate.get('confidence')} is below "
                        f"threshold {CONFIDENCE_THRESHOLD}: {candidate.get('reasoning')}"
                    )
                else:
                    selection = candidate

        if selection is None:
            response["status"] = "NEEDS_CLARIFICATION"
            response["routing_reasoning"] = clarification_reason
        else:
            chosen_id = selection["agent_id"]
            raw_input_for_agent = {k: v for k, v in raw_input.items() if k != "agent_id"}

            logger.event(ctx, "agent_invocation_started", chosen_agent_id=chosen_id)
            agent_ctx = run_node(
                ctx, logger, f"invoke_{chosen_id}",
                lambda: invoke_agent(chosen_id, raw_input_for_agent, correlation_id=ctx.correlation_id),
            )

            response["chosen_agent_id"] = chosen_id
            response["routing_confidence"] = selection.get("confidence")
            response["routing_reasoning"] = selection.get("reasoning")
            response["agent_run_id"] = agent_ctx.run_id

            if agent_ctx.error:
                response["status"] = "AGENT_FAILED"
                response["error"] = agent_ctx.error
                logger.event(ctx, "agent_invocation_failed", level="ERROR",
                             chosen_agent_id=chosen_id, error=agent_ctx.error)
            else:
                response["decision"] = agent_ctx.decision
                response["explanation"] = agent_ctx.explanation
                logger.event(ctx, "agent_invocation_completed", chosen_agent_id=chosen_id)

    except Exception as exc:  # noqa: BLE001 - a router failure must be
        # captured and persisted, never allowed to crash the caller.
        if ctx.error is None:
            ctx.error = {"stage": "agent_router", "type": type(exc).__name__, "message": str(exc)}
        response["status"] = "FAILED"
        response["error"] = ctx.error
        logger.event(ctx, "workflow_failed", level="ERROR",
                     stage=ctx.error["stage"], error=ctx.error["message"])

    finally:
        ctx.decision = {"status": response["status"], "chosen_agent_id": response.get("chosen_agent_id")}
        ctx.explanation = response
        ctx.finished_at = _now_iso()
        save_run(ctx)

    logger.event(ctx, "workflow_completed", status=response["status"])
    logger.run_end(ctx)

    return response
