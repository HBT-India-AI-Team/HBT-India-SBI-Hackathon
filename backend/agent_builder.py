"""Agent Builder: turns a plain-language description into a real,
gates_scoring-shaped agent — full custom gates/factors/thresholds/products,
not a template with placeholders filled in.

The critical safety property: the LLM only ever fills a JSON schema (see
AGENT_SPEC_SCHEMA). It never writes YAML text. Python (render_agent_yaml /
render_skill_files) is what deterministically turns a validated spec into
files, using yaml.safe_dump for the rules files specifically (deep
LLM-authored strings at multiple nesting levels make f-string YAML-escaping
risk real in a way agent_templates.py's static placeholder content never
had to worry about).

generate_spec() never raises for a bad LLM output — it always returns a
GeneratedSpec, falling all the way back to a deterministic, always-valid
spec (the same shape as agent_templates.py's gates_scoring placeholder
content) if generation can't be made internally consistent. Every agent
this module produces is marked draft: true, routable: false — the
validation here guarantees "won't crash the pipeline," not "the thresholds
are sensible," so nothing it writes is ever trusted un-reviewed.
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

import yaml

from agent_platform.llm import OllamaAdapter, OllamaError

# Both point at gemma4:12b — the model actually present on OLLAMA_HOST (granite4.1:3b and
# qwen2.5-coder:14b were only ever tested against a developer's own local Ollama, not the real
# configured server; qwen 503'd there entirely). gemma4:12b is also what every hand-built demo
# agent already uses, and live-tested clean: zero validate_spec errors on both the split decision
# and full rule generation, in ~20-30s per call. Kept as two separate constants/adapter builders
# (even though they're equal today) so either can be swapped independently later.
_BUILDER_MODEL = "gemma4:12b"
_DECOMPOSE_MODEL = "gemma4:12b"
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


def auto_repair(spec: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Only fixes the one thing that's safe to patch without judgment about
    intent: a missing when:[] fallback product. Everything else (undeclared
    fields, category/weight mismatches) needs the LLM to see its own
    mistake and reconsider, not a silent Python patch.
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


def fallback_blank_spec(purpose: str, agent_id: str) -> dict[str, Any]:
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


# -- generation ----

# The wrapper's own read timeout for /ollama is 300s (raised from a flat 30s
# that was cutting off large structured-generation calls early). Ours must
# exceed that, or we'll time out client-side first and report our own
# generic error instead of whatever the wrapper/Ollama actually did.
_BUILDER_TIMEOUT_SECONDS = 320


def _build_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OllamaAdapter(host=host, model=_BUILDER_MODEL, timeout_seconds=_BUILDER_TIMEOUT_SECONDS, seed=7)


def _build_decompose_adapter() -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return OllamaAdapter(host=host, model=_DECOMPOSE_MODEL, timeout_seconds=_BUILDER_TIMEOUT_SECONDS, seed=7)


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


@dataclass(frozen=True)
class GeneratedSpec:
    spec: dict[str, Any]
    attempts: int
    used_fallback: bool


def generate_spec(*, purpose: str, agent_id: str) -> GeneratedSpec:
    """One generation attempt, one cheap mechanical auto-repair if needed —
    no automatic LLM re-prompt. If it's still not valid after that, falls
    back to the safe placeholder spec rather than silently retrying the
    LLM again with no human visibility into what happened. A human
    reviewing a fallback-shaped draft is expected to either edit the YAML
    directly or use refine_spec() to describe what they actually want.
    """
    adapter = _build_adapter()
    errors: list[str] = []
    spec: dict[str, Any] = {}
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=_system_prompt(),
            user_prompt=f"Description: {purpose}",
            schema=AGENT_SPEC_SCHEMA,
            temperature=0.2,
        )
        spec = parsed
        errors = validate_spec(spec)
        if errors:
            spec = auto_repair(spec, errors)
            errors = validate_spec(spec)
    except OllamaError:
        errors = errors or ["LLM call failed"]

    if errors:
        return GeneratedSpec(spec=fallback_blank_spec(purpose, agent_id), attempts=1, used_fallback=True)
    return GeneratedSpec(spec=spec, attempts=1, used_fallback=False)


# -- multi-skill decomposition: does this purpose need more than one rule set? ----

_DECOMPOSE_SCHEMA = {
    "type": "object",
    "required": ["skills"],
    "properties": {
        "skills": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["skill_id", "description", "scope"],
                "properties": {
                    "skill_id": {"type": "string", "description": "short, snake_case, unique"},
                    "description": {
                        "type": "string",
                        "description": "One sentence a routing model will use to pick this skill over the others.",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Self-contained description of just this skill's own rules — written "
                                       "as if it were the entire purpose for this one skill.",
                    },
                },
            },
        },
    },
}


def _decompose_system_prompt() -> str:
    return (
        "You are deciding how many distinct rule sets (\"skills\") an agent needs to cover a "
        "plain-language description. Most requests should stay as ONE skill — only split into "
        "multiple when the description clearly describes genuinely distinct evaluation paths that "
        "don't share one gate/scoring/product structure (e.g. different rules for different "
        "applicant types, request types, or products). Output ONLY the structured fields requested "
        "— no YAML, no code, no commentary.\n\n"
        "Rules:\n"
        "- If in doubt, prefer ONE skill.\n"
        "- Each skill_id must be short, snake_case, and distinct from the others.\n"
        "- Each description is one sentence used later by a routing model to pick between skills at "
        "runtime — make it clearly distinguish this skill from the others.\n"
        "- Each scope must be a self-contained description of just that skill's own rules — write it "
        "as if it were the entire purpose for that one skill, since it will be used standalone, "
        "without the other skills' context, to generate that skill's rules."
    )


def decompose_purpose(purpose: str) -> list[dict[str, str]]:
    """Returns [{"skill_id", "description", "scope"}, ...], always length >= 1.
    On any LLM outage or malformed/empty response, returns a single-item
    list describing the whole purpose as one skill — decomposition failure
    must never block generation, only skip the split.
    """
    single_skill_fallback = [{"skill_id": "", "description": purpose.strip()[:200], "scope": purpose}]
    adapter = _build_decompose_adapter()
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=_decompose_system_prompt(),
            user_prompt=f"Description: {purpose}",
            schema=_DECOMPOSE_SCHEMA,
            temperature=0.0,
        )
    except OllamaError:
        return single_skill_fallback

    skills = parsed.get("skills") if isinstance(parsed, dict) else None
    if not isinstance(skills, list) or not skills:
        return single_skill_fallback

    cleaned = [
        item for item in skills
        if isinstance(item, dict) and (item.get("scope") or "").strip()
    ]
    return cleaned or single_skill_fallback


def _sanitize_skill_id(raw: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-z0-9_]", "_", (raw or "").strip().lower()).strip("_")
    return candidate or fallback


@dataclass(frozen=True)
class GeneratedSkill:
    skill_id: str
    description: str
    result: GeneratedSpec


@dataclass(frozen=True)
class MultiSkillGeneration:
    skills: list[GeneratedSkill]
    split: bool


def generate_agent_skills(
    *, purpose: str, agent_id: str, on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> MultiSkillGeneration:
    """Decides (via decompose_purpose) whether this description needs one
    skill or several, then generates each one independently through the
    same proven generate_spec pipeline — never asks the model to author
    multiple full rule sets in a single call.

    on_progress, if given, is called with a small event dict before/after
    each LLM call so a caller (e.g. an SSE endpoint) can report real
    progress instead of guessing at timing.
    """
    def _report(event: dict[str, Any]) -> None:
        if on_progress is not None:
            on_progress(event)

    _report({"step": "decompose", "status": "start"})
    decomposed = decompose_purpose(purpose)
    _report({"step": "decompose", "status": "done", "count": len(decomposed)})

    if len(decomposed) <= 1:
        _report({"step": "generate_skill", "status": "start", "skill_id": agent_id, "index": 1, "total": 1})
        spec_result = generate_spec(purpose=purpose, agent_id=agent_id)
        description = purpose.strip()[:200] or agent_id
        _report({
            "step": "generate_skill", "status": "done", "skill_id": agent_id,
            "index": 1, "total": 1, "used_fallback": spec_result.used_fallback,
        })
        return MultiSkillGeneration(
            skills=[GeneratedSkill(skill_id=agent_id, description=description, result=spec_result)],
            split=False,
        )

    taken: set[str] = set()
    skills: list[GeneratedSkill] = []
    total = len(decomposed)
    for idx, item in enumerate(decomposed, start=1):
        skill_id = _sanitize_skill_id(item.get("skill_id", ""), f"{agent_id}_skill_{idx}")
        base_id = skill_id
        suffix = 2
        while skill_id in taken:
            skill_id = f"{base_id}_{suffix}"
            suffix += 1
        taken.add(skill_id)

        description = (item.get("description") or "").strip() or skill_id
        scope = (item.get("scope") or "").strip() or purpose
        _report({"step": "generate_skill", "status": "start", "skill_id": skill_id, "index": idx, "total": total})
        result = generate_spec(purpose=scope, agent_id=skill_id)
        skills.append(GeneratedSkill(skill_id=skill_id, description=description, result=result))
        _report({
            "step": "generate_skill", "status": "done", "skill_id": skill_id,
            "index": idx, "total": total, "used_fallback": result.used_fallback,
        })
    return MultiSkillGeneration(skills=skills, split=True)


# -- human-in-the-loop refinement: describe what's wrong, AI corrects the existing draft ----

def _refine_system_prompt() -> str:
    return (
        _system_prompt()
        + "\n\nYou are CORRECTING an existing rule set based on a human's feedback about what's wrong "
          "with it, not designing one from scratch. Preserve anything the feedback doesn't mention as a "
          "problem — don't invent unrelated changes. Output the full corrected spec, not a partial patch."
    )


def _refine_user_prompt(current_rules: dict[str, str], feedback: str) -> str:
    current_yaml = "\n\n".join(
        f"--- rules/{name}.yaml ---\n{text}" for name, text in current_rules.items() if text
    )
    return f"Current rules:\n{current_yaml}\n\nA human reviewed this and said: {feedback}"


@dataclass(frozen=True)
class RefineResult:
    ok: bool
    spec: dict[str, Any] | None
    errors: list[str]


def refine_spec(*, current_rules: dict[str, str], feedback: str) -> RefineResult:
    """Unlike generate_spec, this never falls back to a placeholder on
    failure — silently discarding a human's in-progress draft (and their
    specific feedback) into a generic placeholder would be worse than just
    telling them it didn't work, so they can retry with clearer wording or
    edit the YAML directly instead.
    """
    adapter = _build_adapter()
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=_refine_system_prompt(),
            user_prompt=_refine_user_prompt(current_rules, feedback),
            schema=AGENT_SPEC_SCHEMA,
            temperature=0.2,
        )
    except OllamaError as exc:
        return RefineResult(ok=False, spec=None, errors=[f"LLM call failed: {exc}"])

    errors = validate_spec(parsed)
    if errors:
        parsed = auto_repair(parsed, errors)
        errors = validate_spec(parsed)

    if errors:
        return RefineResult(ok=False, spec=None, errors=errors)
    return RefineResult(ok=True, spec=parsed, errors=[])


# -- rendering: spec -> files. yaml.safe_dump on plain dicts/lists, never f-string YAML ----

def render_agent_yaml(agent_id: str, skill_ids: list[str], spec: dict[str, Any]) -> str:
    purpose = (spec.get("purpose") or "").strip() or "Generated agent — describe further via editor."
    # Block scalars require every non-blank continuation line to be indented; a raw f-string
    # interpolation only indents the first line, so a multi-paragraph purpose breaks YAML parsing.
    purpose_block = "\n".join(f"  {line}" if line.strip() else "" for line in purpose.splitlines())
    evidence_lines = "\n".join(
        f"        - {f['path']} ({f['type']}): {f['description']}" for f in spec.get("evidence_fields", [])
    ) or "        (no evidence fields declared)"

    skill_block = "skills:\n" + "\n".join(f"  - {sid}" for sid in skill_ids)

    # load_skills is a no-op stage for single-skill agents (see skill_selection.py) but only
    # needed in the pipeline at all once there's more than one candidate skill to choose between.
    pipeline_stage_lines = ["  - load_input", "  - gather_evidence"]
    if len(skill_ids) > 1:
        pipeline_stage_lines.append("  - load_skills")
    pipeline_stage_lines += [
        "  - evaluate_rules", "  - reason_llm", "  - validate_output", "  - decide", "  - hitl_gate", "  - explain",
    ]
    pipeline_block = "\n".join(pipeline_stage_lines)

    return f"""agent_id: {agent_id}
