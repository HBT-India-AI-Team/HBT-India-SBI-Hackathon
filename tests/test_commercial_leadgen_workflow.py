"""End-to-end commercial_leadgen_demo workflow tests with a fake LLM —
covers all three branch outcomes (proceed / rejected / needs human review)
deterministically, without depending on a live (and occasionally flaky)
Ollama tunnel. See test_live_workflow_smoke below for the one live check.
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.stages import pipeline_stages
from agent_platform.state import get_run
from agent_platform.workflows import run_workflow

from fakes import FakeAdapter


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


def test_strong_lead_proceeds_through_to_proposal():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "manufacturing", "location": "Chennai", "business_need": "working_capital",
    })
    assert result["status"] == "COMPLETED"
    assert result["discovery"]["selected_lead_id"] == "SME-1001"
    assert result["qualification"]["qualification_status"] == "QUALIFIED"
    assert result["proposal"] is not None
    assert result["proposal"]["proposal_status"] == "READY"
    assert result["proposal"]["customer_proposal"]


def test_weak_lead_is_rejected_and_proposal_is_skipped():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "construction", "location": "Chennai",
    })
    assert result["discovery"]["selected_lead_id"] == "SME-1004"
    assert result["qualification"]["qualification_status"] == "NOT_QUALIFIED"
    assert result["proposal"] is None
    assert result["skip_reason"] == "NOT_QUALIFIED"


def test_borderline_lead_needs_human_review_and_proposal_is_skipped():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "import_export", "location": "Chennai",
    })
    assert result["discovery"]["selected_lead_id"] == "SME-1003"
    assert result["qualification"]["qualification_status"] == "NEEDS_HUMAN_REVIEW"
    assert result["proposal"] is None
    assert result["skip_reason"] == "NEEDS_HUMAN_REVIEW"
    assert result["qualification"]["hitl"] is not None


def test_no_matching_candidates_fails_the_workflow_cleanly():
    result = run_workflow("commercial_leadgen_demo", {"industry": "does_not_exist"})
    assert result["status"] == "FAILED"
    assert "no matching candidates" in result["error"]["message"]


def test_workflow_run_is_persisted_with_child_agent_run_ids():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "manufacturing", "location": "Chennai", "business_need": "working_capital",
    })
    record = get_run(result["run_id"])
    assert record is not None
    assert record["explanation"]["discovery"]["run_id"]
    assert record["explanation"]["qualification"]["run_id"]
    assert record["explanation"]["proposal"]["run_id"]
    # each child run is independently addressable too
    discovery_record = get_run(record["explanation"]["discovery"]["run_id"])
    assert discovery_record["agent_id"] == "lead_discovery"


def test_explainability_block_carries_every_stage_reason():
    result = run_workflow("commercial_leadgen_demo", {
        "industry": "manufacturing", "location": "Chennai", "business_need": "working_capital",
    })
    explainability = result["explainability"]
    assert explainability["lead_selection_reason"]
    assert explainability["qualification_rule_summary"]
    assert explainability["product_selection_reason"]
