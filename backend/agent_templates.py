"""Template definitions for the New Agent flow's start-from-template picker.

Each template = (id, label, description, pipeline, a function rendering
agent.yaml text, a function rendering skill-relative filename -> content).
Adding a template means adding one entry to TEMPLATES; admin.py doesn't
change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

AgentYamlFn = Callable[[str, str, str], str]        # (agent_id, skill_id, purpose) -> text
SkillFilesFn = Callable[[str, str], dict[str, str]]  # (skill_id, purpose) -> {relative_path: content}


@dataclass(frozen=True)
class AgentTemplate:
    id: str
    label: str
    description: str
    pipeline: list[str]
    render_agent_yaml: AgentYamlFn
    render_skill_files: SkillFilesFn


def _blank_agent_yaml(agent_id: str, skill_id: str, purpose: str) -> str:
    return f"""agent_id: {agent_id}
version: "1.0.0"
purpose: >
  {purpose}

skills:
  - {skill_id}

pipeline:
  - load_input
  - reason_llm
  - validate_output
  - explain

capabilities: []

governance:
  hitl_conditions: []
  confidence_threshold: 0.5
  max_llm_retries: 1

llm:
  model: gemma4:12b
  temperature: 0.0
  seed: 7
  timeout_seconds: 120

input_schema:
  type: object
  required: []
  properties: {{}}

output_schema:
  type: object
  required: []
  properties: {{}}
"""


def _blank_skill_files(skill_id: str, purpose: str) -> dict[str, str]:
    output_contract = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["summary", "confidence"],
    }
    return {
        "skill.yaml": f"""skill_id: {skill_id}
version: "1.0.0"
description: >
  Describe this skill.

instructions: instructions.md
output_contract: output_contract.json

shared_includes:
  - shared/compliance_guardrails.md

rules: {{}}
""",
        "instructions.md": "# New Skill\n\nDescribe what the agent should produce and what it must not do.\n",
        "output_contract.json": json.dumps(output_contract, indent=2),
    }


def _gates_scoring_agent_yaml(agent_id: str, skill_id: str, purpose: str) -> str:
    return f"""agent_id: {agent_id}
version: "1.0.0"
purpose: >
  {purpose}

skills:
  - {skill_id}

pipeline:
  - load_input
  - gather_evidence
  - evaluate_rules
  - reason_llm
  - validate_output
  - decide
  - hitl_gate
  - explain

capabilities: []

governance:
  hitl_conditions:
    - low_confidence
    - validation_degraded
  confidence_threshold: 0.6
  max_llm_retries: 1

llm:
  model: gemma4:12b
  temperature: 0.0
  seed: 7
  timeout_seconds: 120

