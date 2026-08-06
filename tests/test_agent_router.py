"""Tests for the agent_router workflow — routes a raw request to whichever
registered agent should handle it (LLM-decided, or an explicit `agent_id`
override), without depending on a live (and occasionally flaky) Ollama
tunnel. Uses its own small fake since tests/fakes.py's shared FakeAdapter
doesn't special-case `agent_id` (by design — it isn't router-aware).
"""
import pytest

import capabilities_impl  # noqa: F401  (registers mock tools)
from agent_platform.stages import pipeline_stages
from agent_platform.state import get_run
from agent_platform.workflows import agent_router, list_workflows, run_workflow

from fakes import FailingAdapter, FakeAdapter


class FakeRoutingAdapter:
    def __init__(self, agent_id: str = "lead_qualification", confidence: float = 0.95,
                 reasoning: str = "fake routing reasoning"):
        self.agent_id = agent_id
        self.confidence = confidence
        self.reasoning = reasoning

    def generate_structured(self, *, system_prompt, user_prompt, schema, temperature=0.0):
        parsed = {"agent_id": self.agent_id, "confidence": self.confidence, "reasoning": self.reasoning}
        metadata = {"model": "fake-router-model", "duration_ms": 1.0,
                    "prompt_tokens": 10, "completion_tokens": 10}
        return parsed, metadata


@pytest.fixture(autouse=True)
def fake_downstream_llm(monkeypatch):
    # Whichever agent the router picks still runs its own reason_llm stage.
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())


def test_agent_router_is_registered():
    assert "agent_router" in list_workflows()


def test_catalog_excludes_non_routable_agents():
    catalog = agent_router._build_catalog()
    ids = {c["agent_id"] for c in catalog}
    assert "echo_probe" not in ids
    assert {"lead_qualification", "lead_discovery", "proposal"} <= ids


def test_confident_routing_invokes_chosen_agent(monkeypatch):
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: FakeRoutingAdapter(agent_id="lead_qualification", confidence=0.95))

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    assert result["status"] == "COMPLETED"
    assert result["chosen_agent_id"] == "lead_qualification"
    assert result["routing_confidence"] == 0.95
    assert result["agent_run_id"]
    assert result["decision"]["outcome"] == "QUALIFIED"


def test_low_confidence_returns_needs_clarification_without_invoking_agent(monkeypatch):
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: FakeRoutingAdapter(agent_id="lead_qualification", confidence=0.3))

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["chosen_agent_id"] is None
    assert result["agent_run_id"] is None


def test_llm_outage_returns_needs_clarification(monkeypatch):
    monkeypatch.setattr(agent_router, "_build_router_adapter", lambda: FailingAdapter())

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["agent_run_id"] is None


def test_agent_id_outside_catalog_is_rejected_defensively(monkeypatch):
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: FakeRoutingAdapter(agent_id="not_a_real_agent", confidence=0.95))

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["agent_run_id"] is None


def test_chosen_agent_input_mismatch_is_surfaced_not_swallowed(monkeypatch):
    # Route confidently to "proposal", whose input_schema this raw_input
    # doesn't satisfy at all — invoke_agent won't raise (run_pipeline
    # swallows stage errors), so the router must check agent_ctx.error itself.
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: FakeRoutingAdapter(agent_id="proposal", confidence=0.95))

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    assert result["status"] == "AGENT_FAILED"
    assert result["error"] is not None


def test_router_run_is_persisted(monkeypatch):
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: FakeRoutingAdapter(agent_id="lead_qualification", confidence=0.95))

    result = run_workflow("agent_router", {"lead_id": "SME-1001"})

    stored = get_run(result["run_id"])
    assert stored is not None
    assert stored["explanation"] == result


def test_explicit_agent_id_override_skips_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_router, "_build_router_adapter",
                         lambda: calls.append("called") or FakeRoutingAdapter())

    result = run_workflow("agent_router", {"agent_id": "lead_qualification", "lead_id": "SME-1001"})

    assert calls == []  # LLM adapter never constructed
    assert result["status"] == "COMPLETED"
    assert result["chosen_agent_id"] == "lead_qualification"
    assert result["routing_confidence"] == 1.0


def test_explicit_agent_id_override_outside_catalog_needs_clarification():
    result = run_workflow("agent_router", {"agent_id": "not_a_real_agent", "lead_id": "SME-1001"})

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["agent_run_id"] is None
