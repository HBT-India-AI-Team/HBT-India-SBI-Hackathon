"""Tests for backend/archetypes/conversational.py — the no-rules, no-gates
advisor archetype. Covers validate_spec's checks, the always-valid fallback,
and that rendering produces a real load_input -> reason_llm ->
validate_output -> explain agent with no rules/*.yaml files, parseable by
the same loader every other agent goes through.
"""
import json

import yaml

from backend.archetypes import conversational


def _valid_spec() -> dict:
    return {
        "purpose": "Answer employee questions about the leave policy.",
        "input_fields": [
            {"path": "question", "type": "string", "description": "The employee's question.", "required": True},
        ],
        "output_fields": [
            {"path": "answer", "type": "string", "description": "The answer."},
        ],
        "guidance": "Answer directly using only the provided policy text.",
    }


def test_valid_spec_has_no_errors():
    assert conversational.validate_spec(_valid_spec()) == []


def test_fallback_spec_always_valid():
    spec = conversational.fallback_spec("purpose text", "agent_id")
    assert conversational.validate_spec(spec) == []


def test_empty_input_fields_is_rejected():
    spec = _valid_spec()
    spec["input_fields"] = []
    errors = conversational.validate_spec(spec)
    assert any("input_fields must not be empty" in e for e in errors)


def test_duplicate_input_field_paths_is_rejected():
    spec = _valid_spec()
    spec["input_fields"] = spec["input_fields"] * 2
    errors = conversational.validate_spec(spec)
    assert any("input_fields paths must be unique" in e for e in errors)


def test_invalid_field_type_is_rejected():
    spec = _valid_spec()
    spec["output_fields"][0]["type"] = "array"
    errors = conversational.validate_spec(spec)
    assert any("invalid type" in e for e in errors)


def test_empty_guidance_is_rejected():
    spec = _valid_spec()
    spec["guidance"] = "   "
    errors = conversational.validate_spec(spec)
    assert any("guidance must not be empty" in e for e in errors)


def test_auto_repair_is_a_no_op():
    spec = _valid_spec()
    errors = ["some error"]
    assert conversational.auto_repair(spec, errors) == spec


# -- rendering ----

def test_render_agent_yaml_produces_the_fixed_narrator_pipeline():
    text = conversational.render_agent_yaml("advisor1", ["advisor1"], _valid_spec())
    parsed = yaml.safe_load(text)

    assert parsed["pipeline"] == ["load_input", "reason_llm", "validate_output", "explain"]
    assert parsed["capabilities"] == []
    assert parsed["draft"] is True
    assert parsed["routable"] is False


def test_render_agent_yaml_input_schema_respects_required_flags():
    spec = _valid_spec()
    spec["input_fields"].append(
        {"path": "employee_id", "type": "string", "description": "optional context", "required": False},
    )
    parsed = yaml.safe_load(conversational.render_agent_yaml("advisor1", ["advisor1"], spec))

    assert parsed["input_schema"]["required"] == ["question"]
    assert set(parsed["input_schema"]["properties"]) == {"question", "employee_id"}


def test_render_agent_yaml_output_schema_includes_confidence():
    parsed = yaml.safe_load(conversational.render_agent_yaml("advisor1", ["advisor1"], _valid_spec()))
    assert set(parsed["output_schema"]["required"]) == {"answer", "confidence"}
    assert parsed["output_schema"]["properties"]["confidence"]["type"] == "number"


def test_render_skill_files_has_no_rules():
    files = conversational.render_skill_files("advisor1", _valid_spec())
    assert set(files.keys()) == {"skill.yaml", "instructions.md", "output_contract.json"}

    manifest = yaml.safe_load(files["skill.yaml"])
    assert manifest["archetype"] == "conversational"
    assert not manifest.get("rules")  # empty dict/None both count as "no rules" once loaded


def test_render_skill_files_output_contract_matches_output_fields():
    files = conversational.render_skill_files("advisor1", _valid_spec())
    contract = json.loads(files["output_contract.json"])
    assert "answer" in contract["properties"]
    assert "confidence" in contract["properties"]
    assert set(contract["required"]) == {"answer", "confidence"}


def test_render_skill_files_instructions_include_guidance_text():
    files = conversational.render_skill_files("advisor1", _valid_spec())
    assert "Answer directly using only the provided policy text." in files["instructions.md"]
