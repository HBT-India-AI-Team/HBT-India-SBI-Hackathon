"""START -> discover_leads -> qualify_lead -> qualification_router ->
{NOT_QUALIFIED: stop | NEEDS_HUMAN_REVIEW: stop | QUALIFIED/CONDITIONALLY_QUALIFIED: generate_proposal} -> END

Composes agents purely by their registered agent_id via invoke_agent() —
this file has no knowledge of any agent's internals, only their run
contexts' generic shape (.decision, .rule_results, .evidence, .error).
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_platform.observability import get_logger
from agent_platform.runtime.executor import invoke_agent
from agent_platform.state import save_run

from .executor import new_workflow_context, run_node
from .registry import register_workflow

WORKFLOW_ID = "commercial_leadgen_demo"
WORKFLOW_VERSION = "1.0.0"

_PROCEED_OUTCOMES = {"QUALIFIED", "CONDITIONALLY_QUALIFIED"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discovery_summary(ctx) -> dict:
    return {
        "selected_lead_id": (ctx.decision or {}).get("selected_lead_id"),
        "ranked_leads": ctx.rule_results.get("ranked_leads", []),
        "selection_reason": (ctx.validated_output or {}).get("selection_reason"),
        "confidence": (ctx.validated_output or {}).get("confidence"),
        "run_id": ctx.run_id,
    }


def _governing_rule_results(ctx) -> dict:
    """ctx.rule_results is keyed by skill_id (an agent can have more than
    one active skill); this pulls out whichever skill's results actually
    governed ctx.decision, matching the flat single-skill shape workflow
    code historically expected.
    """
    return (ctx.rule_results or {}).get(ctx.governing_skill_id, {})


def _qualification_summary(ctx) -> dict:
    decision = ctx.decision or {}
    rule_results = _governing_rule_results(ctx)
    return {
        "qualification_status": decision.get("outcome"),
        "reason": decision.get("reason"),
        "financial_health_score": rule_results.get("scores", {}).get("financial_health", {}).get("value"),
        "composite_score": decision.get("composite_score"),
        "hitl": ctx.hitl,
        "recommended_products": rule_results.get("products", []),
        "run_id": ctx.run_id,
    }


def _proposal_summary(ctx) -> dict:
    decision = ctx.decision or {}
    rationale = ctx.validated_output or {}
    products = ctx.rule_results.get("selected_products", [])
    return {
        "proposal_status": decision.get("proposal_status"),
        "recommended_products": products,
        "next_best_action": rationale.get("next_best_action"),
        # required_documents is deterministic (from the matched top product's
        # catalog entry), never LLM-sourced.
        "required_documents": products[0].get("required_documents", []) if products else [],
        "customer_proposal": rationale.get("customer_proposal"),
        "confidence": rationale.get("confidence"),
        "run_id": ctx.run_id,
    }


@register_workflow(WORKFLOW_ID)
def run(raw_input: dict, correlation_id: str | None = None) -> dict:
    ctx = new_workflow_context(WORKFLOW_ID, WORKFLOW_VERSION, raw_input, correlation_id)
    logger = get_logger()
    logger.event(ctx, "workflow_started", workflow_id=WORKFLOW_ID, workflow_version=WORKFLOW_VERSION)

    response: dict = {
        "workflow_id": WORKFLOW_ID,
        "run_id": ctx.run_id,
        "status": "COMPLETED",
    }

    try:
        run_node(ctx, logger, "validate_request", lambda: _validate_request(raw_input))

        logger.event(ctx, "lead_discovery_started")
        discovery_ctx = run_node(
            ctx, logger, "discover_leads",
            lambda: invoke_agent("lead_discovery", raw_input, correlation_id=ctx.correlation_id),
        )
        response["discovery"] = _discovery_summary(discovery_ctx)

        selected_lead_id = run_node(
            ctx, logger, "select_lead",
            lambda: _require_selected_lead(discovery_ctx),
        )
        logger.event(ctx, "lead_selected", selected_lead_id=selected_lead_id)

        logger.event(ctx, "lead_qualification_started", lead_id=selected_lead_id)
        qualification_ctx = run_node(
            ctx, logger, "qualify_lead",
            lambda: invoke_agent("lead_qualification", {"lead_id": selected_lead_id},
                                  correlation_id=ctx.correlation_id),
        )
        response["qualification"] = _qualification_summary(qualification_ctx)
        logger.event(ctx, "qualification_completed",
                     outcome=(qualification_ctx.decision or {}).get("outcome"))

        outcome = (qualification_ctx.decision or {}).get("outcome")
        branch = run_node(ctx, logger, "qualification_router", lambda: _route(outcome))
        logger.event(ctx, "qualification_branch_selected", branch=branch, outcome=outcome)

        if branch == "PROCEED":
            proposal_input = _build_proposal_input(discovery_ctx, qualification_ctx)
            logger.event(ctx, "proposal_generation_started")
            proposal_ctx = run_node(
                ctx, logger, "generate_proposal",
                lambda: invoke_agent("proposal", proposal_input, correlation_id=ctx.correlation_id),
            )
            response["proposal"] = _proposal_summary(proposal_ctx)
            logger.event(ctx, "proposal_generated",
                         status=(proposal_ctx.decision or {}).get("proposal_status"))
        else:
            response["proposal"] = None
            response["skip_reason"] = branch

        response["explainability"] = {
            "lead_selection_reason": response["discovery"].get("selection_reason"),
            "qualification_rule_summary": [
                {"id": g["id"], "passed": g["passed"]}
                for g in _governing_rule_results(qualification_ctx).get("gates", {}).get("gates", [])
            ],
            "product_selection_reason": (
                (response.get("proposal") or {}).get("customer_proposal")
            ),
        }

        run_node(ctx, logger, "finalize_response", lambda: None)
        ctx.decision = {"status": "COMPLETED", "branch": branch}

    except Exception as exc:  # noqa: BLE001 - a workflow failure must be
        # captured and persisted, never allowed to crash the caller.
        if ctx.error is None:
            ctx.error = {"stage": "workflow", "type": type(exc).__name__, "message": str(exc)}
        response["status"] = "FAILED"
        response["error"] = ctx.error
        logger.event(ctx, "workflow_failed", level="ERROR",
                     stage=ctx.error["stage"], error=ctx.error["message"])

    finally:
        ctx.explanation = response
        ctx.finished_at = _now_iso()
        save_run(ctx)

    if response["status"] != "FAILED":
        logger.event(ctx, "workflow_completed", status=response["status"])
    logger.run_end(ctx)

    return response


def _validate_request(raw_input: dict) -> None:
    # Discovery's own load_input stage validates its own required fields
    # against agents/lead_discovery/agent.yaml's input_schema; this is a
    # workflow-level presence check on the overall request shape.
    if not isinstance(raw_input, dict):
        raise ValueError("workflow input must be a JSON object")


def _require_selected_lead(discovery_ctx) -> str:
    selected_lead_id = (discovery_ctx.decision or {}).get("selected_lead_id")
    if not selected_lead_id:
        raise ValueError("lead discovery found no matching candidates")
    return selected_lead_id


def _route(outcome: str | None) -> str:
    if outcome in _PROCEED_OUTCOMES:
        return "PROCEED"
    if outcome == "NEEDS_HUMAN_REVIEW":
        return "NEEDS_HUMAN_REVIEW"
    return "NOT_QUALIFIED"


def _build_proposal_input(discovery_ctx, qualification_ctx) -> dict:
    lead = dict(qualification_ctx.evidence.get("lead", {}))
    lead["financials"] = qualification_ctx.evidence.get("financials", {})
    return {
        "lead": lead,
        "qualification_result": qualification_ctx.decision,
        "eligible_products": [p["id"] for p in _governing_rule_results(qualification_ctx).get("products", [])],
    }
