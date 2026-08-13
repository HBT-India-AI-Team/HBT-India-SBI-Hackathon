"""The reusable lifecycle stages every agent's pipeline is built from.

None of these functions know they belong to "the Lead Qualification Agent"
— they read whatever the bundle's capabilities/skills/rules declare and
operate generically over RunContext. A future agent reuses whichever of
these stages fit its pipeline (declared in its own agent.yaml) and only
needs new stages for behaviour that's genuinely new.
"""
from __future__ import annotations

import copy
import json
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

    # Reject a bad enum value outright rather than letting it silently mean
    # something else downstream — e.g. an agent that dispatches on skill_id
    # (see load_skills) falls back to its first declared skill on an unknown
    # id, which would otherwise turn a caller's typo into a wrong-but-valid
    # response instead of a clear error. No existing agent declares an enum
    # on an input_schema property today, so this only ever activates for one
    # that opts in.
    properties = bundle.definition.input_schema.get("properties", {})
    for field_name, spec in properties.items():
        enum_values = spec.get("enum") if isinstance(spec, dict) else None
        if enum_values and field_name in ctx.raw_input and ctx.raw_input[field_name] not in enum_values:
            raise ValueError(
                f"input field '{field_name}' has value {ctx.raw_input[field_name]!r}, "
                f"must be one of: {enum_values}"
            )


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


def _record_llm_detail(ctx, meta: dict, *, prompt_chars: int | None = None,
                       tool_calls: list[dict] | None = None) -> None:
    """Attaches what this LLM call actually did to the stage's detail, for
    the Playground's AI Observation panel.

    Everything here comes from Ollama's own response — `thinking` is the
    model's real reasoning trace, not a summary written after the fact. If
    the model doesn't reason, `thinking` is None and the UI shows the call
    stats alone rather than fabricating a narrative.
    """
    detail = {
        "model": meta.get("model"),
        "duration_ms": meta.get("duration_ms"),
        "prompt_tokens": meta.get("prompt_tokens"),
        "completion_tokens": meta.get("completion_tokens"),
        "done_reason": meta.get("done_reason"),
        "thinking": meta.get("thinking"),
    }
    if prompt_chars is not None:
        detail["prompt_chars"] = prompt_chars
    if tool_calls:
        detail["tool_calls"] = [
            {"name": c["name"], "arguments": c["arguments"], "result": c["result"]} for c in tool_calls
        ]
    ctx.pending_stage_detail = {k: v for k, v in detail.items() if v is not None}


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
        _record_llm_detail(ctx, meta, prompt_chars=len(system_prompt) + len(user_prompt))
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
        ctx.pending_stage_detail = {"model": bundle.definition.llm.model, "llm_error": str(exc)}
        ctx.llm_output = None


_TEXT_ROUTING_KEYS = {"skill_id", "skill_ids", "correlation_id"}


def _build_text_prompt(skill, raw_input: dict) -> tuple[str, str]:
    """Plain system/user prompt for a text-output skill — no evidence/facts/
    citation-grounding payload (that's prompt_assembler's job for the
    JSON-structured archetypes). The caller's own fields become simple
    'key: value' lines rather than a JSON blob, so the model isn't primed to
    echo JSON back when the whole point is plain text out.
    """
    sections = [skill.instructions_text]
    if skill.shared_text:
        sections.append(f"--- Shared platform guardrails ---\n{skill.shared_text}")
    system_prompt = "\n\n".join(sections)

    fields = {k: v for k, v in raw_input.items() if k not in _TEXT_ROUTING_KEYS}
    user_prompt = "\n".join(f"{k}: {v}" for k, v in fields.items()) or "(no context provided)"
    return system_prompt, user_prompt


