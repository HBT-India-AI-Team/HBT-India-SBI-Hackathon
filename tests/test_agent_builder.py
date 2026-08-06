"""Tests for backend/agent_builder.py — the "Describe it" meta-agent that
turns a plain-language description into a real gates/factors/composite/
product_fit rule set. Covers validate_spec's semantic checks, the
generate -> validate -> auto-repair -> re-prompt -> fallback pipeline, the
renderers, and a full round trip through the POST /admin/agents/generate
endpoint proving a generated agent actually runs and decides something.
"""
import pytest
import yaml

import capabilities_impl  # noqa: F401  (registers mock tools)
import agent_platform.composition.loader as loader
import backend.admin as admin
from agent_platform.composition import load_agent
from agent_platform.llm import OllamaError
from agent_platform.runtime.executor import invoke_agent
from agent_platform.stages import pipeline_stages
from backend import agent_builder
from backend.admin import GenerateAgentPayload

from fakes import FakeAdapter


def _valid_spec() -> dict:
    return {
        "purpose": "test",
        "evidence_fields": [{"path": "debt_ratio", "type": "number", "description": "d"}],
        "gates": [{
            "id": "G1", "description": "d", "field": "debt_ratio", "operator": "lt", "value": "0.6",
            "on_fail_decision": "NOT_QUALIFIED", "on_fail_reason": "too high",
        }],
        "categories": [{
            "name": "risk",
            "factors": [{
                "id": "F1", "description": "d", "field": "debt_ratio", "weight": 1.0,
                "bands": [{"min": 0, "score": 100}, {"min": 1, "score": 0}],
            }],
        }],
        "composite_weights": [{"category": "risk", "weight": 1.0}],
        "thresholds": {"qualified_min": 75, "conditional_min": 50},
        "products": [{"id": "P1", "name": "Loan", "reason": "r", "when": []}],
    }


class FakeSpecAdapter:
    def __init__(self, specs):
        self._specs = list(specs)
        self.calls = 0

    def generate_structured(self, *, system_prompt, user_prompt, schema, temperature=0.0):
        spec = self._specs[min(self.calls, len(self._specs) - 1)]
        self.calls += 1
        return spec, {"model": "fake", "duration_ms": 1.0, "prompt_tokens": 1, "completion_tokens": 1}


class FailingSpecAdapter:
    def generate_structured(self, *args, **kwargs):
        raise OllamaError("simulated outage")


# -- validate_spec ----

def test_valid_spec_has_no_errors():
    assert agent_builder.validate_spec(_valid_spec()) == []


def test_fallback_blank_spec_always_valid():
    spec = agent_builder.fallback_blank_spec("purpose text", "agent_id")
    assert agent_builder.validate_spec(spec) == []


def test_undeclared_gate_field_is_rejected():
    spec = _valid_spec()
    spec["gates"][0]["field"] = "not_declared"
    errors = agent_builder.validate_spec(spec)
    assert any("undeclared field" in e for e in errors)


def test_undeclared_factor_field_is_rejected():
    spec = _valid_spec()
    spec["categories"][0]["factors"][0]["field"] = "not_declared"
    errors = agent_builder.validate_spec(spec)
    assert any("undeclared field" in e for e in errors)


def test_category_weight_mismatch_is_rejected():
    spec = _valid_spec()
    spec["composite_weights"] = [{"category": "wrong_name", "weight": 1.0}]
    errors = agent_builder.validate_spec(spec)
    assert any("composite_weights categories" in e for e in errors)


def test_missing_fallback_product_is_flagged():
    spec = _valid_spec()
    spec["products"][0]["when"] = [{"field": "debt_ratio", "operator": "lt", "value": "1"}]
    errors = agent_builder.validate_spec(spec)
    assert any("when: []" in e for e in errors)


def test_bad_threshold_ordering_is_rejected():
    spec = _valid_spec()
    spec["thresholds"] = {"qualified_min": 50, "conditional_min": 75}
    errors = agent_builder.validate_spec(spec)
    assert any("qualified_min" in e for e in errors)


