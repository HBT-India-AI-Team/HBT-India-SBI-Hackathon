"""The qualification/scoring archetype: evidence -> gates -> weighted-factor
categories -> composite score -> threshold decision -> product-fit
recommendation. This is the original (and, until this module existed, only)
shape the AI "describe it" generator produced — moved here unchanged from
backend/agent_builder.py, now registered as one archetype among others
instead of being the only one.

The critical safety property (preserved from the original module): the LLM
only ever fills the JSON schema below (AGENT_SPEC_SCHEMA). It never writes
YAML text. Python (render_agent_yaml / render_skill_files) deterministically
turns a validated spec into files via yaml.safe_dump for the rules files
specifically — deep LLM-authored strings at multiple nesting levels make
f-string YAML-escaping risk real in a way static placeholder content never
had to worry about.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from .base import Archetype, DEFAULT_MODEL, read_if_exists, register_archetype

_OPERATORS = {"eq", "neq", "gte", "lte", "gt", "lt", "in", "not_in"}
_NUMERIC_OPERATORS = {"gte", "lte", "gt", "lt"}
_ON_FAIL_DECISIONS = {"NOT_QUALIFIED", "NEEDS_HUMAN_REVIEW"}


def _looks_numeric(value: Any) -> bool:
    try:
        float(str(value))
        return True
    except (ValueError, TypeError):
        return False


# -- the schema the LLM fills — see module docstring for why this is the only thing it produces ----

_CONDITION_SCHEMA = {
    "type": "object",
    "required": ["field", "operator", "value"],
    "properties": {
        "field": {"type": "string"},
        "operator": {"type": "string", "enum": sorted(_OPERATORS)},
        "value": {
            "type": "string",
            "description": "Always a plain string, even for numbers/booleans (e.g. \"0.6\", \"true\"). "
                            "For in/not_in, a comma-separated string.",
        },
    },
}

_GATE_SCHEMA = {
    "type": "object",
    "required": ["id", "description", "field", "operator", "value", "on_fail_decision", "on_fail_reason"],
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "field": {"type": "string"},
        "operator": {"type": "string", "enum": sorted(_OPERATORS)},
        "value": {"type": "string"},
        "on_fail_decision": {"type": "string", "enum": sorted(_ON_FAIL_DECISIONS)},
        "on_fail_reason": {"type": "string"},
    },
}

_BAND_SCHEMA = {
    "type": "object",
    "required": ["min", "score"],
    "properties": {"min": {"type": "number"}, "score": {"type": "number"}},
}

_FACTOR_SCHEMA = {
    "type": "object",
    "required": ["id", "description", "field", "weight", "bands"],
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "field": {"type": "string"},
        "weight": {"type": "number"},
        "bands": {"type": "array", "items": _BAND_SCHEMA, "minItems": 2, "maxItems": 4},
    },
}

_CATEGORY_SCHEMA = {
    "type": "object",
    "required": ["name", "factors"],
    "properties": {
        "name": {"type": "string"},
        "factors": {"type": "array", "items": _FACTOR_SCHEMA, "minItems": 1, "maxItems": 4},
    },
}

_COMPOSITE_WEIGHT_SCHEMA = {
    "type": "object",
    "required": ["category", "weight"],
    "properties": {"category": {"type": "string"}, "weight": {"type": "number"}},
}

_PRODUCT_SCHEMA = {
    "type": "object",
    "required": ["id", "name", "reason", "when"],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "reason": {"type": "string"},
        "when": {"type": "array", "items": _CONDITION_SCHEMA, "maxItems": 4},
    },
}

_EVIDENCE_FIELD_SCHEMA = {
    "type": "object",
    "required": ["path", "type", "description"],
    "properties": {
        "path": {"type": "string"},
        "type": {"type": "string", "enum": ["number", "string", "boolean"]},
        "description": {"type": "string"},
    },
}

AGENT_SPEC_SCHEMA = {
    "type": "object",
    "required": ["purpose", "evidence_fields", "gates", "categories", "composite_weights", "thresholds", "products"],
    "properties": {
        "purpose": {"type": "string"},
        "evidence_fields": {"type": "array", "items": _EVIDENCE_FIELD_SCHEMA, "minItems": 1, "maxItems": 8},
        "gates": {"type": "array", "items": _GATE_SCHEMA, "minItems": 1, "maxItems": 5},
        "categories": {"type": "array", "items": _CATEGORY_SCHEMA, "minItems": 1, "maxItems": 4},
        "composite_weights": {"type": "array", "items": _COMPOSITE_WEIGHT_SCHEMA, "minItems": 1, "maxItems": 4},
        "thresholds": {
            "type": "object",
            "required": ["qualified_min", "conditional_min"],
            "properties": {"qualified_min": {"type": "number"}, "conditional_min": {"type": "number"}},
        },
        "products": {"type": "array", "items": _PRODUCT_SCHEMA, "minItems": 1, "maxItems": 5},
    },
}


# -- validation — structural/referential only: guarantees "won't crash", not "is sensible" ----

def validate_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    evidence_fields = spec.get("evidence_fields") or []
    field_paths = [f.get("path") for f in evidence_fields]
    known_fields = set(field_paths)
    if not evidence_fields:
        errors.append("evidence_fields must not be empty")
    if len(field_paths) != len(known_fields):
        errors.append("evidence_fields paths must be unique")

    def _check_field(owner: str, field: Any) -> None:
        if field not in known_fields:
            errors.append(f"{owner} references undeclared field '{field}' — must be one of: {sorted(known_fields)}")

    def _check_condition_value(owner: str, operator: Any, value: Any) -> None:
        # Caught live during testing: a model can pair a numeric operator
        # with a non-numeric value string (e.g. operator "gt", value
        # ">=300" instead of "300") — this parses fine as JSON but raises
        # TypeError at rules-evaluation time (str vs number comparison),
        # so it must be rejected here, not just left to crash a real run.
        if operator in _NUMERIC_OPERATORS and not _looks_numeric(value):
            errors.append(f"{owner} uses operator '{operator}' but value '{value}' is not numeric")

    gates = spec.get("gates") or []
    if not gates:
        errors.append("gates must not be empty")
    gate_ids = [g.get("id") for g in gates]
    if len(gate_ids) != len(set(gate_ids)):
        errors.append("gate ids must be unique")
    for gate in gates:
        _check_field(f"gate '{gate.get('id')}'", gate.get("field"))
        if gate.get("operator") not in _OPERATORS:
            errors.append(f"gate '{gate.get('id')}' has unknown operator '{gate.get('operator')}'")
        else:
            _check_condition_value(f"gate '{gate.get('id')}'", gate.get("operator"), gate.get("value"))
        if gate.get("on_fail_decision") not in _ON_FAIL_DECISIONS:
            errors.append(f"gate '{gate.get('id')}' has invalid on_fail_decision '{gate.get('on_fail_decision')}'")

    categories = spec.get("categories") or []
    if not categories:
        errors.append("categories must not be empty")
    category_names = [c.get("name") for c in categories]
    if len(category_names) != len(set(category_names)):
        errors.append("category names must be unique")
    for cat in categories:
        factors = cat.get("factors") or []
        if not factors:
            errors.append(f"category '{cat.get('name')}' has no factors")
        for factor in factors:
            _check_field(f"factor '{factor.get('id')}' in category '{cat.get('name')}'", factor.get("field"))
            if (factor.get("weight") or 0) <= 0:
                errors.append(f"factor '{factor.get('id')}' must have weight > 0")
            if len(factor.get("bands") or []) < 2:
                errors.append(f"factor '{factor.get('id')}' must have at least 2 bands")

    composite_weights = spec.get("composite_weights") or []
    if not composite_weights:
        errors.append("composite_weights must not be empty")
    weight_categories = [w.get("category") for w in composite_weights]
    if len(weight_categories) != len(set(weight_categories)):
        errors.append("composite_weights category values must be unique")
    cat_name_set, weight_cat_set = set(category_names), set(weight_categories)
    if cat_name_set != weight_cat_set:
        errors.append(
            f"composite_weights categories {sorted(weight_cat_set)} don't match categories "
            f"{sorted(cat_name_set)} (missing: {sorted(cat_name_set - weight_cat_set)}, "
            f"extra: {sorted(weight_cat_set - cat_name_set)})"
        )

    thresholds = spec.get("thresholds") or {}
    qmin, cmin = thresholds.get("qualified_min"), thresholds.get("conditional_min")
    if qmin is None or cmin is None:
        errors.append("thresholds must include both qualified_min and conditional_min")
    elif not (qmin > cmin):
        errors.append(f"thresholds.qualified_min ({qmin}) must be greater than conditional_min ({cmin})")

    products = spec.get("products") or []
    if not products:
        errors.append("products must not be empty")
    product_ids = [p.get("id") for p in products]
    if len(product_ids) != len(set(product_ids)):
        errors.append("product ids must be unique")
    if products and not any((p.get("when") or []) == [] for p in products):
        errors.append("no product has when: [] as a fallback — at least one must always match")
    for product in products:
        for cond in product.get("when") or []:
            owner = f"product '{product.get('id')}' when-condition"
            _check_field(owner, cond.get("field"))
            if cond.get("operator") not in _OPERATORS:
                errors.append(f"{owner} has unknown operator '{cond.get('operator')}'")
            else:
                _check_condition_value(owner, cond.get("operator"), cond.get("value"))

    return errors


def _all_referenced_fields(spec: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for gate in spec.get("gates") or []:
        if gate.get("field"):
            fields.add(gate["field"])
    for category in spec.get("categories") or []:
        for factor in category.get("factors") or []:
            if factor.get("field"):
                fields.add(factor["field"])
    for product in spec.get("products") or []:
        for cond in product.get("when") or []:
            if cond.get("field"):
                fields.add(cond["field"])
    return fields


def _infer_field_type(spec: dict[str, Any], field: str) -> str:
    for category in spec.get("categories") or []:
        for factor in category.get("factors") or []:
            if factor.get("field") == field:
                return "number"
    for gate in spec.get("gates") or []:
        if gate.get("field") == field:
            value = str(gate.get("value", "")).strip().lower()
            if value in ("true", "false"):
                return "boolean"
            if _looks_numeric(value):
                return "number"
            return "string"
    return "string"


def auto_repair(spec: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Only fixes the one thing that's safe to patch without judgment about
    intent: a missing when:[] fallback product. Everything else (undeclared
    fields, category/weight mismatches) needs the LLM to see its own
    mistake and reconsider, not a silent Python patch — an undeclared field
    could just as easily be a typo/invented name as a legitimately dropped
    one, and this function has no prior context to tell those apart.
    """
    spec = copy.deepcopy(spec)
    products = spec.get("products") or []
    if products and not any((p.get("when") or []) == [] for p in products):
        products.append({
            "id": "GENERAL_REVIEW", "name": "General Review",
            "reason": "No specific product criteria matched", "when": [],
        })
        spec["products"] = products
    return spec


