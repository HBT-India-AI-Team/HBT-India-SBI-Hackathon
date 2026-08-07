"""The reusable lifecycle stages every agent's pipeline is built from.

None of these functions know they belong to "the Lead Qualification Agent"
— they read whatever the bundle's capabilities/skills/rules declare and
operate generically over RunContext. A future agent reuses whichever of
these stages fit its pipeline (declared in its own agent.yaml) and only
needs new stages for behaviour that's genuinely new.
"""
from __future__ import annotations

import copy
import os

from agent_platform.capabilities import DEFAULT_REGISTRY
from agent_platform.explainability import decision_record
from agent_platform.llm import OllamaAdapter, OllamaError
from agent_platform.runtime.pipeline import register_stage
from agent_platform.skills import output_validator, prompt_assembler, rules_engine

# Used to narrate when no active skill has rules (e.g. an agent whose loaded
# skills are all guidance-only) — same shape output_validator.REQUIRED_FIELDS
# already expects, so validate_output needs no special-casing for it.
_GENERIC_OUTPUT_CONTRACT = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
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
    "required": ["summary", "strengths", "risks", "next_best_action", "confidence"],
}

_OUTCOME_SEVERITY = {"NOT_QUALIFIED": 3, "NEEDS_HUMAN_REVIEW": 2, "CONDITIONALLY_QUALIFIED": 1, "QUALIFIED": 0}


@register_stage("load_input")
def load_input(ctx, bundle, logger) -> None:
    required_fields = bundle.definition.input_schema.get("required", [])
    missing = [f for f in required_fields if f not in ctx.raw_input]
    if missing:
        raise ValueError(f"input missing required field(s): {missing}")


@register_stage("gather_evidence")
def gather_evidence(ctx, bundle, logger) -> None:
    """Either raw_input["evidence"] (an arbitrary dict, used as-is via a deep
    copy — never a live reference into the caller's own dict) or
    raw_input["lead_id"] (the SME fixture lookup). If both are supplied,
    evidence wins silently.
    """
    if "evidence" in ctx.raw_input:
        ctx.evidence = copy.deepcopy(ctx.raw_input["evidence"])
        return

    if "lead_id" not in ctx.raw_input:
        raise ValueError("input must include either 'lead_id' or 'evidence'")
    lead_id = ctx.raw_input["lead_id"]

    for capability in bundle.definition.capabilities:
        if not DEFAULT_REGISTRY.has(capability.name):
            raise KeyError(f"Capability '{capability.name}' is not registered")

    lead = DEFAULT_REGISTRY.invoke("lead_data.get_lead", lead_id=lead_id)
    bureau = DEFAULT_REGISTRY.invoke("credit_bureau.get_report", lead_id=lead_id)
    kyc = DEFAULT_REGISTRY.invoke("kyc.get_status", lead_id=lead_id)

    # Never mutate what a capability returns — it may be a live reference
    # into that capability's own cache/store.
    financials = lead.get("financials", {})
    lead_fields = {k: v for k, v in lead.items() if k != "financials"}
    ctx.evidence = {
        "lead": lead_fields,
        "financials": financials,
        "bureau": bureau,
        "kyc": kyc,
    }


def _skill_referenced_fields(skill) -> set[str]:
    rules = skill.rules or {}
    fields = {g["field"] for g in (rules.get("gates") or {}).get("gates", [])}
    for category in (rules.get("factors") or {}).get("categories", {}).values():
        fields |= {f["field"] for f in category.get("factors", [])}
    return fields


def skill_evidence_fields(skill) -> list[dict]:
    """Every field a skill's own gates/factors reference, with a
    human-readable description where one exists — the gate/factor's own
    `description` (e.g. "Annual turnover exceeds 50 lakh"), since fields
    themselves carry no separate description in gates.yaml/factors.yaml.
    Gate fields are marked required (a missing one can't be scored around —
    the gate just can't be evaluated); factor-only fields are not, since a
    missing factor field only affects one component of the composite score.
    Used by agent_platform/runtime/chat.py to know what to ask a user about
    in a conversation, reusing the exact same walk _skill_referenced_fields
    does for applicability filtering.
    """
    rules = skill.rules or {}
    fields: dict[str, dict] = {}
    for gate in (rules.get("gates") or {}).get("gates", []):
        fields[gate["field"]] = {"field": gate["field"], "description": gate.get("description", ""), "required": True}
    for category in (rules.get("factors") or {}).get("categories", {}).values():
        for factor in category.get("factors", []):
            fields.setdefault(factor["field"], {
                "field": factor["field"], "description": factor.get("description", ""), "required": False,
            })
    return list(fields.values())


def _skill_is_applicable(skill, evidence) -> bool:
    """A rule-bearing skill only "applies" to this request if the fields its
    own gates/factors reference are actually present in the evidence — e.g.
    MCA's gates shouldn't silently reject a GST-only request just because
    MCA-specific fields are missing. If a skill references no fields at all
    (unusual), it's trivially applicable.
    """
    fields = _skill_referenced_fields(skill)
    return not fields or all(rules_engine.get_field(evidence, f) is not None for f in fields)