def test_bands_below_minimum_is_rejected():
    spec = _valid_spec()
    spec["categories"][0]["factors"][0]["bands"] = [{"min": 0, "score": 100}]
    errors = agent_builder.validate_spec(spec)
    assert any("at least 2 bands" in e for e in errors)


def test_unknown_operator_is_rejected():
    spec = _valid_spec()
    spec["gates"][0]["operator"] = "startswith"
    errors = agent_builder.validate_spec(spec)
    assert any("unknown operator" in e for e in errors)


def test_numeric_operator_with_non_numeric_value_is_rejected():
    # Caught live: a model paired operator "gt" with value ">=300" instead
    # of "300" — parses fine as JSON but raises TypeError (str vs number)
    # at rules-evaluation time if not caught here.
    spec = _valid_spec()
    spec["gates"][0]["operator"] = "gt"
    spec["gates"][0]["value"] = ">=300"
    errors = agent_builder.validate_spec(spec)
    assert any("not numeric" in e for e in errors)


def test_numeric_operator_with_non_numeric_product_condition_value_is_rejected():
    spec = _valid_spec()
    spec["products"][0]["when"] = [{"field": "debt_ratio", "operator": "lt", "value": ">=0.5"}]
    errors = agent_builder.validate_spec(spec)
    assert any("not numeric" in e for e in errors)


# -- auto_repair ----

def test_auto_repair_adds_fallback_product():
    spec = _valid_spec()
    spec["products"][0]["when"] = [{"field": "debt_ratio", "operator": "lt", "value": "1"}]
    errors = agent_builder.validate_spec(spec)
    repaired = agent_builder.auto_repair(spec, errors)
    assert agent_builder.validate_spec(repaired) == []
    assert any(p["when"] == [] for p in repaired["products"])


# -- generate_spec's three-tier pipeline ----

def test_generate_spec_accepts_valid_first_attempt(monkeypatch):
    adapter = FakeSpecAdapter([_valid_spec()])
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: adapter)

    result = agent_builder.generate_spec(purpose="x", agent_id="a1")

    assert result.used_fallback is False
    assert result.attempts == 1
    assert adapter.calls == 1


def test_generate_spec_auto_repairs_without_a_second_llm_call(monkeypatch):
    spec = _valid_spec()
    spec["products"][0]["when"] = [{"field": "debt_ratio", "operator": "lt", "value": "1"}]
    adapter = FakeSpecAdapter([spec])
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: adapter)

    result = agent_builder.generate_spec(purpose="x", agent_id="a1")

    assert result.used_fallback is False
    assert result.attempts == 1
    assert adapter.calls == 1
    assert any(p["when"] == [] for p in result.spec["products"])


def test_generate_spec_falls_back_immediately_on_unrepairable_error_no_second_llm_call(monkeypatch):
    # No automatic re-prompt tier anymore — one attempt, cheap auto_repair
    # only, straight to the fallback if that's not enough. The human
    # feedback loop (refine_spec) is what handles this case now, not a
    # second automatic LLM call.
    bad_spec = _valid_spec()
    bad_spec["gates"][0]["field"] = "not_declared"
    adapter = FakeSpecAdapter([bad_spec])
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: adapter)

    result = agent_builder.generate_spec(purpose="my purpose", agent_id="a1")

    assert result.used_fallback is True
    assert result.attempts == 1
    assert adapter.calls == 1
    assert agent_builder.validate_spec(result.spec) == []
    assert result.spec["purpose"] == "my purpose"


def test_generate_spec_falls_back_on_llm_outage(monkeypatch):
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FailingSpecAdapter())

    result = agent_builder.generate_spec(purpose="x", agent_id="a1")

    assert result.used_fallback is True
    assert agent_builder.validate_spec(result.spec) == []


# -- decompose_purpose / generate_agent_skills: multi-skill generation ----