def reconcile_evidence_fields(spec: dict[str, Any], current_content: dict[str, str]) -> dict[str, Any]:
    """Refine-only repair, deliberately separate from auto_repair (which
    also runs during fresh generation, where there's no prior context to
    safely trust): re-adds an evidence field to `spec` only if it's both
    (a) still referenced by a gate/factor/product in `spec` itself, AND
    (b) was ALREADY genuinely in use in `current_content` — the rules as
    they stood *before* this edit. Condition (b) is what makes this safe
    where a blanket "restore anything referenced" repair isn't: a target-
    section-scoped refine asked to focus on one part sometimes drops an
    unrelated, previously-valid field from evidence_fields while still
    referencing it elsewhere in its own output (seen in practice); a
    brand-new typo'd/invented field was never in current_content and stays
    unrepaired, same as auto_repair's existing behavior.
    """
    spec = copy.deepcopy(spec)
    previously_valid = _referenced_fields(current_content)
    if not previously_valid:
        return spec

    evidence_fields = spec.get("evidence_fields") or []
    declared = {f.get("path") for f in evidence_fields}
    missing = (_all_referenced_fields(spec) - declared) & set(previously_valid)
    for field in sorted(missing):
        evidence_fields.append({
            "path": field,
            "type": _infer_field_type(spec, field),
            "description": "Restored automatically — still referenced by existing rules after this edit.",
        })
    if missing:
        spec["evidence_fields"] = evidence_fields
    return spec