version: "1.0.0"
purpose: >
{purpose_block}

{skill_block}

pipeline:
{pipeline_block}

capabilities: []

governance:
  hitl_conditions:
    - low_confidence
    - validation_degraded
  confidence_threshold: 0.6
  max_llm_retries: 1

llm:
  model: {_BUILDER_MODEL}
  temperature: 0.0
  seed: 7
  timeout_seconds: 120

draft: true
routable: false

input_schema:
  type: object
  required: []
  properties:
    evidence:
      type: object
      description: |
        Evidence dict evaluated against this agent's generated rules. Expected fields:
{evidence_lines}

output_schema:
  type: object
  required: [outcome, reason, composite_score]
  properties:
    outcome:
      type: string
      enum: [QUALIFIED, CONDITIONALLY_QUALIFIED, NEEDS_HUMAN_REVIEW, NOT_QUALIFIED]
    reason:
      type: string
    composite_score:
      type: number
"""


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
    purpose_block = "\n".join(f"  {line}" if line.strip() else "" for line in purpose.splitlines())
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

    skill_yaml = f"""skill_id: {skill_id}
version: "1.0.0"
kind: deterministic
description: >
{purpose_block}

instructions: instructions.md
output_contract: output_contract.json

shared_includes:
  - shared/compliance_guardrails.md

rules:
  gates: rules/gates.yaml
  factors: rules/factors.yaml
  composite: rules/composite.yaml
  product_fit: rules/product_fit.yaml
"""

    return {
        "skill.yaml": skill_yaml,
        "instructions.md": instructions_md,
        "output_contract.json": json.dumps(output_contract, indent=2),
        "rules/gates.yaml": yaml.safe_dump(gates_doc, sort_keys=False),
        "rules/factors.yaml": yaml.safe_dump(factors_doc, sort_keys=False),
        "rules/composite.yaml": yaml.safe_dump(composite_doc, sort_keys=False),
        "rules/product_fit.yaml": yaml.safe_dump(product_fit_doc, sort_keys=False),
    }