@register_stage("reason_llm_text")
def reason_llm_text(ctx, bundle, logger) -> None:
    """Text-mode counterpart to reason_llm: for skills whose entire job is
    emitting plain dialogue/prose, not a JSON-schema-constrained object.
    Expects exactly one active skill (load_skills resolves which voice this
    request is for via an explicit skill_id/skill_ids override) — dialogue
    voices must never blend in one response, so there's no multi-skill
    concatenation path here the way prompt_assembler allows for narration.
    """
    active_skills = bundle.active_skills(ctx)
    skill = active_skills[0] if active_skills else None
    if skill is None:
        ctx.llm_output = None
        logger.warning(ctx, "reason_llm_text: no active skill loaded")
        return

    system_prompt, user_prompt = _build_text_prompt(skill, ctx.raw_input)
    adapter = _build_adapter(bundle)
    logger.event(ctx, "ollama_call_started", model=bundle.definition.llm.model)
    try:
        text, meta = adapter.generate_text(
            system_prompt=system_prompt, user_prompt=user_prompt,
            temperature=bundle.definition.llm.temperature,
        )
        ctx.llm_output = {"text": text}
        _record_llm_detail(ctx, meta, prompt_chars=len(system_prompt) + len(user_prompt))
        logger.llm_call(
            ctx, model=meta["model"], duration_ms=meta["duration_ms"],
            prompt_tokens=meta.get("prompt_tokens"), completion_tokens=meta.get("completion_tokens"),
            attempt=1, ok=True,
        )
        logger.event(ctx, "ollama_call_completed", model=meta["model"], duration_ms=meta["duration_ms"], retries=0)
    except OllamaError as exc:
        logger.warning(ctx, f"LLM call failed, no text generated: {exc}")
        logger.event(ctx, "ollama_call_completed", level="WARNING",
                     model=bundle.definition.llm.model, error=str(exc))
        ctx.pending_stage_detail = {"model": bundle.definition.llm.model, "llm_error": str(exc)}
        ctx.llm_output = None