def fallback_spec(purpose: str, agent_id: str) -> dict[str, Any]:
    """Deterministic, no LLM call, always passes validate_spec by
    construction — same shape as agent_templates.py's gates_scoring
    placeholder content, so the worst case is never worse than today's
    known-safe template floor.
    """
    return {
        "purpose": purpose.strip() or f"Generated agent {agent_id} — description unavailable.",
        "evidence_fields": [
            {"path": "flagged", "type": "boolean", "description": "Placeholder — replace with a real field."},
            {"path": "score", "type": "number", "description": "Placeholder — replace with a real field."},
        ],
        "gates": [{
            "id": "EXAMPLE_GATE",
            "description": "Placeholder — delete or edit, then add your own gates",
            "field": "flagged", "operator": "neq", "value": "true",
            "on_fail_decision": "NOT_QUALIFIED", "on_fail_reason": "Example gate: evidence.flagged was true",
        }],
        "categories": [{
            "name": "overall",
            "factors": [{
                "id": "EXAMPLE_SCORE", "description": "Placeholder — replace field/weight/bands with real ones",
                "field": "score", "weight": 1.0,
                "bands": [{"min": 80, "score": 100}, {"min": 50, "score": 60}, {"min": 0, "score": 30}],
            }],
        }],
        "composite_weights": [{"category": "overall", "weight": 1.0}],
        "thresholds": {"qualified_min": 75, "conditional_min": 50},
        "products": [{
            "id": "EXAMPLE_PRODUCT", "name": "Example Product",
            "reason": "Placeholder fallback — always matches until you add when: conditions", "when": [],
        }],
    }


