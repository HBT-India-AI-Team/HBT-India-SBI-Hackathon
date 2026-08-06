import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages

from fakes import FakeAdapter

_STRONG_LEAD_INPUT = {
    "lead": {
        "industry": "manufacturing",
        "business_vintage_years": 6,
        "financials": {"dscr": 1.55, "current_ratio": 1.6},
    },
    "qualification_result": {"outcome": "QUALIFIED", "composite_score": 97.0},
    "eligible_products": ["WORKING_CAPITAL_OD", "TERM_LOAN_EXPANSION", "TRADE_FINANCE"],
}


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


def test_top_product_matches_most_specific_criteria():
    ctx = invoke_agent("proposal", _STRONG_LEAD_INPUT)
    assert ctx.error is None
    top = ctx.rule_results["selected_products"][0]
    assert top["id"] == "TERM_LOAN_EXPANSION"  # matches composite>=75 AND dscr>=1.25
    assert top["fit_score"] == 100


def test_only_eligible_products_are_considered():
    restricted = {**_STRONG_LEAD_INPUT, "eligible_products": ["WORKING_CAPITAL_OD"]}
    ctx = invoke_agent("proposal", restricted)
    ids = {p["id"] for p in ctx.rule_results["selected_products"]}
    assert ids == {"WORKING_CAPITAL_OD"}


def test_proposal_status_ready_when_products_match():
    ctx = invoke_agent("proposal", _STRONG_LEAD_INPUT)
    assert ctx.decision["proposal_status"] == "READY"
    assert ctx.decision["recommended_product_ids"][0] == "TERM_LOAN_EXPANSION"


def test_no_product_match_when_no_eligible_products():
    empty = {**_STRONG_LEAD_INPUT, "eligible_products": []}
    ctx = invoke_agent("proposal", empty)
    assert ctx.decision["proposal_status"] == "NO_PRODUCT_MATCH"
    assert ctx.rule_results["selected_products"] == []


def test_required_documents_are_deterministic_not_llm_sourced():
    ctx = invoke_agent("proposal", _STRONG_LEAD_INPUT)
    top = ctx.rule_results["selected_products"][0]
    assert "Business expansion / project plan" in top["required_documents"]


def test_proposal_never_changes_the_qualification_outcome():
    # Proposal has no gates/thresholds of its own to override a decision
    # with — it can only rank among products it's handed.
    ctx = invoke_agent("proposal", _STRONG_LEAD_INPUT)
    assert "outcome" not in ctx.decision
    assert "qualification_result" not in ctx.decision