def _decomposed_two_skills():
    return {
        "skills": [
            {"skill_id": "personal_loans", "description": "handles personal loans", "scope": "personal loan rules"},
            {"skill_id": "business_loans", "description": "handles business loans", "scope": "business loan rules"},
        ]
    }


def test_decompose_purpose_returns_single_skill_when_llm_says_one(monkeypatch):
    adapter = FakeSpecAdapter([{"skills": [{"skill_id": "x", "description": "d", "scope": "whole purpose"}]}])
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: adapter)

    result = agent_builder.decompose_purpose("a plain purpose")

    assert len(result) == 1
    assert result[0]["scope"] == "whole purpose"


def test_decompose_purpose_returns_multiple_skills(monkeypatch):
    adapter = FakeSpecAdapter([_decomposed_two_skills()])
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: adapter)

    result = agent_builder.decompose_purpose("two distinct products")

    assert [s["skill_id"] for s in result] == ["personal_loans", "business_loans"]


def test_decompose_purpose_falls_back_to_one_skill_on_llm_outage(monkeypatch):
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: FailingSpecAdapter())

    result = agent_builder.decompose_purpose("some purpose")

    assert len(result) == 1
    assert result[0]["scope"] == "some purpose"


def test_decompose_purpose_falls_back_on_malformed_response(monkeypatch):
    adapter = FakeSpecAdapter([{"not_skills": []}])
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: adapter)

    result = agent_builder.decompose_purpose("some purpose")

    assert len(result) == 1


def test_generate_agent_skills_single_skill_matches_generate_spec_behavior(monkeypatch):
    single_decomp = {"skills": [{"skill_id": "", "description": "d", "scope": "the purpose"}]}
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: FakeSpecAdapter([single_decomp]))
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec()]))

    result = agent_builder.generate_agent_skills(purpose="the purpose", agent_id="agentx")

    assert result.split is False
    assert len(result.skills) == 1
    assert result.skills[0].skill_id == "agentx"
    assert result.skills[0].result.used_fallback is False


def test_generate_agent_skills_splits_when_llm_decomposes(monkeypatch):
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: FakeSpecAdapter([_decomposed_two_skills()]))
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec(), _valid_spec()]))

    result = agent_builder.generate_agent_skills(purpose="two distinct products", agent_id="loans")

    assert result.split is True
    assert [s.skill_id for s in result.skills] == ["personal_loans", "business_loans"]
    assert all(s.result.used_fallback is False for s in result.skills)


def test_generate_agent_skills_dedupes_colliding_skill_ids(monkeypatch):
    decomp = {
        "skills": [
            {"skill_id": "review", "description": "a", "scope": "scope a"},
            {"skill_id": "review", "description": "b", "scope": "scope b"},
        ]
    }
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: FakeSpecAdapter([decomp]))
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec(), _valid_spec()]))

    result = agent_builder.generate_agent_skills(purpose="x", agent_id="agentx")

    assert [s.skill_id for s in result.skills] == ["review", "review_2"]


# -- refine_spec: human-described correction, one attempt, never falls back ----

def _current_rules_text():
    files = agent_builder.render_skill_files("a1", _valid_spec())
    return {
        "gates": files["rules/gates.yaml"],
        "factors": files["rules/factors.yaml"],
        "composite": files["rules/composite.yaml"],
        "product_fit": files["rules/product_fit.yaml"],
    }


def test_refine_spec_accepts_a_valid_correction(monkeypatch):
    corrected = _valid_spec()
    corrected["gates"][0]["description"] = "corrected description"
    adapter = FakeSpecAdapter([corrected])
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: adapter)

    result = agent_builder.refine_spec(current_rules=_current_rules_text(), feedback="fix the gate description")

    assert result.ok is True
    assert result.spec["gates"][0]["description"] == "corrected description"