# -- prompts ----

def _system_prompt() -> str:
    return (
        "You design deterministic qualification rules for a banking agent platform, given a plain-"
        "language description of what it should evaluate. Output ONLY the structured fields requested "
        "— no YAML, no code, no commentary.\n\n"
        "Rules:\n"
        "- Prefer simple, flat evidence_fields paths that match how a caller would naturally submit "
        "the value directly (e.g. \"credit_score\", not \"applicant.credit_score.value\") — a nested path "
        "only makes sense if the description clearly implies a nested object.\n"
        "- Every 'field' you reference in gates, factors, or product when-conditions MUST be one of the "
        "paths you declared in evidence_fields.\n"
        "- 'categories[].name' values must exactly match the 'category' values you use in "
        "composite_weights — these are the same set of names, just referenced from two places.\n"
        "- 'value' is always a plain string, even for numbers/booleans (e.g. \"0.6\", \"true\", "
        "\"VERIFIED\"). For 'in'/'not_in' operators, use a comma-separated string (e.g. \"US,UK,CA\").\n"
        "- Never use a calendar date/timestamp as a gate or product condition value — gt/gte/lt/lte only "
        "support comparing plain numbers, not dates, and a date string will be rejected. If the "
        "description implies a duration or age (e.g. \"incorporated for more than 2 years\", \"launched "
        "over 6 months ago\", \"more than 5 years of experience\"), declare a plain NUMBER evidence field "
        "for that duration directly (e.g. \"years_in_business\", \"months_since_launch\") and compare that "
        "number with gt/gte/lt/lte — never a date field compared against a computed cutoff date.\n"
        "- Include at least one product with an empty when: [] list as a fallback that always matches.\n"
        "- Keep it small and focused: 1-5 gates, 1-4 categories with 1-4 factors each, 1-5 products."
    )


def _refine_system_prompt() -> str:
    return (
        _system_prompt()
        + "\n\nYou are CORRECTING an existing rule set based on a human's feedback about what's wrong "
          "with it, not designing one from scratch. Preserve anything the feedback doesn't mention as a "
          "problem — don't invent unrelated changes. Output the full corrected spec, not a partial patch."
    )


def _build_user_prompt(purpose: str) -> str:
    return f"Description: {purpose}"


def _referenced_fields(current_rules: dict[str, str]) -> list[str]:
    """Every evidence field name currently referenced by any gate, factor,
    or product condition — parsed straight from the current rule YAML, not
    left for the model to notice on its own. Used to hand the refine
    prompt a concrete checklist: past testing showed a model asked to
    "keep every currently-used field" in prose alone would still silently
    drop ones outside the section it was focused on.
    """
    fields: set[str] = set()

    gates_doc = yaml.safe_load(current_rules.get("gates", "")) or {}
    for gate in gates_doc.get("gates", []) or []:
        if gate.get("field"):
            fields.add(gate["field"])

    factors_doc = yaml.safe_load(current_rules.get("factors", "")) or {}
    for category in (factors_doc.get("categories") or {}).values():
        for factor in category.get("factors", []) or []:
            if factor.get("field"):
                fields.add(factor["field"])

    product_fit_doc = yaml.safe_load(current_rules.get("product_fit", "")) or {}
    for product in product_fit_doc.get("products", []) or []:
        for cond in product.get("when", []) or []:
            if cond.get("field"):
                fields.add(cond["field"])

    return sorted(fields)


