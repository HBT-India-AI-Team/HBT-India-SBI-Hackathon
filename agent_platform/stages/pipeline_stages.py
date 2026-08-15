"""The reusable lifecycle stages every agent's pipeline is built from.

None of these functions know they belong to "the Lead Qualification Agent"
— they read whatever the bundle's capabilities/skills/rules declare and
operate generically over RunContext. A future agent reuses whichever of
these stages fit its pipeline (declared in its own agent.yaml) and only
needs new stages for behaviour that's genuinely new.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re

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
                       tool_calls: list[dict] | None = None,
                       style: dict | None = None, voice: bool = False,
                       language: str | None = None, speech: dict | None = None) -> None:
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
    if style:
        detail["style"] = style
    if voice:
        detail["voice"] = True
    if language:
        detail["reply_language"] = language
    if speech:
        detail["speech"] = speech
    # This assignment replaces the slot rather than updating it, so anything a
    # stage wants in its detail has to arrive through this call.
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


# What different callers name the reply language. Both are in production and
# neither is ours to rename -- the frontend team did not know which of the two
# their own client sends, which is reason enough to accept both rather than
# make a demo depend on remembering.
_LANGUAGE_KEYS = ("language", "lang")

# What different callers name the prior turns. `conversation_history` is what
# our own chat route writes; `history` is what the app sends. Same shape --
# [{role, content}] -- and both were previously rendered into the prompt as a
# raw dict repr, because neither was declared here.
_HISTORY_KEYS = ("conversation_history", "history")


# Keys that steer the runtime and are not content. _build_text_prompt renders
# every *other* key straight into the user prompt, so anything added to
# raw_input and left out of this set is shown to the model as though the user
# had typed it. "style" was: the prompt carried a literal "style: True" line,
# and the tool loop -- which style is not even supposed to reach -- started
# picking different tools because of it. Reproducibly, 3 runs out of 3.
#
# Spellings a caller might reasonably pick are ALL listed, not just the one
# we happen to read. Missing an alias is worse than a no-op: `lang` was not
# here, so a client sending it got the language ignored *and* a literal
# "lang: ta" line appended to the question -- broken twice, silently, from
# one missing string.
_TEXT_ROUTING_KEYS = {
    "skill_id", "skill_ids", "correlation_id", "style", "voice",
    *_LANGUAGE_KEYS, *_HISTORY_KEYS, "name",
}

# How many prior turns to carry. Matches the window chat.py already applies to
# its own sessions, so a caller sending history inline and a caller resuming a
# stored session get the same amount of context rather than two behaviours.
_HISTORY_TURNS = 6

# A cap per turn, so one pasted wall of text cannot crowd out the instructions.
# Truncation is marked rather than silent -- a model that can see it was cut
# will ask, where one that cannot will confidently answer half a question.
_HISTORY_CHARS = 600


def _conversation_history(raw_input) -> list[dict]:
    """Prior turns, under whichever name and level the caller used.

    Our chat route calls it `conversation_history`; the app calls it `history`
    and nests it in `evidence`. Both are [{role, content}]. `text` and
    `direction` are also accepted because the app's own local store uses those,
    and a client that forwards its store directly is an easy mistake to make.
    """
    raw = _request_flag(raw_input, *_HISTORY_KEYS)
    if not isinstance(raw, list):
        return []

    turns: list[dict] = []
    for entry in raw[-_HISTORY_TURNS:]:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role") or entry.get("direction") or ""
        # "inbound"/"outbound" is the app's local vocabulary for the same thing.
        role = {"inbound": "user", "outbound": "assistant"}.get(role, role)
        content = entry.get("content") or entry.get("text") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()
        if len(content) > _HISTORY_CHARS:
            content = content[:_HISTORY_CHARS] + " […]"
        turns.append({"role": "assistant" if role == "assistant" else "user",
                      "content": content})
    return turns


def _history_section(turns: list[dict]) -> str:
    """A readable transcript, not a dict dumped into the question.

    This existed only by accident before: history was passed through the same
    generic renderer as every other field, so the model received a literal
    Python repr -- "history: [{'role': 'user', 'content': 'hi'}]" -- pasted
    into the user's own question. It worked, in the sense that a model will
    read anything, but nothing formatted it and nothing bounded it.
    """
    if not turns:
        return ""
    lines = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    return (
        "## Earlier in this conversation\n\n"
        f"{lines}\n\n"
        "Use this only to resolve what the new message refers to — \"what about "
        "15 years?\" means the loan above. Do not re-answer anything already "
        "answered, and do not treat it as a fact source: every figure still "
        "comes from a tool call made now.\n\n"
        "## The new message\n\n"
    )


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

    # Scrubbed at both levels, because callers nest flags at both. The voice
    # client sends them inside "evidence", so filtering only the top level
    # put a literal "'voice': True, 'style': True" into the prompt for the
    # model to read as though the user had said it.
    fields = {k: v for k, v in raw_input.items() if k not in _TEXT_ROUTING_KEYS}
    evidence = fields.get("evidence")
    if isinstance(evidence, dict):
        fields["evidence"] = {k: v for k, v in evidence.items() if k not in _TEXT_ROUTING_KEYS}
    body = "\n".join(f"{k}: {v}" for k, v in fields.items()) or "(no context provided)"
    # Prior turns are rendered as a transcript ahead of the new message rather
    # than dumped among the fields; see _history_section.
    user_prompt = _history_section(_conversation_history(raw_input)) + body
    return system_prompt, user_prompt


# What different callers name the user's own words. Neither is more correct;
# both are in production and neither is ours to rename.
_MESSAGE_KEYS = ("message", "question")


def _request_flag(raw_input, *names: str):
    """A runtime flag, at whichever level the caller nested it, under
    whichever of `names` they spelled it.

    Three shapes are live and all three are legitimate:

      /invoke, generic     {"question": ..., "voice": true}
      our chat route       {"evidence": {...}, "voice": true}
      the voice client     {"evidence": {"question": ..., "voice": true}}

    Reading one level only is not a hypothetical bug. It is how style spent
    its first week silently switched off, and how the voice client's own
    `voice: true` did nothing at all -- the flag was set, sent, and read at a
    level it was never at. Returns None when absent, which each caller reads
    as its own default.

    Level is checked outside spelling, so a caller sending `lang` at the top
    level and `language` inside evidence gets the top-level one -- same
    precedence every other flag already has, rather than a new rule per name.
    """
    if not isinstance(raw_input, dict):
        return None
    for candidate in (raw_input, raw_input.get("evidence")):
        if not isinstance(candidate, dict):
            continue
        for name in names:
            if name in candidate:
                return candidate[name]
    return None


def _user_message(raw_input) -> str:
    """The user's own words, wherever the caller happened to put them.

    Two levels and two spellings. /invoke passes fields at the top level and
    calls it `question`; our chat route nests them under "evidence" and calls
    it `message`. Reading only the top level, or only one name, looked correct
    and ran clean while returning no style on the path that mattered -- twice.
    """
    if not isinstance(raw_input, dict):
        return ""
    for candidate in (raw_input, raw_input.get("evidence")):
        if not isinstance(candidate, dict):
            continue
        for key in _MESSAGE_KEYS:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _style_enabled(raw_input) -> bool:
    """Off only when a caller says so in as many words.

    The Playground sends this from a toggle so a reviewer can put a styled
    and an unstyled answer side by side in one session. /invoke callers send
    nothing at all, and nothing means on -- a new flag must never quietly
    change what an existing integration already gets.
    """
    return _request_flag(raw_input, "style") is not False


def _style_section(ctx, logger) -> tuple[str, dict]:
    """Vernacular wording guidance, plus a record of whether any applied.

    Returns the prompt text and a small dict describing what happened, which
    the caller hands to _record_llm_detail. That reporting is not decoration:
    every path in here can legitimately produce nothing -- wrong script, no
    index, no embedding host, or a query that simply scored below the floor
    -- and all of them look identical from the outside. Twice now a wiring
    bug has hidden inside that silence. The trace says which it was.

    Imported lazily and inside a try: agent_platform is the generic runtime
    and capabilities_impl is one app built on it, so the runtime must stay
    importable without it. Nothing about grounding is allowed to depend on
    style, so every failure returns "" and the prompt goes out unchanged.
    """
    if not _style_enabled(ctx.raw_input):
        return "", {"applied": False, "reason": "turned off by the caller"}

    try:
        from capabilities_impl import style_examples
    except ImportError:
        return "", {"applied": False, "reason": "style module not installed"}

    message = _user_message(ctx.raw_input)
    if not message:
        return "", {"applied": False, "reason": "no user message found"}

    try:
        language = style_examples.language_of(message)
        guide = style_examples.register_guide(language)
        examples = style_examples.for_query(message, language=language) if language else []
        section = style_examples.as_prompt_section(examples, guide)
    except Exception as exc:                # noqa: BLE001 - style is never fatal
        logger.warning(ctx, f"Style lookup failed, answering unstyled: {exc}")
        return "", {"applied": False, "reason": f"lookup failed: {exc}"}

    detail = {
        "applied": bool(section),
        "language": language,
        "guide": bool(guide),
        "examples": len(examples),
    }
    if not section:
        # Only when nothing at all applied. With a guide in play the counts
        # above already say which half fired, and a "reason" beside them
        # would read as if the whole layer had been skipped.
        detail["reason"] = (
            "not a script with a style corpus" if not language
            # The common case, and the one that reads as a bug: the layer is
            # working and the corpus simply has nothing near this question.
            else f"no passage cleared {style_examples.MIN_SCORE}"
        )

    if examples:
        logger.event(ctx, "style_examples_used", count=len(examples))
    return section, detail


# A "_BREADTH_RULE" section was added here and then removed. Recorded because
# the removal is the useful part.
#
# It was written to fix Indic "tell me about X" questions that appeared to
# come back as "which scheme did you mean?" while the English version answered
# in full. That symptom was NOT REAL: the failing requests were sent with
# `curl -d` from Git Bash, which corrupted the Tamil and Devanagari UTF-8 in
# the request body. The backend was answering a mangled question correctly by
# saying it could not read it. Sending the identical payload from Python, the
# same questions answer in full in all three languages.
#
# Two things worth keeping from that. Anything testing Indic text over HTTP
# must build the body in Python or it measures its own terminal. And a prompt
# section added against an unverified symptom is not free -- this one made
# Tamil "tell me about FD" worse while it was in, on a 12B model already
# carrying ~18,000 characters of skill instructions.


_VOICE_BRIEF = (
    "\n\n## This answer will be spoken aloud, not read\n\n"
    "It goes to a text-to-speech engine and reaches the user as sound. That "
    "changes what a good answer looks like, and **these instructions override "
    "anything above about length and layout.**\n\n"
    # Scoped deliberately. An earlier draft said "length or formatting", and
    # the model read digit grouping as formatting: the same FD figure that
    # came out Rs 1,06,398.02 on screen came out Rs 106,398.02 spoken. An
    # Indian listener hears that as a hundred thousand, not a lakh.
    "**Nothing here relaxes how numbers, currency or names are written.** "
    "Indian digit grouping and lakh/crore still apply exactly as above — "
    "₹1,06,398.02, never ₹106,398.02 — and scheme names still come from the "
    "tool. Only the shape of the answer changes.\n\n"
    # Markdown is the first thing that breaks. Engines either read the
    # punctuation out ("asterisk asterisk") or strip it and run the sentences
    # together; neither is recoverable at the client.
    "**Plain sentences only. No markdown at all.** No `**bold**`, no bullet "
    "points, no numbered lists, no headings, no tables. If there are several "
    "options, say them in a sentence — \"there are two good options: SCSS at "
    "8.2 percent, or a senior citizen FD at 6.75\" — not as a list.\n\n"
    # Brackets survive the "no markdown" rule above, because a parenthesis is
    # ordinary prose punctuation rather than markup. Spoken, an engine either
    # reads them out or drops the pause, so the aside lands mid-clause.
    "**No brackets or parentheses.** If a detail needs saying, say it in the "
    "sentence — \"the equated monthly instalment, or EMI, works out to…\", not "
    "\"the EMI (equated monthly instalment) works out to…\".\n\n"
    # This one is ours before it is the speech engine's: sentences are cut on
    # a terminator followed by whitespace, so a reply written without final
    # punctuation does not split at all and arrives as one long chunk, which
    # is exactly the wait streaming exists to remove.
    "**End every sentence with `.`, `!` or `?`.** Each one is dispatched to be "
    "spoken the moment it is finished, and that is how the end is found. A "
    "sentence with no closing punctuation is not spoken on its own.\n\n"
    # Written for the Tamil path but true generally: the register guide already
    # says to use the everyday loanword, and this stops the model "correcting"
    # it into a dictionary calque no listener uses.
    "**Keep English words that are genuinely the everyday word** — bank and "
    "product names, and terms like loan, EMI, KYC, FD. Say them as they are "
    "said, inside the sentence structure of the user's own language. Do not "
    "translate them into a formal equivalent nobody uses, and do not switch "
    "whole clauses into English.\n\n"
    "**Two to four sentences.** Someone listening cannot skim, scroll back, "
    "or skip ahead, and a long answer is a long wait. Lead with the answer "
    "itself, then at most one sentence of why. Stop there.\n\n"
    # Brevity here comes from cutting whole sections, not from softening
    # facts. The distinction is the entire safety argument for this mode.
    "**Get shorter by cutting sections, never by cutting accuracy.** Drop the "
    "enumerated alternatives, the worked example, the background, the source "
    "URL. Keep every figure exactly as the tool returned it — do not round it, "
    "do not approximate it, do not drop the paise if they are not zero. Keep "
    "any threshold, age limit, eligibility condition or caveat that would "
    "change what the listener does. If the honest answer needs a warning, the "
    "warning is not the part you cut.\n\n"
    "**Say the source, don't spell it.** \"According to SBI\" or \"per RBI's "
    "rules\" — never a URL, and never an as-of date unless the user asked how "
    "current it is.\n\n"
    "**Ask at most one follow-up question, at the very end.** Speech is "
    "turn-taking; two questions in one breath cannot be answered.\n\n"
    # The image path emits a raw JSON object as `content`. Spoken, that is
    # a machine reading punctuation for twenty seconds.
    "**Never emit an image.** `content_type` is always `text` in this mode, "
    "whatever the user asked for. A JSON object read aloud is unusable — say "
    "what the chart would have shown instead.\n"
)


def _language_section(ctx, logger) -> tuple[str, str | None]:
    """Tell the model what language to answer in, rather than let it guess.

    Two sources, in order of trust:

      1. what the caller declared -- the voice client already sends
         `language`, and its ASR knows what it transcribed better than we can
         infer from the output
      2. Sarvam's language ID, if a key is configured

    Neither is required. With no declared language and no Sarvam key this
    returns "" and behaviour is exactly what it is today: the model infers
    from the text, which is right most of the time.

    It is the "most of the time" that this is for. A Tamil question came back
    answered in Telugu -- both Indic, the ASR transcript was garbled, and our
    own detection is a Unicode range, which cannot separate Tamil from
    Malayalam or Hindi from Marathi. A wrong language is not a bad answer, it
    is a useless one.
    """
    try:
        from capabilities_impl import sarvam
    except ImportError:
        sarvam = None

    declared = _request_flag(ctx.raw_input, *_LANGUAGE_KEYS)
    code = declared.strip() if isinstance(declared, str) and declared.strip() else None
    source = "declared by the caller"
    if code and sarvam is not None:
        # "ta-IN" asks the model to know a BCP-47 table; "Tamil" does not.
        code = sarvam.language_name(code)

    if not code:
        if sarvam is None or not sarvam.available():
            return "", None
        message = _user_message(ctx.raw_input)
        if not message:
            return "", None
        try:
            detected = sarvam.identify_language(message)
        except Exception as exc:               # noqa: BLE001 - never fatal
            logger.warning(ctx, f"Language detection failed, letting the model infer: {exc}")
            return "", None
        if not detected:
            return "", None
        code, source = detected.get("name") or detected.get("code"), "detected"

    if not code:
        return "", None

    logger.event(ctx, "reply_language_pinned", language=code, source=source)
    return (
        "\n\n## Answer in this language\n\n"
        f"The user is writing in **{code}**. Reply in that language and its "
        "script, whatever the text below looks like — it may be a speech "
        "transcript, and transcripts of Indian languages are frequently "
        "garbled or partly romanized. Do not switch to a different language "
        "because the input looks unclear.\n\n"
        # This escape hatch was one sentence and far too easy to reach. On
        # gemma4:12b it fired on clean Tamil: "சேமிப்பு கணக்கு வட்டி எவ்வளவு?"
        # -- a plain savings-rate question -- came back as "sorry, I did not
        # understand, please ask clearly", as did two of four Tamil-script
        # questions tested. Answering the obvious reading of a slightly odd
        # question is almost always right; refusing a clear one is always
        # wrong, so the bar is now explicit and the default is to answer.
        "**Assume you can understand it.** Transcripts drop letters and "
        "mangle spelling, and the intended question is nearly always "
        "recoverable — a question about வட்டி, ரேட், FD, லோன் or கணக்கு is a "
        "question about interest, rates, deposits, loans or accounts however "
        "it is spelled. Answer the reading that makes sense.\n\n"
        "Only say you did not understand when there is genuinely no question "
        "you can identify at all — not because the wording is unusual, "
        "colloquial, mixed with English, or missing a word. If you can name "
        "the topic, you can answer it.\n",
        code,
    )


# Indic blocks, by every name _language_section might hand back -- it returns
# a display name ("Tamil") when Sarvam is importable and the raw code ("ta")
# when it is not, so both must resolve or the check silently stops running.
_REPLY_SCRIPTS: tuple[tuple[frozenset[str], str, re.Pattern[str]], ...] = (
    (frozenset({"ta", "ta-in", "tamil"}), "Tamil", re.compile("[஀-௿]")),
    (frozenset({"hi", "hi-in", "hindi", "mr", "marathi"}), "Devanagari", re.compile("[ऀ-ॿ]")),
    (frozenset({"te", "te-in", "telugu"}), "Telugu", re.compile("[ఀ-౿]")),
    (frozenset({"kn", "kn-in", "kannada"}), "Kannada", re.compile("[ಀ-೿]")),
    (frozenset({"ml", "ml-in", "malayalam"}), "Malayalam", re.compile("[ഀ-ൿ]")),
    (frozenset({"bn", "bn-in", "bengali"}), "Bengali", re.compile("[ঀ-৿]")),
    (frozenset({"gu", "gu-in", "gujarati"}), "Gujarati", re.compile("[઀-૿]")),
    (frozenset({"pa", "pa-in", "punjabi"}), "Gurmukhi", re.compile("[਀-੿]")),
    (frozenset({"or", "od", "or-in", "odia"}), "Odia", re.compile("[଀-୿]")),
)


def _wrong_script(content: str, language: str | None) -> str | None:
    """The script that took over instead, or None if the reply is acceptable.

    Compares Indic blocks against each other rather than measuring how much of
    the reply is Tamil. A correct Tamil answer here is full of English -- "FD",
    "Fixed Deposit", "interest rate" -- so a share-of-characters test either
    sits so low it catches nothing or rejects good answers. What actually goes
    wrong is a *different Indic language*: a Tamil question answered in Telugu,
    which both scripts-are-Indic and looks-plausible let straight through.

    Returns None when it cannot judge -- English, an unlisted language, or a
    reply with no Indic characters at all (which is a legitimate answer to a
    question asked in romanized Tamil).
    """
    if not content or not language:
        return None
    key = language.strip().lower()
    expected = next((entry for entry in _REPLY_SCRIPTS if key in entry[0]), None)
    if expected is None:
        return None

    counts = [(name, len(pattern.findall(content))) for _, name, pattern in _REPLY_SCRIPTS]
    mine = dict(counts)[expected[1]]
    intruder, most = max(counts, key=lambda pair: pair[1])
    if most == 0:                       # no Indic script at all — not our call
        return None
    if intruder != expected[1] and most > mine:
        return intruder
    return None


def _has_sentence_sink() -> bool:
    """Is anything waiting to receive sentences as they are written?

    Lazily imported for the same reason as style: agent_platform is the
    generic runtime and must stay importable without the streaming layer.
    """
    try:
        from agent_platform.llm import speech_stream
    except ImportError:
        return False
    return speech_stream.sentence_sink.get() is not None


def _stream_answer(adapter, system_prompt, user_prompt, schema, temperature,
                   language, ctx, logger, voice: bool = False,
                   ) -> tuple[dict | None, dict | None, dict | None]:
    """Stream the spoken answer, forwarding each sentence as it completes.

    Returns (parsed, meta, speech_detail), or (None, None, detail) to mean
    "use the ordinary path instead". Everything here degrades: a stream that
    dies, a speech box that is down, or a reply that does not parse all fall
    back to generate_structured, which has the retry a stream cannot.

    Why a stream cannot retry: by the time one fails, part of it has already
    been spoken. Replaying from the top would repeat audio the listener has
    heard, which is worse than the pause of starting over silently.
    """
    try:
        from agent_platform.llm.speech_stream import stream_to_speech
    except ImportError:
        return None, None, None

    try:
        result = asyncio.run(stream_to_speech(
            adapter, system_prompt=system_prompt, user_prompt=user_prompt,
            schema=schema, temperature=temperature, language=language,
            # Only spoken sentences get stripped of markdown. With voice off
            # this same stream draws the on-screen bubble, where `**bold**` is
            # wanted.
            normalize=voice,
        ))
    except Exception as exc:                    # noqa: BLE001 - falls back
        logger.warning(ctx, f"Answer stream failed, answering without streaming: {exc}")
        return None, None, {"streamed": False, "reason": f"{type(exc).__name__}: {exc}"}

    detail = {
        "streamed": True,
        "sentences": len(result.sentences),
        "first_sentence_ms": result.first_sentence_ms,
        "forwarded": sum(1 for s in result.sentences if s.forwarded),
        "timings_ms": [s.elapsed_ms for s in result.sentences],
    }
    if result.parsed is None:
        # The words were spoken but the JSON wrapper is unusable. Regenerating
        # will repeat the audio; that is still better than returning nothing,
        # and it is rare enough to be worth the duplication.
        logger.warning(ctx, "Streamed answer did not parse as JSON; regenerating unstreamed")
        detail["reason"] = "streamed output was not valid JSON"
        return None, None, detail

    logger.event(ctx, "answer_streamed", sentences=detail["sentences"],
                 first_sentence_ms=detail["first_sentence_ms"])
    return result.parsed, result.meta, detail


def _voice_enabled(raw_input) -> bool:
    """On only when a caller says so explicitly.

    Opposite default to style: style shapes every answer unless switched off,
    while voice restructures one for a channel most callers are not on.
    Anything but an explicit true leaves the answer as it is.
    """
    return _request_flag(raw_input, "voice") is True


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
def _tool_usage_instructions() -> str:
    """Hard instruction to make EMI/FIRE questions run through real finance tools.

    The model can otherwise answer from intuition on simple arithmetic, which is
    exactly the failure mode we are trying to prevent in the chat UI. Keep this
    explicit and mandatory so tool calls are part of the normal chat flow.
    """
    return (
        "Mandatory finance-tool rule: if the user asks about an EMI, loan payment, monthly payment, "
        "loan repayment schedule, FIRE target, retirement corpus, monthly goal, or target investment, "
        "you MUST call the relevant finance tool before answering, even when the question is simple or "
        "looks trivial. Do not compute manually, do not estimate, and do not answer from intuition. "
        "Use the tool result as the source of truth. If required parameters are missing, ask only for the "
        "missing values and then call the tool. For EMI and FIRE questions, tool use is required."
    )


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
    "india.get_card_offers": {
        "type": "function",
        "function": {
            "name": "india.get_card_offers",
            "description": "Look up current SBI DEBIT CARD merchant offers and discounts — Amazon "
                            "Fresh/Grocery/Pharma, BigBasket, Flipkart Minutes, Flipkart Travel, "
                            "Cleartrip, Apollo 24|7, Reliance Digital, and the business-card bundle "
                            "(Google Workspace, MediBuddy, Awfis, FabHotels, DHL, Cleartax). Use it "
                            "for any question about offers, discounts, cashback or deals on a debit "
                            "card, including 'what offers are running', 'is there anything on "
                            "groceries' and 'do I get a discount at <merchant>'. Each offer comes "
                            "back with its dates already checked against today: never describe one "
                            "whose status is 'expired' or 'upcoming' as available now, and say when "
                            "it ran instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant": {
                        "type": "string",
                        "description": "Merchant name to match loosely — amazon, bigbasket, "
                                        "flipkart, cleartrip, apollo, reliance, dhl. Omit for all.",
                    },
                    "category": {
                        "type": "string",
                        "description": "One of: grocery, travel, pharmacy, electronics, "
                                        "quick_commerce, business, activation_voucher.",
                    },
                    "include_inactive": {
                        "type": "boolean",
                        "description": "True to also return expired and not-yet-started offers — "
                                        "needed for 'was there an offer on X' or 'when does it start'.",
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


# Words that mean "what can I save money on", across the three languages this
# agent answers in. Deliberately broad, because the pairing below is what makes
# it precise.
_OFFER_WORD = re.compile(
    r"\boffers?\b|\bdiscount|\bcashback\b|\bdeals?\b|\bsale\b|\bcoupon"
    r"|ஆஃபர்|சலுகை|தள்ளுபடி|டிஸ்கவுண்ட்"
    r"|ऑफर|छूट|डिस्काउंट|कैशबैक|सेल",
    re.IGNORECASE)

# …paired with something that makes it a CARD-offer question rather than
# "what does SBI offer for savings", which must not drag a merchant catalogue
# into an answer about deposits.
_OFFER_CONTEXT = re.compile(
    r"\bcards?\b|\bdebit\b|\bcredit\b|கார்டு|कार्ड|डेबिट"
    r"|amazon|bigbasket|big basket|flipkart|cleartrip|apollo|reliance"
    r"|jio|swiggy|zomato|dhl|awfis|fabhotels|cleartax|medibuddy|google workspace",
    re.IGNORECASE)


def _ensure_offer_lookup(ctx, tool_calls_made: list[dict], logger) -> None:
    """Fetch the card offers when the question is about them and the model
    did not think to ask.

    Offers exist nowhere but this capability -- not in the skill text, not in
    the document corpus -- so a missed tool call is not a degraded answer, it
    is the model saying "I don't have a list of current offers" while holding
    a list of current offers. That is what it did: asked "sbi current debit
    card offers" it reached for docs.search and answered from RBI card
    guidance instead.

    A mandatory instruction was the other option and is weaker; the tool rule
    for EMI has to be restated in the prompt and still depends on the model
    obeying it. This is the same reasoning as deriving calculator widgets in
    code rather than asking the model to emit them.

    Never fatal, and never a duplicate: if the loop already called it, this
    does nothing.
    """
    if any(c.get("name") == "india.get_card_offers" for c in tool_calls_made):
        return
    message = _user_message(ctx.raw_input)
    if not (message and _OFFER_WORD.search(message) and _OFFER_CONTEXT.search(message)):
        return
    if not DEFAULT_REGISTRY.has("india.get_card_offers"):
        return
    try:
        result = DEFAULT_REGISTRY.invoke("india.get_card_offers")
    except Exception as exc:                    # noqa: BLE001 - decoration only
        logger.warning(ctx, f"Offer lookup failed, answering without it: {exc}")
        return
    tool_calls_made.append(
        {"name": "india.get_card_offers", "arguments": {}, "result": result})
    logger.event(ctx, "offer_lookup_forced", count=result.get("count"))


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
            tool_rule = _tool_usage_instructions()
            adapter.run_tool_loop(
                system_prompt=(
                    system_prompt
                    + "\n\n"
                    + tool_rule
                    + "\n\nCall the available tools whenever they let you answer with a real number instead "
                    "of an estimate."
                ),
                user_prompt=user_prompt,
                tools=tools,
                resolve_tool=resolve_tool,
                max_turns=3,
                temperature=bundle.definition.llm.temperature,
            )
        except OllamaError as exc:
            logger.warning(ctx, f"Tool-calling loop failed, answering without tool results: {exc}")

    _ensure_offer_lookup(ctx, tool_calls_made, logger)
    logger.event(ctx, "tool_calls_made", calls=[c["name"] for c in tool_calls_made])

    if tool_calls_made:
        tool_lines = "\n".join(
            f"- {c['name']}({c['arguments']}) -> {c['result']}" for c in tool_calls_made
        )
        user_prompt = (
            f"{user_prompt}\n\nReal tool results already gathered — use these exact values in your "
            f"answer, don't recompute or guess them:\n{tool_lines}"
        )

    # Style goes on THIS call and not the tool loop above. The loop picks
    # tools and writes English, numeric arguments -- vernacular guidance
    # there is noise against a selection step that is already delicate. This
    # call is the one that writes prose the user reads.
    style_text, style_detail = _style_section(ctx, logger)
    # Voice goes last on purpose. It contradicts style directly -- style says
    # "say everything you would have said, the same length", voice says "two
    # to four sentences" -- and the one that has to win is the one that knows
    # the answer is going to be spoken. Last word in the prompt is how that
    # is expressed.
    voice = _voice_enabled(ctx.raw_input)
    language_text, language = _language_section(ctx, logger)
    answer_prompt = system_prompt + language_text + style_text + (_VOICE_BRIEF if voice else "")

    adapter = _build_adapter(bundle)
    logger.event(ctx, "ollama_call_started", model=bundle.definition.llm.model)
    speech: dict | None = None
    try:
        parsed = meta = None
        # Streaming is decided by whether anything is consuming sentences, not
        # by voice mode. The two are independent: voice changes how an answer
        # is *written* (short, no markdown), streaming changes how it is
        # *delivered*. A text client watching an answer appear wants streaming
        # without the brevity, and both combinations are legitimate.
        #
        # With nobody listening it takes the ordinary path, which has the
        # retry that a stream cannot -- there is no point paying that cost for
        # sentences nothing will read.
        if _has_sentence_sink():
            parsed, meta, speech = _stream_answer(
                adapter, answer_prompt, user_prompt, output_contract,
                bundle.definition.llm.temperature, language, ctx, logger, voice,
            )
        if parsed is None:
            parsed, meta = adapter.generate_structured(
                system_prompt=answer_prompt, user_prompt=user_prompt,
                schema=output_contract, temperature=bundle.definition.llm.temperature,
            )

        # The prompt asks for a language; this checks it got one. Measured on
        # gemma4:12b, a Tamil question with language "ta" declared, read and
        # pinned still came back written in Telugu -- the exact failure the
        # declaration exists to prevent. A caller cannot detect that, and a
        # user reading an answer in a language they do not speak has been
        # given nothing, so it is worth one more call to fix.
        intruder = _wrong_script((parsed or {}).get("content", ""), language)
        if intruder is not None:
            logger.warning(ctx, f"Reply came back in {intruder}, not {language}; regenerating")
            logger.event(ctx, "reply_language_wrong", expected=language, got=intruder)
            retry_parsed, retry_meta = adapter.generate_structured(
                system_prompt=(
                    answer_prompt
                    + f"\n\n## Your previous attempt was written in {intruder}\n\n"
                    f"That is the wrong language and the user cannot read it. Write "
                    f"this answer in **{language}** and its own script, every "
                    f"sentence of it. The facts and figures stay exactly the same; "
                    f"only the language changes.\n"
                ),
                user_prompt=user_prompt, schema=output_contract,
                temperature=bundle.definition.llm.temperature,
            )
            # Kept only if it actually fixed the problem. A second wrong-language
            # answer is no better than the first, and the first at least came
            # from the unmodified prompt.
            if _wrong_script((retry_parsed or {}).get("content", ""), language) is None:
                parsed, meta = retry_parsed, retry_meta
                logger.event(ctx, "reply_language_corrected", language=language)

        ctx.llm_output = parsed
        _record_llm_detail(ctx, meta, prompt_chars=len(answer_prompt) + len(user_prompt),
                           tool_calls=tool_calls_made, style=style_detail, voice=voice,
                           language=language, speech=speech)
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
