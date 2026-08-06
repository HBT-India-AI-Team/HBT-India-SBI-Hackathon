"""End-to-end pipeline tests with a fake LLM — no live Ollama required.
Confirms the deterministic decision (gates, scoring, thresholds) is correct
regardless of what the LLM does, and that a failing/unavailable LLM
degrades gracefully instead of crashing the run.
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.stages import pipeline_stages


class FakeAdapter:
    """Always returns a minimal, schema-valid rationale. Strengths/risks are
    left empty so the test doesn't need to know each lead's real evidence
    keys in advance.
    """

    def __init__(self, *args, **kwargs):
        pass

    def generate_structured(self, *, system_prompt, user_prompt, schema, temperature=0.0):
        output = {
            "summary": "fake summary",
            "strengths": [],
            "risks": [],
            "next_best_action": "fake action",
            "product_rationale": {},
            "confidence": 0.9,
        }
        metadata = {"model": "fake-model", "duration_ms": 1.0,
                    "prompt_tokens": 10, "completion_tokens": 10}
        return output, metadata


class FailingAdapter:
    def __init__(self, *args, **kwargs):
        pass

    def generate_structured(self, *args, **kwargs):
        from agent_platform.llm import OllamaError
        raise OllamaError("simulated LLM outage")


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


def _run(lead_id: str):
    from agent_platform.runtime.executor import invoke_agent
    return invoke_agent("lead_qualification", {"lead_id": lead_id})


def test_qualified_lead():
    ctx = _run("SME-1001")
    assert ctx.error is None
    assert ctx.decision["outcome"] == "QUALIFIED"
    assert ctx.decision["composite_score"] >= 75


def test_conditionally_qualified_lead():
    ctx = _run("SME-1002")
    assert ctx.decision["outcome"] == "CONDITIONALLY_QUALIFIED"
    assert 55 <= ctx.decision["composite_score"] < 75


def test_bureau_floor_forces_human_review():
    ctx = _run("SME-1003")
    assert ctx.decision["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "650" in ctx.decision["reason"] or "floor" in ctx.decision["reason"].lower()


def test_sanctions_hit_forces_not_qualified():
    ctx = _run("SME-1004")
    assert ctx.decision["outcome"] == "NOT_QUALIFIED"
    skill_results = ctx.rule_results["lead_qualification"]
    gate_ids_failed = {f["gate_id"] for f in skill_results["gates"]["failures"]}
    assert "NO_SANCTIONS_HIT" in gate_ids_failed
    assert skill_results["products"] == []


def test_multiple_gate_failures_pick_most_severe():
    ctx = _run("SME-1005")
    assert ctx.decision["outcome"] == "NOT_QUALIFIED"
    gate_ids_failed = {f["gate_id"] for f in ctx.rule_results["lead_qualification"]["gates"]["failures"]}
    assert {"KYC_COMPLETE", "MIN_VINTAGE", "BUREAU_SCORE_FLOOR"} <= gate_ids_failed


def test_unknown_lead_is_a_captured_error_not_a_crash():
    ctx = _run("SME-9999")
    assert ctx.error is not None
    assert ctx.error["stage"] == "gather_evidence"
    assert ctx.explanation is not None  # explanation still produced on error


def test_llm_outage_falls_back_to_deterministic_rationale(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FailingAdapter())
    ctx = _run("SME-1001")
    assert ctx.error is None
    assert ctx.validated_output["degraded"] is True
    # composite score is unaffected by the LLM outage, but low confidence from
    # the deterministic fallback correctly escalates to human review
    assert ctx.decision["composite_score"] >= 75
    assert ctx.decision["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert ctx.hitl["triggered"] is True


def test_every_run_has_a_persisted_record():
    from agent_platform.state import get_run
    ctx = _run("SME-1001")
    record = get_run(ctx.run_id)
    assert record is not None
    for key in ("run_id", "input_summary", "stage_results", "final_result", "explanation", "error"):
        assert key in record