# Friendly names for current_rules' keys (rule-group names) — shown to the model when a
# correction is scoped to just one of them via target_section.
_SECTION_LABELS = {
    "gates": "gates (hard pass/fail eligibility checks)",
    "factors": "factors (the weighted scoring bands)",
    "composite": "composite scoring and thresholds (category weights, the Accepted/Review/Rejected cutoffs)",
    "product_fit": "product fit (which product/track an eligible profile is routed to)",
}


def _refine_user_prompt(current_rules: dict[str, str], feedback: str, *, skill_id: str, skill_description: str,
                         target_section: str | None = None) -> str:
    current_yaml = "\n\n".join(
        f"--- rules/{name}.yaml ---\n{text}" for name, text in current_rules.items() if text
    )
    # A multi-skill agent gets refined one skill at a time, all sharing one
    # human-written feedback string that may talk about several skills at
    # once. Two skills can start from byte-identical placeholder rules with
    # nothing else to tell them apart, so without naming which skill this
    # call is actually correcting, the model has no way to know which part
    # of the feedback applies here and can bleed unrelated rules across
    # skills (e.g. a GST-specific gate landing on the GeM skill).
    scope_note = ""
    if target_section:
        label = _SECTION_LABELS.get(target_section, target_section)
        scope_note = (
            f"\n\nThis feedback is specifically about the {label} part of these rules. Your output is the "
            f"FULL spec (every evidence field, every gate, every category, every product) — you are NOT "
            f"just outputting the {label} part, you are outputting everything, with only that one part "
            f"changed. Copy every gate, every category (all of them, not just the one(s) related to this "
            f"feedback), and every product through byte-for-byte unchanged from the current rules above, "
            f"UNLESS your change genuinely requires a matching change elsewhere to stay internally "
            f"consistent (e.g. adding a new scoring category means it also needs a composite weight, or "
            f"renaming a field used elsewhere). Do not delete, merge, or omit anything you weren't asked "
            f"to change, and don't make unrelated improvements to other rule groups just because you can "
            f"see them.\n\n"
        )
        referenced_fields = _referenced_fields(current_rules)
        if referenced_fields:
            field_list = ", ".join(referenced_fields)
            scope_note += (
                f"Your output's evidence_fields list MUST include every one of these {len(referenced_fields)} "
                f"fields (they are referenced by the current rules, including parts you are not changing), "
                f"plus any new field your own change introduces: {field_list}. Do not drop any of them, "
                f"even ones unrelated to this feedback."
            )
    return (
        f"You are correcting the rules for the '{skill_id}' skill ({skill_description}).\n\n"
        f"Current rules:\n{current_yaml}\n\n"
        f"A human reviewed the whole agent and said: {feedback}{scope_note}\n\n"
        f"Only apply the parts of this feedback that are relevant to '{skill_id}'. "
        f"Ignore anything that clearly belongs to a different skill."
    )


# -- rendering: spec -> files. yaml.safe_dump on plain dicts/lists, never f-string YAML ----

