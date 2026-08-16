"""Structured logging shared by every agent.

Every event is one JSON line appended to logs/agent-runs.jsonl, always
carrying run_id / agent_id / correlation_id / event so runs can be filtered
and joined later. A short human-readable line also goes to stdout so a demo
run is legible without tailing the JSONL file.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "agent-runs.jsonl"


def _now_iso() -> str:
    # Local time (with its UTC offset), not UTC -- logs read far more often
    # by a person on this machine than joined against another timezone.
    return datetime.now().astimezone().isoformat()


class AgentLogger:
    def __init__(self, log_path: Path | None = None, echo_to_console: bool = True):
        self.log_path = log_path or DEFAULT_LOG_PATH
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.echo_to_console = echo_to_console

    def _emit(self, event: str, ctx, level: str = "INFO", **fields: Any) -> None:
        record = {
            "timestamp": _now_iso(),
            "level": level,
            "event": event,
            "run_id": getattr(ctx, "run_id", None),
            "agent_id": getattr(ctx, "agent_id", None),
            "correlation_id": getattr(ctx, "correlation_id", None),
            **fields,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        if self.echo_to_console:
            print(f"[{record['timestamp']}] {level:<5} {event:<18} "
                  f"run={record['run_id']} {_console_suffix(fields)}")

    # -- run lifecycle -----------------------------------------------------
    def run_start(self, ctx) -> None:
        self._emit("run_start", ctx, input_summary=ctx.input_summary())

    def run_end(self, ctx) -> None:
        outcome = (ctx.decision or {}).get("outcome") if ctx.decision else None
        self._emit(
            "run_end", ctx,
            outcome=outcome,
            had_error=ctx.error is not None,
            hitl_triggered=(ctx.hitl or {}).get("triggered", False),
        )

    # -- stage lifecycle -----------------------------------------------------
    def stage_start(self, ctx, stage_name: str) -> None:
        self._emit("stage_start", ctx, stage=stage_name)

    def stage_end(self, ctx, stage_result) -> None:
        self._emit(
            "stage_end", ctx,
            stage=stage_result.stage,
            duration_ms=stage_result.duration_ms,
            summary=stage_result.summary,
        )

    def stage_error(self, ctx, stage_result, exc: Exception) -> None:
        self._emit(
            "stage_error", ctx, level="ERROR",
            stage=stage_result.stage,
            duration_ms=stage_result.duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    # -- llm calls -----------------------------------------------------
    def llm_call(self, ctx, *, model: str, duration_ms: float,
                 prompt_tokens: int | None, completion_tokens: int | None,
                 attempt: int, ok: bool) -> None:
        self._emit(
            "llm_call", ctx,
            level="INFO" if ok else "WARNING",
            model=model,
            duration_ms=round(duration_ms, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            attempt=attempt,
            ok=ok,
        )

    # -- generic -----------------------------------------------------
    def warning(self, ctx, message: str, **detail: Any) -> None:
        self._emit("warning", ctx, level="WARNING", message=message, **detail)

    def info(self, ctx, message: str, **detail: Any) -> None:
        self._emit("info", ctx, level="INFO", message=message, **detail)

    def event(self, ctx, event_name: str, level: str = "INFO", **fields: Any) -> None:
        """Emit an arbitrarily-named structured event (e.g. `lead_selected`,
        `qualification_branch_selected`, `proposal_generated`). Any stage or
        workflow can call this for observability points more specific than
        the generic stage_start/stage_end lifecycle, without the logger
        needing to know what any of them mean.
        """
        self._emit(event_name, ctx, level=level, **fields)


def _console_suffix(fields: dict[str, Any]) -> str:
    parts = []
    for key in ("stage", "outcome", "message", "summary", "model"):
        if key in fields and fields[key] is not None:
            parts.append(f"{key}={fields[key]}")
    return " ".join(parts)


_default_logger: AgentLogger | None = None


def get_logger() -> AgentLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = AgentLogger()
    return _default_logger