def _clean_text_output(raw: str) -> str:
    """Strips whitespace and the wrapping artifacts models add even when not
    asked to (a fenced code block, or the whole reply in one pair of quotes)
    — never touches wording/content, only the outer wrapper.
    """
    text = (raw or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        first_line, _, rest = text.partition("\n")
        if first_line and " " not in first_line and len(first_line) < 20:
            text = rest.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        inner = text[1:-1]
        if inner.count(text[0]) == 0:
            text = inner.strip()
    return text


@register_stage("validate_text_output")
def validate_text_output(ctx, bundle, logger) -> None:
    if ctx.llm_output is None:
        ctx.validated_output = {"text": "", "degraded": True}
        logger.event(ctx, "output_validated", status="degraded_fallback")
        return

    cleaned = _clean_text_output(ctx.llm_output.get("text", ""))
    if not cleaned:
        ctx.validated_output = {"text": "", "degraded": True}
        logger.event(ctx, "output_validated", status="degraded_fallback", issue_count=1)
        return

    ctx.validated_output = {"text": cleaned}
    logger.event(ctx, "output_validated", status="ok", issue_count=0)


# Hand-declared JSON-schema wrappers for real, callable capabilities — capabilities.py's
# ToolRegistry itself carries no parameter schema (its callers today all invoke it directly
# by name with known kwargs), so a stage that wants an LLM to call one via tool-calling needs
# its own schema. Only capabilities listed here are offered to the model; a capability an
# agent declares without a schema here is simply never offered as a tool (still usable
# directly by other stages, e.g. gather_evidence's lead_id path).
_TOOL_SCHEMAS: dict[str, dict] = {
    "finance.get_fd_rate": {
        "type": "function",
        "function": {
            "name": "finance.get_fd_rate",
            "description": "Look up the current fixed deposit interest rate for a given tenure in months.",
            "parameters": {
                "type": "object",
                "required": ["tenure_months"],
                "properties": {"tenure_months": {"type": "integer", "description": "Deposit tenure in months"}},
            },
        },
    },
    "finance.get_savings_rate": {
        "type": "function",
        "function": {
            "name": "finance.get_savings_rate",
            "description": "Look up the current savings account interest rate.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "finance.calculate_emi": {
        "type": "function",
        "function": {
            "name": "finance.calculate_emi",
            "description": "Calculate the monthly EMI, total payment, and total interest for a loan.",
            "parameters": {
                "type": "object",
                "required": ["principal", "annual_rate_percent", "tenure_months"],
                "properties": {
                    "principal": {"type": "number", "description": "Loan amount"},
                    "annual_rate_percent": {"type": "number", "description": "Annual interest rate, percent"},
                    "tenure_months": {"type": "integer", "description": "Loan tenure in months"},
                },
            },
        },
    },
    "reports.lead_pipeline_digest": {
        "type": "function",
        "function": {
            "name": "reports.lead_pipeline_digest",
            "description": "Get real, computed SME lead-pipeline statistics (lead count, total requested "
                            "amount, average turnover, business-need breakdown) for a location and/or industry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City to filter leads by, e.g. Chennai"},
                    "industry": {"type": "string", "description": "Industry to filter leads by, e.g. manufacturing"},
                },
            },
        },
    },
    # -- FinGuru: India retail banking reference rates ----
    # Each of these returns as_of / source_url / stale alongside the number;
    # the finguru skill requires the model to pass that provenance through.
    "india.get_policy_rate": {
        "type": "function",
        "function": {
            "name": "india.get_policy_rate",
            "description": "Look up the RBI policy repo rate, with the date it was last updated. Use this "
                            "for any question about 'the repo rate', RBI policy, or why loan/deposit rates "
                            "are moving.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "india.get_savings_rate": {
        "type": "function",
        "function": {
            "name": "india.get_savings_rate",
            "description": "Look up the representative savings-account interest rate, with its as-of date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "india.get_fd_rate": {
        "type": "function",
        "function": {
            "name": "india.get_fd_rate",
            "description": "Look up the fixed-deposit interest rate for a given tenure. Call this before "
                            "quoting any FD rate or computing an FD maturity value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tenure_months": {"type": "integer", "description": "Deposit tenure in months, e.g. 12"},
                    "senior_citizen": {
                        "type": "boolean",
                        "description": "True to include the senior-citizen bonus rate. Only set this if the "
                                        "user said they are a senior citizen.",
                    },
                },
                "required": ["tenure_months"],
            },
        },
    },
    "india.get_loan_rate": {
        "type": "function",
        "function": {
            "name": "india.get_loan_rate",
            "description": "Look up the indicative interest-rate RANGE for a retail loan product. Returns a "
                            "from/to band, not a single rate, because the advertised floor is only what the "
                            "best-credit borrowers get.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {
                        "type": "string",
                        "enum": ["home_loan", "personal_loan", "car_loan", "education_loan", "gold_loan"],
                        "description": "Which retail loan product",
                    },
                },
                "required": ["product"],
            },
        },
    },
    "india.get_tax_saving_limits": {
        "type": "function",
        "function": {
            "name": "india.get_tax_saving_limits",
            "description": "Look up old-regime tax deduction ceilings (80C, 80D, NPS 80CCD(1B), home-loan "
                            "interest under 24b). These do NOT apply under the new tax regime.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "india.get_scheme_details": {
        "type": "function",
        "function": {
            "name": "india.get_scheme_details",
            "description": "Look up Indian government scheme terms: small savings rates (PPF, Sukanya "
                            "Samriddhi, Senior Citizens Savings Scheme, NSC, Kisan Vikas Patra, Post "
                            "Office Monthly Income), the Jan Suraksha insurance and pension schemes "
                            "(PMJJBY, PMSBY, Atal Pension Yojana), and financial-inclusion schemes "
                            "(Jan Dhan / PMJDY, MUDRA loans). Use this for any question about a "
                            "government scheme by name, and for 'what schemes can I get' or 'cheap "
                            "insurance' — call it with no argument to see everything at once. Small "
                            "savings rates are revised quarterly, so pass on the stale flag if set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme": {
                        "type": "string",
                        "description": "A scheme name (ppf, sukanya_samriddhi, scss, nsc, kvp, pmjjby, "
                                        "pmsby, apy, pmjdy, mudra_pmmy) or a group (small_savings, "
                                        "jan_suraksha, financial_inclusion). Omit for everything.",
                    },
                },
            },
        },
    },
    # -- FinGuru: live market data ----
    "fx.get_rate": {
        "type": "function",
        "function": {
            "name": "fx.get_rate",
            "description": "Get the live ECB reference exchange rate for a currency pair. This is genuinely "
                            "live data. Use it for any 'what is X worth in Y' question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "3-letter ISO code to convert FROM, e.g. USD"},
                    "target": {"type": "string", "description": "3-letter ISO code to convert TO, e.g. INR"},
                },
                "required": ["base", "target"],
            },
        },
    },
    # -- FinGuru: deterministic personal-finance math ----
    "money.fd_maturity": {
        "type": "function",
        "function": {
            "name": "money.fd_maturity",
            "description": "Compute a fixed deposit's maturity value and interest earned, compounded "
                            "quarterly. Look the rate up with india.get_fd_rate first, then pass it here.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {"type": "number", "description": "Amount deposited, in rupees"},
                    "annual_rate_percent": {"type": "number", "description": "Annual rate, e.g. 6.6 for 6.6%"},
                    "tenure_months": {"type": "integer", "description": "Deposit tenure in months"},
                },
                "required": ["principal", "annual_rate_percent", "tenure_months"],
            },
        },
    },
    "money.sip_projection": {
        "type": "function",
        "function": {
            "name": "money.sip_projection",
            "description": "Project the future value of a monthly SIP. The return rate is an assumption you "
                            "or the user supplies — it is not looked up anywhere and must be presented as an "
                            "assumption, never a promise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_investment": {"type": "number", "description": "Monthly amount invested, in rupees"},
                    "annual_return_percent": {"type": "number", "description": "Assumed annual return, e.g. 12"},
                    "years": {"type": "number", "description": "Investment period in years"},
                },
                "required": ["monthly_investment", "annual_return_percent", "years"],
            },
        },
    },
    "money.sip_required_for_goal": {
        "type": "function",
        "function": {
            "name": "money.sip_required_for_goal",
            "description": "Compute the monthly SIP needed to reach a target amount in a given number of "
                            "years. Use this for 'how much should I invest to get to X' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_amount": {"type": "number", "description": "Goal amount in rupees"},
                    "annual_return_percent": {"type": "number", "description": "Assumed annual return, e.g. 12"},
                    "years": {"type": "number", "description": "Years available to reach the goal"},
                },
                "required": ["target_amount", "annual_return_percent", "years"],
            },
        },
    },
    "money.debt_payoff_time": {
        "type": "function",
        "function": {
            "name": "money.debt_payoff_time",
            "description": "Compute how long a debt takes to clear at a fixed monthly payment and the total "
                            "interest paid. If the payment is too small to ever clear it, this reports that "
                            "and the minimum payment needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "balance": {"type": "number", "description": "Outstanding balance in rupees"},
                    "annual_rate_percent": {"type": "number", "description": "Annual interest rate, e.g. 42 for a credit card"},
                    "monthly_payment": {"type": "number", "description": "Fixed amount paid each month"},
                },
                "required": ["balance", "annual_rate_percent", "monthly_payment"],
            },
        },
    },
    "money.budget_split": {
        "type": "function",
        "function": {
            "name": "money.budget_split",
            "description": "Split monthly take-home pay across needs/wants/savings. Defaults to 50/30/20; "
                            "pass explicit percentages to reflect a split the user already uses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_take_home": {"type": "number", "description": "Post-tax monthly income in rupees"},
                    "needs_percent": {"type": "number", "description": "Percent for needs (default 50)"},
                    "wants_percent": {"type": "number", "description": "Percent for wants (default 30)"},
                    "savings_percent": {"type": "number", "description": "Percent for savings (default 20)"},
                },
                "required": ["monthly_take_home"],
            },
        },
    },
    "money.emergency_fund_target": {
        "type": "function",
        "function": {
            "name": "money.emergency_fund_target",
            "description": "Compute an emergency-fund target from monthly essential expenses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_essential_expenses": {
                        "type": "number",
                        "description": "Rent, EMIs, food, utilities, insurance — not discretionary spending",
                    },
                    "months_of_cover": {"type": "number", "description": "Months of cover to target (default 6)"},
                },
                "required": ["monthly_essential_expenses"],
            },
        },
    },
    "money.prepayment_savings": {
        "type": "function",
        "function": {
            "name": "money.prepayment_savings",
            "description": "Compute exactly what a one-off lump-sum prepayment against a loan buys: "
                            "months cut off the tenure and interest saved, with the EMI unchanged. Use "
                            "this for ANY 'should I prepay / is it worth prepaying' question — the "
                            "number is the decision, so never answer that with a general principle "
                            "alone. If the EMI is unknown, ask for it (and the rate and balance) "
                            "rather than estimating.",
            "parameters": {
                "type": "object",
                "properties": {
                    "balance": {"type": "number", "description": "Outstanding principal still owed"},
                    "annual_rate_percent": {"type": "number", "description": "Annual interest rate, e.g. 14"},
                    "monthly_payment": {"type": "number", "description": "Current EMI"},
                    "prepay_amount": {"type": "number", "description": "Lump sum being considered"},
                    "prepayment_charge_percent": {
                        "type": "number",
                        "description": "The loan's prepayment/foreclosure charge as a percentage, taken "
                                        "from prepayment_charge_percent on the loan record. ALWAYS pass "
                                        "it when you have it — the tool nets the charge and the 18% GST "
                                        "on it off the saving. Do not work the charge out yourself; "
                                        "3% of 50,000 is not 1,500 once GST applies.",
                    },
                },
                "required": ["balance", "annual_rate_percent", "monthly_payment", "prepay_amount"],
            },
        },
    },
    # -- FinGuru: the signed-in customer's own position ----
    "accounts.get_profile": {
        "type": "function",
        "function": {
            "name": "accounts.get_profile",
            "description": "Read the SIGNED-IN customer's actual banking position — savings and FD "
                            "balances, every loan with its real outstanding amount, interest rate, EMI, "
                            "remaining tenure and prepayment charge, and card dues. Call this FIRST for "
                            "any question about 'my' money, 'my' loan, 'my' balance, or what they should "
                            "do with their own finances. Never ask the customer for a figure this "
                            "returns — the bank already knows it.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "accounts.get_borrowings": {
        "type": "function",
        "function": {
            "name": "accounts.get_borrowings",
            "description": "Read the signed-in customer's debts, sorted most expensive first, each with "
                            "its rate, EMI and prepayment charge. Use for 'which loan should I clear "
                            "first', 'am I over-borrowed', or any spare-money question.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # -- FinGuru: published RBI guidance ----
    "docs.search": {
        "type": "function",
        "function": {
            "name": "docs.search",
            "description": "Search published RBI guidance for the rule that answers a question about "
                            "customer rights and banking regulation — deposit insurance cover, liability "
                            "for unauthorised card transactions, home loan foreclosure and prepayment "
                            "charges, and how to complain to the Banking Ombudsman. Use this whenever the "
                            "answer is a RULE rather than a number, instead of recalling the rule. Returns "
                            "passages with their source and URL, so quote what comes back and cite it. "
                            "An empty results list means the corpus does not cover the question — say so.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, phrased as the precise thing you need to know. "
                                        "MUST be in English even when the user asked in another language — "
                                        "the documents are English, so translate here and answer in their "
                                        "language afterwards. Ask for the FACT, not the situation: "
                                        "'how much is a depositor insured for' retrieves the limit, while "
                                        "'what happens if a bank fails' retrieves passages about bank "
                                        "failure that never state it. If the passages you get back don't "
                                        "actually contain the answer, call this again with the question "
                                        "rephrased — that usually fixes it.",
                    },
                    "top_k": {"type": "number", "description": "How many passages to return (default 4, max 8)"},
                },
                "required": ["query"],
            },
        },
    },
    "docs.list_sources": {
        "type": "function",
        "function": {
            "name": "docs.list_sources",
            "description": "List which RBI documents are in the corpus and how current they are. Use when "
                            "the user asks what you can look up, or to check coverage before saying you "
                            "cannot answer something.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
}


@register_stage("reason_llm_with_tools")
def reason_llm_with_tools(ctx, bundle, logger) -> None:
    """Text-prompt reasoning (see reason_llm_text) that can also call real,
    registered capabilities as tools before producing its final structured
    answer — e.g. a "financial guru" chat agent actually computing an EMI
    via capabilities_impl.financial_tools.calculate_emi rather than having
    the LLM guess a plausible-looking number. The tool-calling loop
    (OllamaAdapter.run_tool_loop) is the same mechanism load_skills already
    uses for skill routing; here it resolves to real DEFAULT_REGISTRY calls
    instead of a routing decision.
    """
    active_skills = bundle.active_skills(ctx)
    skill = active_skills[0] if active_skills else None
    output_contract = _governing_output_contract(ctx, bundle)
    system_prompt, user_prompt = _build_text_prompt(skill, ctx.raw_input) if skill else ("", "")

    tool_names = [c.name for c in bundle.definition.capabilities if c.name in _TOOL_SCHEMAS]
    tools = [_TOOL_SCHEMAS[name] for name in tool_names]
    tool_calls_made: list[dict] = []

    if tools:
        def resolve_tool(name: str, arguments: dict) -> str:
            if not DEFAULT_REGISTRY.has(name):
                return f"Unknown tool '{name}'"
            try:
                result = DEFAULT_REGISTRY.invoke(name, **arguments)
            except Exception as exc:  # noqa: BLE001 - a bad tool call must be reported back
                # to the model as a tool result, never crash the run.
                return f"Tool call failed: {exc}"
            tool_calls_made.append({"name": name, "arguments": arguments, "result": result})
            return json.dumps(result, default=str)

        adapter = _build_adapter(bundle)
        try:
            adapter.run_tool_loop(
                system_prompt=system_prompt + "\n\nCall the available tools whenever they let you answer "
                                               "with a real number instead of an estimate.",
                user_prompt=user_prompt,
                tools=tools,
                resolve_tool=resolve_tool,
                max_turns=3,
                temperature=bundle.definition.llm.temperature,
            )
        except OllamaError as exc:
            logger.warning(ctx, f"Tool-calling loop failed, answering without tool results: {exc}")

    logger.event(ctx, "tool_calls_made", calls=[c["name"] for c in tool_calls_made])

    if tool_calls_made:
        tool_lines = "\n".join(
            f"- {c['name']}({c['arguments']}) -> {c['result']}" for c in tool_calls_made
        )
        user_prompt = (
            f"{user_prompt}\n\nReal tool results already gathered — use these exact values in your "
            f"answer, don't recompute or guess them:\n{tool_lines}"
        )

    adapter = _build_adapter(bundle)
    logger.event(ctx, "ollama_call_started", model=bundle.definition.llm.model)
    try:
        parsed, meta = adapter.generate_structured(
            system_prompt=system_prompt, user_prompt=user_prompt,
            schema=output_contract, temperature=bundle.definition.llm.temperature,
        )
        ctx.llm_output = parsed
        _record_llm_detail(ctx, meta, prompt_chars=len(system_prompt) + len(user_prompt),
                           tool_calls=tool_calls_made)
        logger.llm_call(
            ctx, model=meta["model"], duration_ms=meta["duration_ms"],
            prompt_tokens=meta.get("prompt_tokens"), completion_tokens=meta.get("completion_tokens"),
            attempt=1, ok=True,
        )
        logger.event(ctx, "ollama_call_completed", model=meta["model"], duration_ms=meta["duration_ms"], retries=0)
    except OllamaError as exc:
        logger.warning(ctx, f"LLM call failed, will fall back to deterministic rationale: {exc}")
        logger.event(ctx, "ollama_call_completed", level="WARNING",
                     model=bundle.definition.llm.model, error=str(exc))
        # Tool calls still happened even though the narration call failed —
        # keep them, they're the grounded part of the run.
        ctx.pending_stage_detail = {"model": bundle.definition.llm.model, "llm_error": str(exc)}
        if tool_calls_made:
            ctx.pending_stage_detail["tool_calls"] = [
                {"name": c["name"], "arguments": c["arguments"], "result": c["result"]}
                for c in tool_calls_made
            ]
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
        think=llm_config.think,
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