def _decide_one(rule_results: dict) -> dict:
    forced = rule_results["gates"]["forced_decision"]
    composite = rule_results["composite"]
    thresholds = composite["thresholds"]

    if forced:
        outcome, reason = forced["decision"], forced["reason"]
    else:
        value = composite["value"]
        if value >= thresholds["qualified_min"]:
            outcome, reason = "QUALIFIED", (
                f"Composite score {value} meets the qualified threshold ({thresholds['qualified_min']})"
            )
        elif value >= thresholds["conditional_min"]:
            outcome, reason = "CONDITIONALLY_QUALIFIED", (
                f"Composite score {value} meets the conditional threshold "
                f"({thresholds['conditional_min']}) but not the qualified one"
            )
        else:
            outcome, reason = "NOT_QUALIFIED", (
                f"Composite score {value} is below the conditional threshold ({thresholds['conditional_min']})"
            )

    return {"outcome": outcome, "reason": reason, "composite_score": composite["value"], "thresholds": thresholds}


def _pick_governing_skill(skill_decisions: dict[str, dict]) -> str:
    """Most severe outcome governs (NOT_QUALIFIED > NEEDS_HUMAN_REVIEW >
    CONDITIONALLY_QUALIFIED > QUALIFIED) — a simple, safe, fully explainable
    default until weighted combining ships. Ties break by load order: dict
    iteration order follows insertion order, and max() returns the first
    item achieving the max.
    """
    return max(skill_decisions, key=lambda sid: _OUTCOME_SEVERITY.get(skill_decisions[sid]["outcome"], 0))


@register_stage("evaluate_rules")
def evaluate_rules(ctx, bundle, logger) -> None:
    active_skills = bundle.active_skills(ctx)
    rule_bearing = [s for s in active_skills if s.has_rules]

    ctx.rule_results = {
        skill.skill_id: rules_engine.run_all(ctx.evidence, skill.rules) for skill in rule_bearing
    }

    # If nothing loaded is cleanly applicable (evidence doesn't match any
    # loaded skill's fields), fall back to treating every rule-bearing
    # loaded skill as applicable rather than leaving decide() nothing to
    # work from.
    applicable = [s for s in rule_bearing if _skill_is_applicable(s, ctx.evidence)] or rule_bearing

    ctx.skill_decisions = {s.skill_id: _decide_one(ctx.rule_results[s.skill_id]) for s in applicable}
    ctx.governing_skill_id = _pick_governing_skill(ctx.skill_decisions) if ctx.skill_decisions else None

    logger.event(
        ctx, "qualification_rules_evaluated",
        skills_with_rules=list(ctx.rule_results.keys()),
        applicable_skills=list(ctx.skill_decisions.keys()),
        governing_skill=ctx.governing_skill_id,
        gate_failures=sum(len(r["gates"]["failures"]) for r in ctx.rule_results.values()),
    )


def _governing_output_contract(ctx, bundle) -> dict:
    governing = bundle.skills.get(ctx.governing_skill_id) if ctx.governing_skill_id else None
    if governing and governing.output_contract:
        return governing.output_contract
    # Custom-pipeline agents (e.g. proposal, lead_discovery) don't run the
    # standard evaluate_rules stage, so governing_skill_id is never set —
    # fall back to whichever active skill actually declares a contract.
    for skill in bundle.active_skills(ctx):
        if skill.output_contract:
            return skill.output_contract
    return _GENERIC_OUTPUT_CONTRACT


@register_stage("reason_llm")
def reason_llm(ctx, bundle, logger) -> None:
    active_skills = bundle.active_skills(ctx)
    output_contract = _governing_output_contract(ctx, bundle)

    system_prompt, user_prompt, citation_keys = prompt_assembler.build_prompt(
        active_skills, ctx.evidence, ctx.rule_results
    )
    ctx.citation_keys = citation_keys

    adapter = _build_adapter(bundle)
    logger.event(ctx, "ollama_call_started", model=bundle.definition.llm.model)
    try:
        parsed, meta = adapter.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=output_contract,
            temperature=bundle.definition.llm.temperature,
        )
        ctx.llm_output = parsed
        logger.llm_call(
            ctx, model=meta["model"], duration_ms=meta["duration_ms"],
            prompt_tokens=meta.get("prompt_tokens"),
            completion_tokens=meta.get("completion_tokens"),
            attempt=1, ok=True,
        )
        logger.event(ctx, "ollama_call_completed", model=meta["model"],
                     duration_ms=meta["duration_ms"], retries=0)
    except OllamaError as exc:
        logger.warning(ctx, f"LLM call failed, will fall back to deterministic rationale: {exc}")
        logger.event(ctx, "ollama_call_completed", level="WARNING",
                     model=bundle.definition.llm.model, error=str(exc))
        ctx.llm_output = None


