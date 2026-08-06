"""Tests for load_skills (agent_platform/stages/skill_selection.py) and
AgentBundle.active_skills() — the unified multi-skill loading mechanism.
Uses the real multi_skill_demo agent (skills=[advisor_risk, advisor_growth,
explain_to_first_time_applicant, explain_to_repeat_customer]) already on
disk rather than a tmp scaffold, mirroring how tests/test_agent_router.py
exercises real agents directly.
"""
from agent_platform.composition import load_agent
from agent_platform.observability import get_logger
from agent_platform.runtime.context import RunContext
from agent_platform.stages import skill_selection

from fakes import FailingAdapter, FakeAdapter


def _ctx(raw_input: dict) -> RunContext:
    return RunContext.start(agent_id="multi_skill_demo", agent_version="1.0.0", raw_input=raw_input)


def test_active_skills_falls_back_to_every_declared_skill_when_unset():
    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({})
    assert {s.skill_id for s in bundle.active_skills(ctx)} == set(bundle.skills.keys())


def test_active_skills_resolves_to_loaded_skills():
    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({})
    ctx.active_skill_ids = ["advisor_growth"]
    assert [s.skill_id for s in bundle.active_skills(ctx)] == ["advisor_growth"]


def test_single_candidate_agent_skips_llm_entirely(monkeypatch):
    def _boom(bundle):
        raise AssertionError("skill-loading LLM should not be called for a single-skill agent")
    monkeypatch.setattr(skill_selection, "_build_adapter", _boom)

    bundle = load_agent("lead_qualification")  # only one skill declared
    ctx = _ctx({"lead_id": "SME-1001"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == ["lead_qualification"]


def test_explicit_single_override_skips_llm(monkeypatch):
    def _boom(bundle):
        raise AssertionError("skill-loading LLM should not be called when skill_id is given explicitly")
    monkeypatch.setattr(skill_selection, "_build_adapter", _boom)

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"skill_id": "advisor_growth"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == ["advisor_growth"]


def test_explicit_plural_override_loads_every_requested_skill(monkeypatch):
    def _boom(bundle):
        raise AssertionError("skill-loading LLM should not be called when skill_ids is given explicitly")
    monkeypatch.setattr(skill_selection, "_build_adapter", _boom)

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"skill_ids": ["advisor_risk", "advisor_growth"]})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == ["advisor_risk", "advisor_growth"]


def test_explicit_override_unknown_id_falls_back_to_first_declared(monkeypatch):
    def _boom(bundle):
        raise AssertionError("skill-loading LLM should not be called on an override attempt")
    monkeypatch.setattr(skill_selection, "_build_adapter", _boom)

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"skill_id": "not_a_real_skill"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == [next(iter(bundle.skills))]


def test_ai_loads_multiple_skills(monkeypatch):
    monkeypatch.setattr(
        skill_selection, "_build_adapter",
        lambda bundle: FakeAdapter(dynamic_skill_ids=["advisor_risk", "advisor_growth"]),
    )

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"request": "assess both risk and growth potential"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == ["advisor_risk", "advisor_growth"]


def test_ai_loads_none_falls_back_to_first_declared(monkeypatch):
    monkeypatch.setattr(skill_selection, "_build_adapter", lambda bundle: FakeAdapter(dynamic_skill_ids=[]))

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"request": "ambiguous request"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == [next(iter(bundle.skills))]


def test_llm_outage_falls_back_to_first_declared(monkeypatch):
    monkeypatch.setattr(skill_selection, "_build_adapter", lambda bundle: FailingAdapter())

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"request": "assess risk"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == [next(iter(bundle.skills))]


def test_ai_selecting_outside_candidate_set_is_ignored(monkeypatch):
    monkeypatch.setattr(
        skill_selection, "_build_adapter",
        lambda bundle: FakeAdapter(dynamic_skill_ids=["not_a_candidate"]),
    )

    bundle = load_agent("multi_skill_demo")
    ctx = _ctx({"request": "assess something"})
    skill_selection.load_skills(ctx, bundle, get_logger())

    assert ctx.active_skill_ids == [next(iter(bundle.skills))]