def render_agent_yaml(agent_id: str, skill_ids: list[str], spec: dict[str, Any]) -> str:
    purpose = (spec.get("purpose") or "").strip() or "Generated agent — describe further via editor."
    # Each evidence field as its own JSON-schema property (not flattened into one
    # description string) so the admin UI's Playground can render a real field-by-field
    # form for the nested `evidence` object instead of one opaque JSON box.
    evidence_properties = {
        f["path"]: {"type": f["type"], "description": f["description"]}
        for f in spec.get("evidence_fields", [])
    }

    # load_skills is a no-op stage for single-skill agents (see skill_selection.py) but only
    # needed in the pipeline at all once there's more than one candidate skill to choose between.
    pipeline = ["load_input", "gather_evidence"]
    if len(skill_ids) > 1:
        pipeline.append("load_skills")
    pipeline += ["evaluate_rules", "reason_llm", "validate_output", "decide", "hitl_gate", "explain"]

    # Every LLM-authored string here (purpose, evidence descriptions) goes into the
    # doc as a plain Python value and is escaped by yaml.safe_dump — never spliced
    # into YAML text directly, so it can't break structure or inject sibling keys.
    doc = {
        "agent_id": agent_id,
        "version": "1.0.0",
        "purpose": purpose,
        "skills": list(skill_ids),
        "pipeline": pipeline,
        "capabilities": [],
        "governance": {
            "hitl_conditions": ["low_confidence", "validation_degraded"],
            "confidence_threshold": 0.6,
            "max_llm_retries": 1,
        },
        "llm": {"model": DEFAULT_MODEL, "temperature": 0.0, "seed": 7, "timeout_seconds": 120},
        "draft": True,
        "routable": False,
        "input_schema": {
            "type": "object",
            "required": [],
            "properties": {
                "evidence": {
                    "type": "object",
                    "description": "Evidence dict evaluated against this agent's generated rules.",
                    "properties": evidence_properties,
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["outcome", "reason", "composite_score"],
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["QUALIFIED", "CONDITIONALLY_QUALIFIED", "NEEDS_HUMAN_REVIEW", "NOT_QUALIFIED"],
                },
                "reason": {"type": "string"},
                "composite_score": {"type": "number"},
            },
        },
    }
    rendered = yaml.safe_dump(doc, sort_keys=False)
    yaml.safe_load(rendered)  # parse-and-validate before this is ever written to disk
    return rendered


def _coerce_condition_value(raw_value: str, operator: str, field: str, field_types: dict[str, str]) -> Any:
    if operator in ("in", "not_in"):
        return [v.strip() for v in str(raw_value).split(",") if v.strip()]
    field_type = field_types.get(field, "string")
    text = str(raw_value)
    if field_type == "number":
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            return text
    if field_type == "boolean":
        return text.strip().lower() == "true"
    return text