@register_stage("validate_output")
def validate_output(ctx, bundle, logger) -> None:
    governing_results = ctx.rule_results.get(ctx.governing_skill_id, {})

    if ctx.llm_output is None:
        ctx.validated_output = output_validator.deterministic_fallback(governing_results)
        logger.event(ctx, "output_validated", status="degraded_fallback")
        return

    required_fields = _governing_output_contract(ctx, bundle).get("required")
    needs_retry, cleaned, issues = output_validator.validate_and_clean(
        ctx.llm_output, ctx.citation_keys, required_fields
    )
    for issue in issues:
        logger.warning(ctx, issue)

    if not needs_retry:
        ctx.validated_output = cleaned
        logger.event(ctx, "output_validated", status="ok", issue_count=len(issues))
        return

    ctx.validated_output = _repair_and_revalidate(ctx, bundle, logger, issues)
    logger.event(ctx, "output_validated", status="repaired_or_fallback")


@register_stage("decide")
def decide(ctx, bundle, logger) -> None:
    if not ctx.skill_decisions:
        # No rule-bearing skill was ever active for this request (e.g. every
        # loaded skill was guidance-only) — there's nothing deterministic to
        # decide from, so this always needs a human.
        ctx.decision = {
            "outcome": "NEEDS_HUMAN_REVIEW",
            "reason": "No rule-bearing skill was active for this request",
            "composite_score": None,
            "thresholds": {},
            "skill_breakdown": [],
        }
        return

    governing = ctx.skill_decisions[ctx.governing_skill_id]
    ctx.decision = {
        **governing,
        "skill_breakdown": [{"skill_id": sid, **d} for sid, d in ctx.skill_decisions.items()],
    }


@register_stage("hitl_gate")
def hitl_gate(ctx, bundle, logger) -> None:
    conditions = set(bundle.definition.governance.hitl_conditions)
    confidence = (ctx.validated_output or {}).get("confidence")
    degraded = bool((ctx.validated_output or {}).get("degraded", False))

    reasons: list[str] = []
    if "low_confidence" in conditions and confidence is not None:
        threshold = bundle.definition.governance.confidence_threshold
        if confidence < threshold:
            reasons.append(f"LLM confidence {confidence} is below threshold {threshold}")
    if "validation_degraded" in conditions and degraded:
        reasons.append("LLM output failed validation; deterministic fallback rationale was used")

    triggered = bool(reasons)
    if triggered and ctx.decision and ctx.decision["outcome"] not in ("NOT_QUALIFIED", "NEEDS_HUMAN_REVIEW"):
        ctx.decision["outcome"] = "NEEDS_HUMAN_REVIEW"
        ctx.decision["reason"] += " | Escalated to human review: " + "; ".join(reasons)

    ctx.hitl = {"triggered": triggered, "reasons": reasons}


@register_stage("explain")
def explain(ctx, bundle, logger) -> None:
    ctx.explanation = decision_record.build(ctx, bundle)


# -- helpers -----------------------------------------------------

def _build_adapter(bundle) -> OllamaAdapter:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    llm_config = bundle.definition.llm
    return OllamaAdapter(
        host=host,
        model=llm_config.model,
        timeout_seconds=llm_config.timeout_seconds,
        seed=llm_config.seed,
    )


def _repair_and_revalidate(ctx, bundle, logger, issues: list[str]) -> dict:
    max_retries = bundle.definition.governance.max_llm_retries
    active_skills = bundle.active_skills(ctx)
    output_contract = _governing_output_contract(ctx, bundle)
    system_prompt, user_prompt, _ = prompt_assembler.build_prompt(active_skills, ctx.evidence, ctx.rule_results)
    repair_prompt = (
        user_prompt
        + "\n\nYour previous output had these problems — fix them and "
          "resend the full JSON object: " + "; ".join(issues)
    )
    adapter = _build_adapter(bundle)

    for attempt in range(2, max_retries + 2):
        try:
            parsed, meta = adapter.generate_structured(
                system_prompt=system_prompt,
                user_prompt=repair_prompt,
                schema=output_contract,
                temperature=bundle.definition.llm.temperature,
            )
            logger.llm_call(
                ctx, model=meta["model"], duration_ms=meta["duration_ms"],
                prompt_tokens=meta.get("prompt_tokens"),
                completion_tokens=meta.get("completion_tokens"),
                attempt=attempt, ok=True,
            )
        except OllamaError as exc:
            logger.warning(ctx, f"Repair attempt {attempt} failed: {exc}")
            continue

        needs_retry, cleaned, retry_issues = output_validator.validate_and_clean(
            parsed, ctx.citation_keys, output_contract.get("required")
        )
        for issue in retry_issues:
            logger.warning(ctx, issue)
        if not needs_retry:
            return cleaned

    logger.warning(ctx, "Validation failed after all retries; using deterministic fallback rationale")
    return output_validator.deterministic_fallback(ctx.rule_results.get(ctx.governing_skill_id, {}))
