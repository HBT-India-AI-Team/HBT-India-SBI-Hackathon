"""PLACEHOLDER — the real production trigger this prototype doesn't have yet.

The intended real workflow: fin_health runs (on a real Excel/DB-sourced
company record), its result gets persisted as a row, and THAT WRITE is
what should fire proposal_generator automatically — not a person clicking
a button in the admin Playground.

No real database or change-listener exists in this prototype, so today
that handoff is a manual "Send to Proposal" click in the Playground
(frontend/src/components/Playground.tsx), which carries the just-computed
fin_health result forward as proposal_generator's trigger context by hand.

This module is the real shape that manual step should eventually replace:
`on_fin_health_result_persisted` is genuinely callable right now (it does
call invoke_agent for real) — only the *trigger* is fake. Wiring a real
one later means calling this function from wherever the real write
actually happens, in any of the usual ways:
  - Postgres: a LISTEN/NOTIFY trigger on the fin_health_results table,
    with a small always-running listener process calling this on notify.
  - A polling job checking for new rows since it last ran.
  - An outbox/webhook the write path calls synchronously after commit.
None of that infra exists here — swap this docstring's approach in
without changing `on_fin_health_result_persisted`'s own signature/logic.
"""
from __future__ import annotations

from typing import Any

from agent_platform.runtime.executor import invoke_agent

# Only these fin_health outcomes are eligible to auto-proceed to a proposal —
# NOT_QUALIFIED (Rejected) never should. CONDITIONALLY_QUALIFIED (Review) is
# included because in production this would only fire after the human review
# step that gates it has already happened (recorded on the persisted row) —
# it is not skipping that review, it's firing *after* it.
_PROPOSAL_ELIGIBLE_OUTCOMES = {"QUALIFIED", "CONDITIONALLY_QUALIFIED"}


def on_fin_health_result_persisted(fin_health_result: dict[str, Any]) -> dict[str, Any] | None:
    """Call this with a persisted fin_health result row — real logic, fake
    trigger (see module docstring). Returns proposal_generator's RunContext-
    shaped result dict, or None if this result never should have reached
    proposal_generator in the first place.

    `fin_health_result` is expected to carry at least `business_name`,
    `outcome`, `composite_score`, and whatever real evidence fields
    fin_health scored on — the same shape the Playground's "Send to
    Proposal" button builds by hand today.
    """
    outcome = fin_health_result.get("outcome")
    if outcome not in _PROPOSAL_ELIGIBLE_OUTCOMES:
        return None

    evidence = {
        "business_name": fin_health_result.get("business_name"),
        "fin_health_outcome": outcome,
        "fin_health_score": fin_health_result.get("composite_score"),
        **(fin_health_result.get("evidence") or {}),
    }
    ctx = invoke_agent("proposal_generator", {"evidence": evidence})
    return {
        "run_id": ctx.run_id,
        "decision": ctx.decision,
        "validated_output": ctx.validated_output,
        "error": ctx.error,
    }
