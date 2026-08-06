"""Tests for gather_evidence's inline-evidence path — a caller can supply
`{"evidence": {...}}` directly instead of being limited to the 5 canned SME
fixture leads via `{"lead_id": ...}`. The fixture path is unchanged and
covered separately by tests/test_pipeline_end_to_end.py; this file is
scoped to the stage's now-generic dispatch behavior.
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages

from fakes import FakeAdapter


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


_SAMPLE_EVIDENCE = {
    "lead": {"business_vintage_years": 6},
    "financials": {"turnover_growth_pct": 18, "current_ratio": 1.6, "monthly_avg_balance_lakhs": 12,
                    "dscr": 1.55},
    "bureau": {"score": 780, "default_flag": False},
    "kyc": {"status": "VERIFIED", "sanctions_hit": False, "gst_filing_regularity_pct": 98},
}


def test_inline_evidence_sets_ctx_evidence_directly():
    ctx = invoke_agent("lead_qualification", {"evidence": _SAMPLE_EVIDENCE})
    assert ctx.error is None
    assert ctx.evidence == _SAMPLE_EVIDENCE


def test_inline_evidence_skips_capability_calls_entirely(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("capability should not be called on the inline-evidence path")

    monkeypatch.setattr(DEFAULT_REGISTRY, "invoke", _boom)

    ctx = invoke_agent("lead_qualification", {"evidence": _SAMPLE_EVIDENCE})
    assert ctx.error is None


def test_inline_evidence_is_deep_copied_not_aliased():
    original = {"lead": {"business_vintage_years": 6}, "financials": {}, "bureau": {}, "kyc": {}}
    ctx = invoke_agent("lead_qualification", {"evidence": original})

    ctx.evidence["lead"]["business_vintage_years"] = 999

    assert original["lead"]["business_vintage_years"] == 6


def test_missing_both_lead_id_and_evidence_is_a_captured_error():
    ctx = invoke_agent("lead_qualification", {})
    assert ctx.error is not None
    assert ctx.error["stage"] == "gather_evidence"
    assert "lead_id" in ctx.error["message"]
    assert "evidence" in ctx.error["message"]
    assert ctx.explanation is not None


def test_evidence_wins_when_both_supplied():
    ctx = invoke_agent("lead_qualification", {"lead_id": "SME-1001", "evidence": _SAMPLE_EVIDENCE})
    assert ctx.evidence == _SAMPLE_EVIDENCE


def test_capability_check_only_applies_to_lead_id_path(monkeypatch):
    monkeypatch.setattr(DEFAULT_REGISTRY, "has", lambda name: False)

    inline_ctx = invoke_agent("lead_qualification", {"evidence": _SAMPLE_EVIDENCE})
    assert inline_ctx.error is None

    fixture_ctx = invoke_agent("lead_qualification", {"lead_id": "SME-1001"})
    assert fixture_ctx.error is not None
    assert fixture_ctx.error["type"] == "KeyError"


def test_lead_id_path_is_unchanged():
    ctx = invoke_agent("lead_qualification", {"lead_id": "SME-1001"})
    assert ctx.error is None
    assert set(ctx.evidence.keys()) == {"lead", "financials", "bureau", "kyc"}
    assert ctx.evidence["lead"]["business_name"] == "Anand Precision Tools Pvt Ltd"
    assert ctx.evidence["financials"]["annual_turnover_cr"] == 8.2
    assert ctx.evidence["bureau"]["score"] == 780
    assert ctx.evidence["kyc"]["status"] == "VERIFIED"
