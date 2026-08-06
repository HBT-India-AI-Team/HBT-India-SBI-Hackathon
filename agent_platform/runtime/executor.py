"""The single entry point both the CLI and the FastAPI backend call:
invoke_agent(agent_id, raw_input) -> RunContext. Loads the agent, runs its
declared pipeline, always persists a run record and an explanation — even
on failure — and returns the finished context.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_platform import stages  # noqa: F401  (import registers pipeline stages)
from agent_platform.composition import load_agent
from agent_platform.explainability import decision_record
from agent_platform.observability import get_logger
from agent_platform.state import save_run

from .context import RunContext
from .pipeline import run_pipeline


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def invoke_agent(agent_id: str, raw_input: dict, correlation_id: str | None = None) -> RunContext:
    bundle = load_agent(agent_id)
    ctx = RunContext.start(
        agent_id=bundle.agent_id,
        agent_version=bundle.definition.version,
        raw_input=raw_input,
        correlation_id=correlation_id,
    )
    logger = get_logger()
    logger.run_start(ctx)

    try:
        run_pipeline(bundle, ctx, logger)
    finally:
        if ctx.explanation is None:
            ctx.explanation = decision_record.build(ctx, bundle)
        ctx.finished_at = _now_iso()
        logger.run_end(ctx)
        save_run(ctx)

    return ctx