def test_refine_spec_never_falls_back_on_invalid_correction(monkeypatch):
    bad_spec = _valid_spec()
    bad_spec["gates"][0]["field"] = "not_declared"
    adapter = FakeSpecAdapter([bad_spec])
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: adapter)

    result = agent_builder.refine_spec(current_rules=_current_rules_text(), feedback="something")

    assert result.ok is False
    assert result.spec is None
    assert result.errors  # the real validation errors, not silently swallowed


def test_refine_spec_reports_llm_outage_without_raising(monkeypatch):
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FailingSpecAdapter())

    result = agent_builder.refine_spec(current_rules=_current_rules_text(), feedback="something")

    assert result.ok is False
    assert result.spec is None


# -- rendering ----

def test_render_agent_yaml_is_draft_and_not_routable():
    text = agent_builder.render_agent_yaml("a1", ["a1"], _valid_spec())
    parsed = yaml.safe_load(text)
    assert parsed["draft"] is True
    assert parsed["routable"] is False
    assert parsed["pipeline"] == [
        "load_input", "gather_evidence", "evaluate_rules", "reason_llm",
        "validate_output", "decide", "hitl_gate", "explain",
    ]
    assert parsed["skills"] == ["a1"]


def test_render_agent_yaml_multi_skill_adds_skills_list_and_load_skills_stage():
    text = agent_builder.render_agent_yaml("a1", ["primary", "secondary"], _valid_spec())
    parsed = yaml.safe_load(text)
    assert parsed["skills"] == ["primary", "secondary"]
    assert parsed["pipeline"] == [
        "load_input", "gather_evidence", "load_skills", "evaluate_rules", "reason_llm",
        "validate_output", "decide", "hitl_gate", "explain",
    ]


def test_render_skill_files_produces_all_four_rule_groups_with_coerced_values():
    files = agent_builder.render_skill_files("a1", _valid_spec())
    assert set(files) >= {
        "skill.yaml", "instructions.md", "output_contract.json",
        "rules/gates.yaml", "rules/factors.yaml", "rules/composite.yaml", "rules/product_fit.yaml",
    }
    gates = yaml.safe_load(files["rules/gates.yaml"])
    assert gates["gates"][0]["value"] == 0.6  # coerced from the string "0.6" to a real float
    composite = yaml.safe_load(files["rules/composite.yaml"])
    assert composite["weights"] == {"risk": 1.0}


# -- full endpoint round trip ----

@pytest.fixture
def tmp_agent_dirs(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills_library"
    agents_dir.mkdir()
    skills_dir.mkdir()
    (skills_dir / "shared").mkdir()
    (skills_dir / "shared" / "compliance_guardrails.md").write_text("Test guardrails.\n", encoding="utf-8")

    monkeypatch.setattr(loader, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(admin, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(admin, "SKILLS_DIR", skills_dir)
    loader._registry.clear()
    return agents_dir, skills_dir


def _stub_single_skill_decompose(monkeypatch):
    """Decomposition stays out of the way for tests that aren't exercising it —
    always reports back exactly one skill so admin.generate_agent's decompose
    call doesn't try to reach a real (and, for the real builder, different)
    Ollama model.
    """
    monkeypatch.setattr(
        agent_builder, "_build_decompose_adapter",
        lambda: FakeSpecAdapter([{"skills": [{"skill_id": "", "description": "d", "scope": "d"}]}]),
    )


def test_generate_agent_endpoint_round_trip(tmp_agent_dirs, monkeypatch):
    _stub_single_skill_decompose(monkeypatch)
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec()]))
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    result = admin.generate_agent(GenerateAgentPayload(agent_id="built1", purpose="qualify on debt ratio"))

    assert result["status"] == "ok"
    assert result["used_fallback"] is False

    bundle = load_agent("built1")
    assert bundle.definition.draft is True
    assert bundle.definition.routable is False

    ctx = invoke_agent("built1", {"evidence": {"debt_ratio": 0.3}})
    assert ctx.error is None
    assert ctx.decision["outcome"] == "QUALIFIED"

    ctx_gated = invoke_agent("built1", {"evidence": {"debt_ratio": 0.9}})
    assert ctx_gated.decision["outcome"] == "NOT_QUALIFIED"


