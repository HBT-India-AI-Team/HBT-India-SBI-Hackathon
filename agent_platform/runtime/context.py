"""RunContext: the single mutable object a pipeline execution passes through
every stage. Stages read from it and write to it; nothing else is shared
state.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class StageResult:
    stage: str
    status: str  # "ok" | "error" | "skipped"
    started_at: str
    duration_ms: float
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    run_id: str
    agent_id: str
    agent_version: str
    correlation_id: str
    raw_input: dict[str, Any]

    evidence: dict[str, Any] = field(default_factory=dict)
    active_skill_ids: list[str] = field(default_factory=list)
    active_skill_reasoning: str | None = None
    rule_results: dict[str, Any] = field(default_factory=dict)
    # Per-applicable-skill {outcome, reason, composite_score, thresholds}, keyed by skill_id —
    # computed once in evaluate_rules, reused by reason_llm (to pick which skill's output_contract
    # constrains narration) and decide (to build the final decision + skill_breakdown).
    skill_decisions: dict[str, Any] = field(default_factory=dict)
    governing_skill_id: str | None = None
    citation_keys: dict[str, Any] = field(default_factory=dict)
    llm_output: dict[str, Any] | None = None
    validated_output: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    hitl: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None

    stage_results: list[StageResult] = field(default_factory=list)

    # Scratch slot a stage can fill to attach evidence about what it actually
    # did — model name, token counts, the model's own reasoning trace, tool
    # calls. run_pipeline drains this into the StageResult it builds and
    # clears it, so a stage never has to know how StageResults are made and
    # one stage's detail can't leak into the next. Stages that set nothing
    # produce an empty detail, which is what every non-LLM stage does.
    pending_stage_detail: dict[str, Any] = field(default_factory=dict)

    error: dict[str, Any] | None = None
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None

    @classmethod
    def start(cls, agent_id: str, agent_version: str, raw_input: dict[str, Any],
              correlation_id: str | None = None) -> "RunContext":
        return cls(
            run_id=new_id("run"),
            agent_id=agent_id,
            agent_version=agent_version,
            correlation_id=correlation_id or new_id("corr"),
            raw_input=raw_input,
        )

    def input_summary(self) -> dict[str, Any]:
        """Log-safe view of the input. A plain shallow copy: generic over any
        agent's input shape, not just this demo's lead-qualification fields.
        """
        return dict(self.raw_input or {})
