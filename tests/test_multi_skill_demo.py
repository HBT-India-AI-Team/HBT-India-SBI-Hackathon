"""Round-trip test for agents/multi_skill_demo — proves the unified skill
loading mechanism actually produces a real, correct decision: rule-bearing
skills (advisor_risk/advisor_growth) and guidance-only skills
(explain_to_first_time_applicant/explain_to_repeat_customer) can all load
together, any subset can be active, and every applicable rule-bearing
skill's own result is preserved in the explanation even when only one of
them governs the final outcome.
"""
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages, skill_selection

from fakes import FakeAdapter

_GOOD_RISK_EVIDENCE = {"debt_ratio": 0.3, "credit_score": 780, "growth_rate": 2}
_BAD_RISK_EVIDENCE = {"debt_ratio": 0.9, "credit_score": 780, "growth_rate": 2}
_GOOD_GROWTH_EVIDENCE = {"debt_ratio": 0.3, "credit_score": 780, "growth_rate": 25}


def test_explicit_advisor_risk_override_qualifies_on_good_debt_ratio(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    ctx = invoke_agent("multi_skill_demo", {"skill_id": "advisor_risk", "evidence": _GOOD_RISK_EVIDENCE})

    assert ctx.error is None
    assert ctx.active_skill_ids == ["advisor_risk"]
    gate_ids = {g["id"] for g in ctx.rule_results["advisor_risk"]["gates"]["gates"]}
    assert gate_ids == {"ACCEPTABLE_DEBT_RATIO"}
    assert ctx.decision["outcome"] == "QUALIFIED"


def test_explicit_advisor_risk_override_rejects_on_high_debt_ratio(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    ctx = invoke_agent("multi_skill_demo", {"skill_id": "advisor_risk", "evidence": _BAD_RISK_EVIDENCE})

    assert ctx.error is None
    assert ctx.decision["outcome"] == "NOT_QUALIFIED"
    assert ctx.rule_results["advisor_risk"]["gates"]["failures"][0]["gate_id"] == "ACCEPTABLE_DEBT_RATIO"


def test_explicit_advisor_growth_override_uses_different_gates(monkeypatch):
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    ctx = invoke_agent("multi_skill_demo", {"skill_id": "advisor_growth", "evidence": _GOOD_GROWTH_EVIDENCE})

    assert ctx.error is None
    assert ctx.active_skill_ids == ["advisor_growth"]
    gate_ids = {g["id"] for g in ctx.rule_results["advisor_growth"]["gates"]["gates"]}
    assert gate_ids == {"MIN_GROWTH_SIGNAL"}
    assert ctx.decision["outcome"] == "QUALIFIED"
    assert ctx.explanation["skill_id"] == "advisor_growth"


def test_ai_loading_a_single_skill_is_reported_in_explanation(monkeypatch):
    monkeypatch.setattr(
        skill_selection, "_build_adapter",
        lambda bundle: FakeAdapter(dynamic_skill_ids=["advisor_risk"]),
    )
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    ctx = invoke_agent("multi_skill_demo", {"request": "assess debt risk", "evidence": _GOOD_RISK_EVIDENCE})

    assert ctx.error is None
    assert ctx.active_skill_ids == ["advisor_risk"]
    assert ctx.explanation["skills_loaded"] == ["advisor_risk"]
    assert ctx.explanation["skill_loading_reasoning"] == "AI loaded: advisor_risk"


def test_ai_loads_both_rule_bearing_skills_and_worst_outcome_governs(monkeypatch):
    monkeypatch.setattr(
        skill_selection, "_build_adapter",
        lambda bundle: FakeAdapter(dynamic_skill_ids=["advisor_risk", "advisor_growth"]),
    )
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    # debt_ratio 0.9 fails advisor_risk's gate (NOT_QUALIFIED); growth_rate 25
    # passes advisor_growth's gate and scores QUALIFIED -- the more severe
    # outcome should govern, and both skills' own results must still be
    # visible in the explanation, not silently discarded.
    evidence = {"debt_ratio": 0.9, "credit_score": 780, "growth_rate": 25}
    ctx = invoke_agent("multi_skill_demo", {"request": "assess this applicant fully", "evidence": evidence})

    assert ctx.error is None
    assert set(ctx.active_skill_ids) == {"advisor_risk", "advisor_growth"}
    assert ctx.decision["outcome"] == "NOT_QUALIFIED"
    breakdown = {b["skill_id"]: b["outcome"] for b in ctx.decision["skill_breakdown"]}
    assert breakdown == {"advisor_risk": "NOT_QUALIFIED", "advisor_growth": "QUALIFIED"}
    assert ctx.explanation["skill_breakdown"] == ctx.decision["skill_breakdown"]


def test_guidance_only_skill_loads_alongside_a_rule_bearing_one(monkeypatch):
    monkeypatch.setattr(
        skill_selection, "_build_adapter",
        lambda bundle: FakeAdapter(dynamic_skill_ids=["advisor_risk", "explain_to_first_time_applicant"]),
    )
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    ctx = invoke_agent(
        "multi_skill_demo",
        {"request": "first-time applicant risk check", "evidence": _GOOD_RISK_EVIDENCE},
    )

    assert ctx.error is None
    assert set(ctx.active_skill_ids) == {"advisor_risk", "explain_to_first_time_applicant"}
    # the guidance-only skill has no rules, so it never appears in rule_results/skill_breakdown
    assert set(ctx.rule_results.keys()) == {"advisor_risk"}
    assert ctx.decision["outcome"] == "QUALIFIED"
    assert set(ctx.explanation["skills_loaded"]) == {"advisor_risk", "explain_to_first_time_applicant"}
