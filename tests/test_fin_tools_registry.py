"""Inline calculators — EMI and FIRE.

Two guarantees. A calculator computes through the same capability the agent
called, so the widget and the sentence above it cannot disagree. And a
calculator opens only when it is wanted: on a question the agent actually
treated as an EMI question, or on one that named the topic without giving
numbers to work with.
"""
from __future__ import annotations

import pytest

from backend import tool_store, tool_suggest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_store, "DB_PATH", tmp_path / "tools.db")
    tool_store.init_db()


def test_the_catalog_serves_emi_and_fire():
    tools = {tool["tool_id"]: tool for tool in tool_store.get_tools()}
    assert {"emi_calculator", "fire_calculator"} <= set(tools)
    assert all(tool["active"] for tool in tools.values())
    # Every calculator must name a capability; that is what does the maths.
    assert all(tool["capability"] for tool in tools.values())


def test_a_calculator_computes_through_its_capability():
    """Not through a formula of its own.

    A stored formula evaluated in the browser was the first design. It means
    two implementations of the same arithmetic, and the first time they
    disagree the user is looking at a contradiction with no way to tell which
    number is real.
    """
    from agent_platform.capabilities import DEFAULT_REGISTRY

    computed = tool_store.run_tool(
        "emi_calculator", {"principal": 2000000, "rate": 8.5, "months": 240})
    direct = DEFAULT_REGISTRY.invoke(
        "finance.calculate_emi", principal=2000000, annual_rate_percent=8.5, tenure_months=240)

    assert computed["value"] == direct["emi"]
    assert computed["output_label"] == "Monthly EMI"


def test_unusable_inputs_are_rejected_not_guessed():
    with pytest.raises(ValueError):
        tool_store.run_tool("emi_calculator", {"principal": 2000000, "rate": 8.5})
    with pytest.raises(ValueError):
        tool_store.run_tool("emi_calculator",
                            {"principal": "lots", "rate": 8.5, "months": 240})
    with pytest.raises(KeyError):
        tool_store.run_tool("no_such_calculator", {})


def test_a_tool_call_opens_a_prefilled_calculator():
    """The strong signal. The arguments the capability was invoked with are
    exactly the numbers the calculator should open on, and they are the ones
    already quoted in the prose."""
    trace = [{"detail": {"tool_calls": [{
        "name": "finance.calculate_emi",
        "arguments": {"principal": 2000000, "annual_rate_percent": 8.5, "tenure_months": 240},
        "result": {"emi": 17356.46},
    }]}}]

    [suggestion] = tool_suggest.suggest("emi on a 20 lakh loan?", trace)

    assert suggestion["tool_id"] == "emi_calculator"
    assert suggestion["reason"] == "computed"
    assert suggestion["prefill"] == {"principal": 2000000, "rate": 8.5, "months": 240}
    # The definition travels with it, so a client can render without a second
    # round trip to look the calculator up.
    assert [f["key"] for f in suggestion["tool"]["inputs"]] == ["principal", "rate", "months"]


def test_a_goal_question_prefills_the_answer_it_computed():
    """"2 crore in 20 years — what monthly SIP?" never *supplies* a monthly
    investment, it computes one. Reading the call's arguments alone leaves the
    calculator's main field blank directly under a reply that just quoted the
    figure for it, so the computed value is read out of the result."""
    trace = [{"detail": {"tool_calls": [{
        "name": "money.sip_required_for_goal",
        "arguments": {"target_amount": 20000000, "annual_return_percent": 12, "years": 20},
        "result": {"ok": True, "required_monthly_investment": 20016.81},
    }]}}]

    [suggestion] = tool_suggest.suggest("2 crore corpus in 20 years?", trace)

    assert suggestion["reason"] == "computed"
    assert suggestion["prefill"] == {
        "annual_return": 12, "years": 20, "monthly_investment": 20016.81}
    # Every field the widget renders is filled, so it opens showing a result
    # rather than an empty form.
    assert set(suggestion["prefill"]) == {f["key"] for f in suggestion["tool"]["inputs"]}


def test_a_failed_capability_prefills_nothing_from_its_result():
    """No number reached the prose, so none should reach the widget."""
    trace = [{"detail": {"tool_calls": [{
        "name": "money.sip_required_for_goal",
        "arguments": {"target_amount": -5, "annual_return_percent": 12, "years": 20},
        "result": {"ok": False, "reason": "target_amount must be > 0"},
    }]}}]

    [suggestion] = tool_suggest.suggest("corpus?", trace)

    assert "monthly_investment" not in suggestion["prefill"]


def test_naming_the_topic_opens_an_empty_calculator():
    """"How does EMI work?" has nothing to compute, so no tool runs — and that
    is exactly when someone wants a blank calculator to try numbers in."""
    [suggestion] = tool_suggest.suggest("how does EMI actually work?", [])
    assert suggestion["tool_id"] == "emi_calculator"
    assert suggestion["reason"] == "mentioned"
    assert suggestion["prefill"] == {}


@pytest.mark.parametrize("message", [
    "what is the premium on this policy?",   # 'emi' inside 'premium'
    "my semi annual bonus",                  # 'emi' inside 'semi'
    "tell me about fire insurance",          # 'fire' but not the movement
    "is there a fire safety cover?",
    "what is the SBI savings rate?",
])
def test_calculators_do_not_open_on_near_misses(message):
    """A substring check would fire on every one of these. The cost of a false
    positive is a calculator appearing under an unrelated answer, which reads
    as the product being broken."""
    assert tool_suggest.suggest(message, []) == []


def test_a_computed_calculator_beats_a_mentioned_one():
    """Both signals can fire on the same turn. Only one calculator should
    appear, and it should be the one with the numbers in it."""
    trace = [{"detail": {"tool_calls": [{
        "name": "finance.calculate_emi",
        "arguments": {"principal": 500000, "annual_rate_percent": 10, "tenure_months": 60},
        "result": {},
    }]}}]

    suggestions = tool_suggest.suggest("what is my EMI?", trace)

    assert len(suggestions) == 1
    assert suggestions[0]["reason"] == "computed"


def test_saved_instances_are_per_user():
    tool_store.save_user_tool(user_id="u-1", tool_id="emi_calculator",
                              input_values={"principal": 100000, "rate": 9, "months": 12},
                              result={"value": 8745.15})

    mine = tool_store.get_saved_tools(user_id="u-1")
    assert mine[0]["input_values"]["principal"] == 100000
    assert mine[0]["tool"]["name"] == "EMI Calculator"
    # The whole point of keying on user_id rather than a free-text name.
    assert tool_store.get_saved_tools(user_id="u-2") == []


def test_saving_twice_updates_rather_than_duplicating():
    for principal in (100000, 250000):
        tool_store.save_user_tool(user_id="u-1", tool_id="emi_calculator",
                                  input_values={"principal": principal, "rate": 9, "months": 12})
    saved = tool_store.get_saved_tools(user_id="u-1")
    assert len(saved) == 1
    assert saved[0]["input_values"]["principal"] == 250000


def test_a_broken_registry_does_not_cost_the_answer(monkeypatch):
    """Suggestions are decoration on a reply that is already correct. If the
    calculator store cannot be read, the answer still goes out."""
    def boom(_tool_id):
        raise RuntimeError("database gone")

    monkeypatch.setattr(tool_store, "get_tool_by_id", boom)
    assert tool_suggest.suggest("how does EMI work?", []) == []