def render_skill_files(skill_id: str, spec: dict[str, Any]) -> dict[str, str]:
    purpose = (spec.get("purpose") or "").strip() or "Generated skill."
    purpose_inline = " ".join(purpose.split())
    field_types = {f["path"]: f.get("type", "string") for f in spec.get("evidence_fields", [])}

    def _condition(cond: dict[str, Any]) -> dict[str, Any]:
        return {
            "field": cond["field"],
            "operator": cond["operator"],
            "value": _coerce_condition_value(cond["value"], cond["operator"], cond["field"], field_types),
        }

    generated_note = "Generated by Agent Builder from a plain-language description — review before trusting."

    gates_doc = {
        "rule_group": "gates",
        "description": generated_note,
        "gates": [
            {
                "id": g["id"], "description": g["description"], "field": g["field"], "operator": g["operator"],
                "value": _coerce_condition_value(g["value"], g["operator"], g["field"], field_types),
                "on_fail": {"decision": g["on_fail_decision"], "reason": g["on_fail_reason"]},
            }
            for g in spec["gates"]
        ],
    }

    factors_doc = {
        "rule_group": "factors",
        "description": generated_note,
        "categories": {
            cat["name"]: {
                "factors": [
                    {
                        "id": f["id"], "description": f["description"], "field": f["field"], "weight": f["weight"],
                        "bands": [{"min": b["min"], "score": b["score"]} for b in f["bands"]],
                    }
                    for f in cat["factors"]
                ]
            }
            for cat in spec["categories"]
        },
    }

    composite_doc = {
        "rule_group": "composite",
        "description": generated_note,
        "weights": {w["category"]: w["weight"] for w in spec["composite_weights"]},
        "thresholds": {
            "qualified_min": spec["thresholds"]["qualified_min"],
            "conditional_min": spec["thresholds"]["conditional_min"],
        },
    }

    product_fit_doc = {
        "rule_group": "product_fit",
        "description": generated_note,
        "products": [
            {
                "id": p["id"], "name": p["name"], "reason": p["reason"],
                "when": [_condition(c) for c in p.get("when", [])],
            }
            for p in spec["products"]
        ],
    }

    output_contract = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-3 sentence overview"},
            "strengths": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"point": {"type": "string"}, "evidence_key": {"type": "string"}},
                    "required": ["point", "evidence_key"],
                },
            },
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"point": {"type": "string"}, "evidence_key": {"type": "string"}},
                    "required": ["point", "evidence_key"],
                },
            },
            "next_best_action": {"type": "string"},
            "product_rationale": {"type": "object", "additionalProperties": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["summary", "strengths", "risks", "next_best_action", "product_rationale", "confidence"],
    }

    instructions_md = (
        f"# {skill_id}\n\n"
        "You are producing a qualitative narrative for a decision that has ALREADY been made by "
        "deterministic rules (see rules/*.yaml) — you do not decide anything yourself.\n\n"
        "Use ONLY the facts under `evidence` and `facts` in the prompt payload. For every point in "
        "`strengths` and `risks`, set `evidence_key` to one of the exact strings listed in "
        "`allowed_citation_keys` — never invent a key.\n\n"
        "Do not output `decision`, `outcome`, `qualified`, `score`, or any other decision-bearing field "
        "— those are computed separately and will be stripped from your output if you include them.\n\n"
        f"---\n_Generated by Agent Builder from: {purpose_inline}_\n"
    )

    skill_doc = {
        "skill_id": skill_id,
        "version": "1.0.0",
        "kind": "deterministic",
        "archetype": "qualification",
        "description": purpose,
        "instructions": "instructions.md",
        "output_contract": "output_contract.json",
        "shared_includes": ["shared/compliance_guardrails.md"],
        "rules": {
            "gates": "rules/gates.yaml",
            "factors": "rules/factors.yaml",
            "composite": "rules/composite.yaml",
            "product_fit": "rules/product_fit.yaml",
        },
    }
    skill_yaml = yaml.safe_dump(skill_doc, sort_keys=False)
    yaml.safe_load(skill_yaml)  # parse-and-validate before this is ever written to disk

    return {
        "skill.yaml": skill_yaml,
        "instructions.md": instructions_md,
        "output_contract.json": json.dumps(output_contract, indent=2),
        "rules/gates.yaml": yaml.safe_dump(gates_doc, sort_keys=False),
        "rules/factors.yaml": yaml.safe_dump(factors_doc, sort_keys=False),
        "rules/composite.yaml": yaml.safe_dump(composite_doc, sort_keys=False),
        "rules/product_fit.yaml": yaml.safe_dump(product_fit_doc, sort_keys=False),
    }


def _read_refine_context(skill_dir: Path) -> dict[str, str]:
    """Rebuilds the same {rule_group_name: yaml_text} shape _refine_user_prompt
    expects, reading directly off disk via skill.yaml's own rules: mapping —
    mirrors backend/admin.py's _read_skill_files without depending on it, so
    this module has no dependency on the admin package.
    """
    manifest_text = read_if_exists(skill_dir / "skill.yaml")
    manifest = yaml.safe_load(manifest_text) or {} if manifest_text else {}
    return {
        rule_name: read_if_exists(skill_dir / rel_path)
        for rule_name, rel_path in (manifest.get("rules") or {}).items()
    }


register_archetype(Archetype(
    id="qualification",
    label="Qualification / Scoring",
    description=(
        "Evidence -> hard eligibility gates -> weighted category scoring -> "
        "composite-threshold decision -> product-fit recommendation. Use "
        "this for anything that qualifies, screens, or scores an applicant "
        "against explicit criteria."
    ),
    spec_schema=AGENT_SPEC_SCHEMA,
    build_system_prompt=_system_prompt,
    build_refine_system_prompt=_refine_system_prompt,
    build_user_prompt=_build_user_prompt,
    build_refine_user_prompt=_refine_user_prompt,
    validate=validate_spec,
    auto_repair=auto_repair,
    fallback_spec=fallback_spec,
    render_agent_yaml=render_agent_yaml,
    render_skill_files=render_skill_files,
    merge_field="evidence_fields",
    read_refine_context=_read_refine_context,
    refine_write_keys=["rules/gates.yaml", "rules/factors.yaml", "rules/composite.yaml", "rules/product_fit.yaml"],
    repair_with_context=reconcile_evidence_fields,
))
