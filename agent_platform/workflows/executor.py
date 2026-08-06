"""The smallest generalization of run_pipeline's per-stage bookkeeping
(agent_platform/runtime/pipeline.py) that supports a branching, cross-agent
function instead of a flat list of stages against one bundle. A workflow
isn't shoehorned through STAGE_REGISTRY/run_pipeline because it genuinely
doesn't fit that model — it calls invoke_agent() several times and branches
on the results, which run_pipeline has no way to express. This is not a new
engine: it's the same timing/logging/StageResult pattern, reusable for
heterogeneous nodes.

The context object returned is a plain RunContext (agent_platform.runtime.
context), so save_run/get_run/the /runs/* endpoints work on a workflow run
with zero new persistence code.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

from agent_platform.runtime.context import RunContext, StageResult

T = TypeVar("T")


def new_workflow_context(workflow_id: str, workflow_version: str, raw_input: dict,
                          correlation_id: str | None = None) -> RunContext:
    return RunContext.start(
        agent_id=workflow_id,
        agent_version=workflow_version,
        raw_input=raw_input,
        correlation_id=correlation_id,
    )


def run_node(ctx: RunContext, logger, node_name: str, fn: Callable[[], T]) -> T:
    """Runs one workflow node, with the same timing/logging/StageResult
    bookkeeping run_pipeline gives every agent stage. Re-raises on failure
    (unlike run_pipeline, which swallows and breaks) so the calling
    workflow function decides how to handle it — a workflow has branches
    that need to react to a failure, not just stop.
    """
    logger.stage_start(ctx, node_name)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.perf_counter()
    try:
        result = fn()
        duration_ms = (time.perf_counter() - t0) * 1000
        stage_result = StageResult(
            stage=node_name, status="ok", started_at=started_at,
            duration_ms=round(duration_ms, 2), summary=f"{node_name} completed",
        )
        ctx.stage_results.append(stage_result)
        logger.stage_end(ctx, stage_result)
        return result
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        ctx.error = {"stage": node_name, "type": type(exc).__name__, "message": str(exc)}
        stage_result = StageResult(
            stage=node_name, status="error", started_at=started_at,
            duration_ms=round(duration_ms, 2), summary=str(exc),
        )
        ctx.stage_results.append(stage_result)
        logger.stage_error(ctx, stage_result, exc)
        raise