input_schema:
  type: object
  required: []
  properties:
    evidence:
      type: object
      description: >
        Arbitrary evidence dict evaluated against rules/*.yaml. Either this
        or lead_id is required (enforced by gather_evidence, not this
        schema); if both are given, evidence wins.
    lead_id:
      type: string
      description: >
        Optional — looks up the built-in SME lead fixture instead of
        supplying evidence inline. Only useful if capabilities are added
        back and this skill's rules expect the lead/financials/bureau/kyc
        shape gather_evidence's fixture path produces.

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


_GATES_SCORING_INSTRUCTIONS = """# {skill_id}

You are producing a qualitative narrative for a decision that has ALREADY
been made by deterministic rules (see rules/*.yaml) — you do not decide
anything yourself.

Use ONLY the facts under `evidence` and `facts` in the prompt payload. For
every point in `strengths` and `risks`, set `evidence_key` to one of the
exact strings listed in `allowed_citation_keys` — never invent a key.

Do not output `decision`, `outcome`, `qualified`, `score`, or any other
decision-bearing field — those are computed separately and will be
stripped from your output if you include them.

Replace this file with real domain instructions once you've edited
rules/*.yaml for your use case.
"""

_GATES_YAML = """rule_group: gates
description: >
  Example placeholder gate. Every gate is evaluated (nothing short-
  circuits); the most severe on_fail decision wins. `field` is a dotted
  path into whatever dict is supplied as `evidence` — replace this with
  your own gates once you know that shape. As shipped, this gate only
  fails if evidence.flagged is explicitly true, so a fresh agent with
  empty evidence passes through untouched.

gates:
  - id: EXAMPLE_GATE
    description: "Placeholder — delete or edit, then add your own gates"
    field: flagged
    operator: neq
    value: true
    on_fail:
      decision: NOT_QUALIFIED
      reason: "Example gate: evidence.flagged was true"
"""

_FACTORS_YAML = """rule_group: factors
description: >
  Weighted scoring factors, grouped into categories. Each factor maps a
  dotted evidence field to a 0-100 score via banded thresholds (highest
  matching band wins); a category's score is the weighted average of its
  factors. Replace `overall`/`EXAMPLE_SCORE` with your own categories.

categories:
  overall:
    factors:
      - id: EXAMPLE_SCORE
        description: "Placeholder — replace field/weight/bands with real ones"
        field: score
        weight: 1.0
        bands:
          - {min: 80, score: 100}
          - {min: 50, score: 60}
          - {min: 0, score: 30}
"""

_COMPOSITE_YAML = """rule_group: composite
description: >
  Combines category scores into a single composite score and defines the
  decision thresholds. Weight keys must match rules/factors.yaml's
  category names. A gate failure (see gates.yaml) overrides these
  thresholds entirely.

weights:
  overall: 1.0

thresholds:
  qualified_min: 75
  conditional_min: 50
"""

_PRODUCT_FIT_YAML = """rule_group: product_fit
description: >
  Deterministic matching. `run_all` calls evaluate_products
  unconditionally, so keep at least one product with `when: []` as a
  fallback even before you add real conditions.

products:
  - id: EXAMPLE_PRODUCT
    name: Example Product
    reason: "Placeholder fallback — always matches until you add when: conditions"
    when: []
"""


def _gates_scoring_skill_files(skill_id: str, purpose: str) -> dict[str, str]:
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
    return {
        "skill.yaml": f"""skill_id: {skill_id}
version: "1.0.0"
description: >
  {purpose}

instructions: instructions.md
output_contract: output_contract.json

shared_includes:
  - shared/compliance_guardrails.md

rules:
  gates: rules/gates.yaml
  factors: rules/factors.yaml
  composite: rules/composite.yaml
  product_fit: rules/product_fit.yaml
""",
        "instructions.md": _GATES_SCORING_INSTRUCTIONS.format(skill_id=skill_id),
        "output_contract.json": json.dumps(output_contract, indent=2),
        "rules/gates.yaml": _GATES_YAML,
        "rules/factors.yaml": _FACTORS_YAML,
        "rules/composite.yaml": _COMPOSITE_YAML,
        "rules/product_fit.yaml": _PRODUCT_FIT_YAML,
    }


TEMPLATES: dict[str, AgentTemplate] = {
    "blank": AgentTemplate(
        id="blank",
        label="Blank / Narrator",
        description=(
            "Today's default: load_input -> reason_llm -> validate_output -> "
            "explain. No rules, no evidence gathering — just an LLM narrating "
            "over whatever input you give it."
        ),
        pipeline=["load_input", "reason_llm", "validate_output", "explain"],
        render_agent_yaml=_blank_agent_yaml,
        render_skill_files=_blank_skill_files,
    ),
    "gates_scoring": AgentTemplate(
        id="gates_scoring",
        label="Gates + Scoring",
        description=(
            "Mirrors lead_qualification's shape: hard eligibility gates, "
            "weighted category scoring, a composite-threshold decision, and "
            "product-fit matching — all config-driven via rules/*.yaml. Works "
            "off an inline `evidence` object you supply at run time, not the "
            "SME fixture. Ships with placeholder rule files you're expected "
            "to edit."
        ),
        pipeline=["load_input", "gather_evidence", "evaluate_rules", "reason_llm",
                  "validate_output", "decide", "hitl_gate", "explain"],
        render_agent_yaml=_gates_scoring_agent_yaml,
        render_skill_files=_gates_scoring_skill_files,
    ),
}


def render_procedural_skill_files(skill_id: str, description: str) -> dict[str, str]:
    """Scaffold for a new guidance-only skill (no rules/output_contract) —
    no template picker needed since, unlike a rules-bearing skill, there's
    no pipeline/rules shape to choose between: just an id, a description
    (what the load_skills tool-calling loop reads to decide relevance) and
    guidance text.
    """
    return {
        "skill.yaml": f"""skill_id: {skill_id}
version: "1.0.0"
kind: procedural
description: >
  {description or 'Describe when this guidance applies.'}

instructions: instructions.md
""",
        "instructions.md": f"# {skill_id}\n\nDescribe the guidance this procedural skill should inject.\n",
    }


def get_template(template_id: str) -> AgentTemplate:
    if template_id not in TEMPLATES:
        raise KeyError(f"Unknown template_id '{template_id}'. Known: {sorted(TEMPLATES)}")
    return TEMPLATES[template_id]


def list_templates() -> list[dict]:
    return [
        {"id": t.id, "label": t.label, "description": t.description, "pipeline": t.pipeline}
        for t in TEMPLATES.values()
    ]
