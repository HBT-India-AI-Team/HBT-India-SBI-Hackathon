"""Persists exactly what the demo needs to inspect a run afterward: run id,
input summary, per-stage results, final result, explanation, and error
details (if any) — one JSON file per run under runs/.

This is intentionally not a checkpoint/resume system: a run is written once,
after it finishes (successfully or not). Full workflow replay and
checkpoint-based resumability are future-roadmap items, not implemented
here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def save_run(ctx) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": ctx.run_id,
        "agent_id": ctx.agent_id,
        "agent_version": ctx.agent_version,
        "correlation_id": ctx.correlation_id,
        "started_at": ctx.started_at,
        "finished_at": ctx.finished_at,
        "input_summary": ctx.input_summary(),
        "stage_results": [
            {
                "stage": sr.stage,
                "status": sr.status,
                "started_at": sr.started_at,
                "duration_ms": sr.duration_ms,
                "summary": sr.summary,
            }
            for sr in ctx.stage_results
        ],
        "final_result": ctx.decision,
        "explanation": ctx.explanation,
        "error": ctx.error,
    }
    path = _run_path(ctx.run_id)
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


def get_run(run_id: str) -> dict[str, Any] | None:
    path = _run_path(run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    if not RUNS_DIR.exists():
        return []
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    summaries = []
    for path in files[:limit]:
        record = json.loads(path.read_text(encoding="utf-8"))
        summaries.append({
            "run_id": record["run_id"],
            "agent_id": record["agent_id"],
            "started_at": record["started_at"],
            "outcome": (record.get("final_result") or {}).get("outcome"),
            "had_error": record.get("error") is not None,
        })
    return summaries
