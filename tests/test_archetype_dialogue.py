"""Tests for backend/archetypes/dialogue.py — the plain-text, multi-voice
archetype. Covers validate_spec's checks, the always-valid fallback, and
that rendering produces a real load_input -> load_skills -> reason_llm_text
-> validate_text_output -> hitl_gate -> explain agent with a skill_id enum
covering every voice, and no per-field required flags leaking into the
agent-level input_schema (each voice only needs its own fields).
"""
import yaml

from backend.archetypes import dialogue


def _valid_spec() -> dict:
    return {
        "purpose": "The narrator voice: rephrases a given game-mechanic fact.",
        "input_fields": [
            {"path": "fact", "type": "string", "description": "The exact fact to rephrase.", "required": True},
        ],
        "guidance": "Restate the fact in different words. Same meaning, no new details, one to two sentences.",
    }


def test_valid_spec_has_no_errors():
    assert dialogue.validate_spec(_valid_spec()) == []


def test_fallback_spec_always_valid():
    spec = dialogue.fallback_spec("purpose text", "agent_id")
    assert dialogue.validate_spec(spec) == []


def test_empty_input_fields_is_rejected():
    spec = _valid_spec()
    spec["input_fields"] = []
    errors = dialogue.validate_spec(spec)
    assert any("input_fields must not be empty" in e for e in errors)


def test_duplicate_input_field_paths_is_rejected():
    spec = _valid_spec()
    spec["input_fields"] = spec["input_fields"] * 2
    errors = dialogue.validate_spec(spec)
    assert any("input_fields paths must be unique" in e for e in errors)


def test_invalid_field_type_is_rejected():
    spec = _valid_spec()
    spec["input_fields"][0]["type"] = "array"
    errors = dialogue.validate_spec(spec)
    assert any("invalid type" in e for e in errors)


def test_empty_guidance_is_rejected():
    spec = _valid_spec()
    spec["guidance"] = "   "
    errors = dialogue.validate_spec(spec)
    assert any("guidance must not be empty" in e for e in errors)


def test_auto_repair_is_a_no_op():
    spec = _valid_spec()
    errors = ["some error"]
    assert dialogue.auto_repair(spec, errors) == spec


# -- rendering ----

def test_render_agent_yaml_produces_the_text_mode_pipeline():
    text = dialogue.render_agent_yaml("moneyverse", ["narrator", "npc"], _valid_spec())
    parsed = yaml.safe_load(text)

    assert parsed["pipeline"] == [
        "load_input", "load_skills", "reason_llm_text", "validate_text_output", "hitl_gate", "explain",
    ]
    assert parsed["capabilities"] == []
    assert parsed["draft"] is True
    assert parsed["routable"] is False


def test_render_agent_yaml_skill_id_enum_covers_every_voice():
    parsed = yaml.safe_load(dialogue.render_agent_yaml("moneyverse", ["narrator", "npc"], _valid_spec()))
    assert parsed["input_schema"]["properties"]["skill_id"]["enum"] == ["narrator", "npc"]
    assert parsed["input_schema"]["required"] == ["skill_id"]


def test_render_agent_yaml_never_requires_a_voice_specific_field():
    # A field belonging to one voice must not become globally required —
    # a request for a different voice that never uses it must not be rejected.
    spec = _valid_spec()
    spec["input_fields"][0]["required"] = True
    parsed = yaml.safe_load(dialogue.render_agent_yaml("moneyverse", ["narrator", "npc"], spec))
    assert parsed["input_schema"]["required"] == ["skill_id"]
    assert "fact" in parsed["input_schema"]["properties"]


def test_render_agent_yaml_output_schema_is_plain_text_only():
    parsed = yaml.safe_load(dialogue.render_agent_yaml("moneyverse", ["narrator"], _valid_spec()))
    assert parsed["output_schema"]["required"] == ["text"]
    assert set(parsed["output_schema"]["properties"]) == {"text"}
    assert parsed["output_schema"]["properties"]["text"]["type"] == "string"


def test_render_skill_files_has_no_rules_and_no_output_contract():
    files = dialogue.render_skill_files("narrator", _valid_spec())
    assert set(files.keys()) == {"skill.yaml", "instructions.md"}

    manifest = yaml.safe_load(files["skill.yaml"])
    assert manifest["archetype"] == "dialogue"
    assert not manifest.get("rules")


def test_render_skill_files_instructions_include_guidance_text():
    files = dialogue.render_skill_files("narrator", _valid_spec())
    assert "Restate the fact in different words." in files["instructions.md"]
    assert "never JSON" in files["instructions.md"]
