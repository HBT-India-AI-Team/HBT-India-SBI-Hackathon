"""The shared pipeline executor. Every agent declares its lifecycle as a
list of stage names in agent.yaml (e.g. ["load_input", "gather_evidence",
"evaluate_rules", "reason_llm", "validate_output", "decide", "hitl_gate",
"explain"]). This module resolves each name against a registry and runs it
against a RunContext. Stages are plain functions with signature
(ctx, bundle, logger) -> None that mutate ctx in place; this file has no
knowledge of what any particular stage does.

A new agent with a different pipeline (fewer stages, different order, a
stage this agent doesn't use) runs through the exact same executor.
"""
from __future__ import annotations

import time
from typing import Callable

from .context import RunContext, StageResult

StageFn = Callable[[RunContext, object, object], None]

STAGE_REGISTRY: dict[str, StageFn] = {}


def register_stage(name: str) -> Callable[[StageFn], StageFn]:
    def _decorator(fn: StageFn) -> StageFn:
        STAGE_REGISTRY[name] = fn
        return fn

    return _decorator


def _drain_stage_detail(ctx: RunContext) -> dict:
    """Takes whatever the stage that just ran attached to
    ctx.pending_stage_detail and clears the slot, so the next stage starts
    empty and detail is always attributed to the stage that produced it.
    """
    detail = ctx.pending_stage_detail
    ctx.pending_stage_detail = {}
    return detail


def run_pipeline(bundle, ctx: RunContext, logger) -> RunContext:
    """Runs each declared stage in order. Stops at the first stage that
    raises, but always leaves ctx in a state the caller can persist and
    explain from stage_results/error.
    """
    for stage_name in bundle.definition.pipeline:
        stage_fn = STAGE_REGISTRY.get(stage_name)
        if stage_fn is None:
            ctx.error = {
                "stage": stage_name,
                "type": "UnknownStageError",
                "message": f"No stage registered under '{stage_name}'",
            }
            ctx.stage_results.append(
                StageResult(
                    stage=stage_name,
                    status="error",
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    duration_ms=0.0,
                    summary=ctx.error["message"],
                )
            )
            break

        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        t0 = time.perf_counter()
        logger.stage_start(ctx, stage_name)
        try:
            stage_fn(ctx, bundle, logger)
            duration_ms = (time.perf_counter() - t0) * 1000
            result = StageResult(
                stage=stage_name,
                status="ok",
                started_at=started_at,
                duration_ms=round(duration_ms, 2),
                summary=f"{stage_name} completed",
                detail=_drain_stage_detail(ctx),
            )
            ctx.stage_results.append(result)
            logger.stage_end(ctx, result)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # stage failure must be captured, logged and persisted, never
            # allowed to crash the run silently.
            duration_ms = (time.perf_counter() - t0) * 1000
            ctx.error = {
                "stage": stage_name,
                "type": type(exc).__name__,
                "message": str(exc),
            }
            result = StageResult(
                stage=stage_name,
                status="error",
                started_at=started_at,
                duration_ms=round(duration_ms, 2),
                summary=str(exc),
                # Drained on the failure path too: a stage that got a model
                # response and then failed downstream still has reasoning
                # worth showing, and it's exactly the case you most want to
                # inspect.
                detail=_drain_stage_detail(ctx),
            )
            ctx.stage_results.append(result)
            logger.stage_error(ctx, result, exc)
            break

    return ctx