def test_generate_agent_rejects_duplicate_agent_id(tmp_agent_dirs, monkeypatch):
    _stub_single_skill_decompose(monkeypatch)
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec()]))
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())
    admin.generate_agent(GenerateAgentPayload(agent_id="built1", purpose="x"))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        admin.generate_agent(GenerateAgentPayload(agent_id="built1", purpose="x"))
    assert exc_info.value.status_code == 400


def test_generate_agent_endpoint_splits_into_multiple_skills(tmp_agent_dirs, monkeypatch):
    monkeypatch.setattr(agent_builder, "_build_decompose_adapter", lambda: FakeSpecAdapter([_decomposed_two_skills()]))
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec(), _valid_spec()]))
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())

    result = admin.generate_agent(GenerateAgentPayload(agent_id="loans", purpose="two distinct products"))

    assert result["status"] == "ok"
    assert [s["skill_id"] for s in result["skills"]] == ["personal_loans", "business_loans"]

    bundle = load_agent("loans")
    assert bundle.definition.skills == ["personal_loans", "business_loans"]
    assert "load_skills" in bundle.definition.pipeline
    assert set(bundle.skills.keys()) == {"personal_loans", "business_loans"}


# -- /refine endpoint ----

def test_refine_agent_endpoint_applies_correction(tmp_agent_dirs, monkeypatch):
    from backend.admin import RefineAgentPayload

    _stub_single_skill_decompose(monkeypatch)
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec()]))
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())
    admin.generate_agent(GenerateAgentPayload(agent_id="built1", purpose="x"))

    corrected = _valid_spec()
    corrected["gates"][0]["description"] = "now with a fixed description"
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([corrected]))

    result = admin.refine_agent("built1", RefineAgentPayload(feedback="fix the gate description"))

    assert result["status"] == "ok"
    files = admin.get_agent_files("built1")
    gates = yaml.safe_load(files["skills"]["built1"]["rules"]["gates"])
    assert gates["gates"][0]["description"] == "now with a fixed description"

    # still runs and still draft/not-routable — an AI correction isn't auto-promoted to live
    ctx = invoke_agent("built1", {"evidence": {"debt_ratio": 0.3}})
    assert ctx.error is None
    bundle = load_agent("built1", force_reload=True)
    assert bundle.definition.draft is True
    assert bundle.definition.routable is False


def test_refine_agent_rejects_non_draft_agent(tmp_agent_dirs, monkeypatch):
    from backend.admin import RefineAgentPayload

    admin.create_agent(admin.NewAgentPayload(agent_id="a1", purpose="x", template_id="gates_scoring"))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        admin.refine_agent("a1", RefineAgentPayload(feedback="fix something"))
    assert exc_info.value.status_code == 400
    assert "draft" in exc_info.value.detail


def test_refine_agent_leaves_files_untouched_on_invalid_correction(tmp_agent_dirs, monkeypatch):
    from backend.admin import RefineAgentPayload

    _stub_single_skill_decompose(monkeypatch)
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([_valid_spec()]))
    monkeypatch.setattr(pipeline_stages, "_build_adapter", lambda bundle: FakeAdapter())
    admin.generate_agent(GenerateAgentPayload(agent_id="built1", purpose="x"))
    before = admin.get_agent_files("built1")["skills"]["built1"]["rules"]["gates"]

    bad_spec = _valid_spec()
    bad_spec["gates"][0]["field"] = "not_declared"
    monkeypatch.setattr(agent_builder, "_build_adapter", lambda: FakeSpecAdapter([bad_spec]))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        admin.refine_agent("built1", RefineAgentPayload(feedback="something"))
    assert exc_info.value.status_code == 400

    after = admin.get_agent_files("built1")["skills"]["built1"]["rules"]["gates"]
    assert before == after
