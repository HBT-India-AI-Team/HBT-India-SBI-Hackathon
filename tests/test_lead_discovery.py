import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages

from fakes import FakeAdapter


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


def test_search_filters_by_industry_and_location():
    ctx = invoke_agent("lead_discovery", {"industry": "manufacturing", "location": "Chennai"})
    assert ctx.error is None
    ids = {c["lead_id"] for c in ctx.rule_results["ranked_leads"]}
    assert "SME-1001" in ids
    assert "SME-1002" not in ids  # trading, not manufacturing


def test_selected_lead_is_the_top_ranked_one():
    ctx = invoke_agent("lead_discovery", {"location": "Chennai", "business_need": "working_capital"})
    ranked = ctx.rule_results["ranked_leads"]
    assert ctx.decision["selected_lead_id"] == ranked[0]["lead_id"]
    assert ranked[0]["lead_id"] == "SME-1001"
    scores = [c["discovery_score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)


def test_limit_caps_ranked_leads():
    ctx = invoke_agent("lead_discovery", {"location": "Chennai", "limit": 1})
    assert len(ctx.rule_results["ranked_leads"]) == 1


def test_no_matching_candidates_selects_nothing_without_erroring():
    ctx = invoke_agent("lead_discovery", {"industry": "does_not_exist"})
    assert ctx.error is None
    assert ctx.decision["selected_lead_id"] is None
    assert ctx.rule_results["ranked_leads"] == []


def test_inactive_leads_excluded_by_default():
    ctx = invoke_agent("lead_discovery", {"industry": "logistics"})
    ids = {c["lead_id"] for c in ctx.rule_results["ranked_leads"]}
    assert "SME-1005" not in ids  # active: false in the fixture


def test_every_discovery_run_is_persisted():
    from agent_platform.state import get_run
    ctx = invoke_agent("lead_discovery", {"location": "Chennai"})
    record = get_run(ctx.run_id)
    assert record is not None
    assert record["final_result"]["selected_lead_id"] == ctx.decision["selected_lead_id"]
