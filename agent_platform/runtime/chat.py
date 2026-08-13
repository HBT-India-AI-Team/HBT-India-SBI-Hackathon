"""Turns free-text conversation into the structured evidence invoke_agent()
already knows how to run, instead of asking a user for JSON. One LLM call
per turn extracts any field values mentioned so far (against the active
skills' own gates/factors — the only place real field names live, since
input_schema is generic) and drafts a natural reply; once every required
field is known, the existing pipeline runs unchanged and a templated,
deterministic reply reports the decision (no second LLM call, no risk of a
narrated summary drifting from the actual outcome).

Sessions are file-backed (agent_platform/runtime/chat_store.py) so a
multi-turn conversation survives a server restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_platform.composition import load_agent
from agent_platform.llm import OllamaAdapter, OllamaError
from agent_platform.skills import rules_engine
from agent_platform.stages.pipeline_stages import skill_evidence_fields

from . import chat_store
from .executor import invoke_agent

# Output contracts that declare this field opt this agent into the "rich content" chat
# path (_content_reply) instead of the plain {reply} guidance path — additive, never
# changes behavior for any agent that doesn't declare it.
_CONTENT_TYPE_FIELD = "content_type"

_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["extracted_fields", "reply"],
    "properties": {
        "extracted_fields": {
            "type": "object",
            "description": "Values the user has provided for the target fields, keyed by the exact field "
                            "name given. Only include a field if the user actually stated or implied a value "
                            "for it — never guess. Numbers as numbers, not strings.",
            "additionalProperties": True,
        },
        "reply": {
            "type": "string",
            "description": "A short, natural reply to the user — one or two sentences.",
        },
    },
}


def _build_adapter() -> OllamaAdapter:
    import os

    return OllamaAdapter(
        host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model="gemma4:12b",
        timeout_seconds=60,
    )


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    reply: str
    evidence: dict[str, Any]
    decision: dict[str, Any] | None
    done: bool
    # Only set on the rich-content path (_content_reply) — None for every other agent.
    content_type: str | None = None
    stage_trace: list[dict[str, Any]] | None = None


def _rule_bearing_fields(skills) -> list[dict]:
    fields: dict[str, dict] = {}
    for skill in skills:
        if not skill.has_rules:
            continue
        for f in skill_evidence_fields(skill):
            existing = fields.get(f["field"])
            if existing is None or (f["required"] and not existing["required"]):
                fields[f["field"]] = f
    return list(fields.values())


def _missing_required(evidence: dict, fields: list[dict]) -> list[dict]:
    return [f for f in fields if f["required"] and rules_engine.get_field(evidence, f["field"]) is None]


def _set_field(evidence: dict, path: str, value: Any) -> None:
    """The dotted-path write counterpart to rules_engine.get_field's dotted-
    path read — a field like "kyc.status" must land at evidence["kyc"]
    ["status"], not a literal evidence["kyc.status"] key, or get_field would
    never find it again on the next turn.
    """
    parts = path.split(".")
    node = evidence
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _merge_evidence(evidence: dict, extracted: dict) -> bool:
    """Merges extracted field values into evidence (dotted-path aware),
    returns whether anything actually changed.
    """
    changed = False
    for path, value in extracted.items():
        if rules_engine.get_field(evidence, path) != value:
            changed = True
        _set_field(evidence, path, value)
    return changed


def _decision_reply(decision: dict) -> str:
    outcome = decision.get("outcome", "UNKNOWN")
    reason = decision.get("reason", "")
    score = decision.get("composite_score")
    score_part = f" (score: {score})" if score is not None else ""
    return f"**{outcome}**{score_part}. {reason}"


def _extract(adapter: OllamaAdapter, agent_purpose: str, fields: list[dict], evidence: dict,
             messages: list[dict], decision: dict | None) -> tuple[dict, str]:
    field_lines = "\n".join(
        f"- {f['field']} ({'required' if f['required'] else 'optional'}): {f['description']}" for f in fields
    )
    known_lines = "\n".join(f"- {k}: {v}" for k, v in evidence.items()) or "(none yet)"
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    decision_note = (
        f"\nA decision has already been reached: {decision['outcome']} — {decision['reason']}. "
        "If the user is just asking about it, answer from this; don't ask for more fields unless "
        "they're clearly giving a correction."
        if decision else ""
    )

    system_prompt = (
        f"You are collecting information for: {agent_purpose}\n\n"
        f"Target fields:\n{field_lines}\n\n"
        f"Known so far:\n{known_lines}{decision_note}\n\n"
        "Extract any newly-provided field values from the latest user message, and write a short, "
        "natural reply. If required fields are still missing, ask about the next one or two — don't "
        "list all of them at once. Never fabricate a value the user didn't provide."
    )
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=system_prompt,
            user_prompt=transcript,
            schema=_EXTRACT_SCHEMA,
            temperature=0.0,
        )
    except OllamaError as exc:
        return {}, f"Sorry, I couldn't process that just now ({exc}). Could you try again?"

    extracted = parsed.get("extracted_fields") if isinstance(parsed, dict) else None
    reply = parsed.get("reply") if isinstance(parsed, dict) else None
    return (extracted if isinstance(extracted, dict) else {}), (reply or "Could you tell me more?")


def _has_content_type_contract(skills) -> bool:
    return any(
        s.output_contract and _CONTENT_TYPE_FIELD in (s.output_contract.get("required") or [])
        for s in skills
    )


def _content_reply(agent_id: str, session: dict) -> tuple[str, str, list[dict[str, Any]], dict | None]:
    """Runs this agent's own real pipeline for one turn (not a bespoke LLM
    call) — the "rich content" chat path: content_type-aware output plus a
    genuine stage-by-stage trace (RunContext.stage_results, populated by
    every pipeline run regardless of agent) surfaced as the chat UI's
    "thinking" log, rather than a fabricated one.
    """
    evidence = {
        "message": session["messages"][-1]["content"],
        "conversation_history": session["messages"][:-1][-6:],
        # No customer_id is injected. It used to default to a demo customer so
        # the account-lookup capabilities had someone to resolve, but any agent
        # holding those tools then preferred the fixture over what the user
        # actually typed -- answering a question about a ₹3,00,000 loan with
        # figures from a ₹3,18,500 one. Restoring this means restoring a real
        # authenticated identity from the session, never a default.
    }
    ctx = invoke_agent(agent_id, {"evidence": evidence})
    stage_trace = [
        {
            "stage": r.stage, "status": r.status, "summary": r.summary,
            "duration_ms": r.duration_ms, "detail": r.detail,
        }
        for r in ctx.stage_results
    ]
    if ctx.error:
        return "text", f"Something went wrong: {ctx.error['message']}", stage_trace, None

    output = ctx.validated_output or {}
    content_type = output.get(_CONTENT_TYPE_FIELD) or "text"
    content = output.get("content")
    if not content:
        content = "(no content produced)"
    return content_type, content, stage_trace, ctx.decision


def _guidance_reply(adapter: OllamaAdapter, skills, messages: list[dict]) -> str:
    guidance = "\n\n".join(s.instructions_text for s in skills if s.instructions_text)
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    schema = {"type": "object", "required": ["reply"], "properties": {"reply": {"type": "string"}}}
    try:
        parsed, _meta = adapter.generate_structured(
            system_prompt=f"Follow this guidance when responding:\n\n{guidance}",
            user_prompt=transcript,
            schema=schema,
            temperature=0.3,
        )
    except OllamaError as exc:
        return f"Sorry, I couldn't process that just now ({exc}). Could you try again?"
    reply = parsed.get("reply") if isinstance(parsed, dict) else None
    return reply or "Could you tell me more?"


def handle_chat_turn(agent_id: str, session_id: str | None, message: str) -> ChatTurnResult:
    bundle = load_agent(agent_id)
    if session_id is None:
        session_id = chat_store.new_session_id()
        session = chat_store.new_session(session_id, agent_id)
    else:
        session = chat_store.get_session(session_id) or chat_store.new_session(session_id, agent_id)

    session["messages"].append({"role": "user", "content": message})
    all_skills = list(bundle.skills.values())
    fields = _rule_bearing_fields(all_skills)
    adapter = _build_adapter()

    if not fields:
        if _has_content_type_contract(all_skills):
            content_type, content, stage_trace, decision = _content_reply(agent_id, session)
            session["messages"].append({"role": "assistant", "content": content})
            chat_store.save_session(session)
            return ChatTurnResult(
                session_id, content, session["evidence"], decision, False,
                content_type=content_type, stage_trace=stage_trace,
            )
        reply = _guidance_reply(adapter, all_skills, session["messages"])
        session["messages"].append({"role": "assistant", "content": reply})
        chat_store.save_session(session)
        return ChatTurnResult(session_id, reply, session["evidence"], None, False)

    extracted, drafted_reply = _extract(
        adapter, bundle.definition.purpose, fields, session["evidence"], session["messages"], session["decision"],
    )
    evidence_changed = _merge_evidence(session["evidence"], extracted)

    missing = _missing_required(session["evidence"], fields)
    if missing:
        reply = drafted_reply
        session["messages"].append({"role": "assistant", "content": reply})
        chat_store.save_session(session)
        return ChatTurnResult(session_id, reply, session["evidence"], None, False)

    if session["decision"] is None or evidence_changed:
        ctx = invoke_agent(agent_id, {"evidence": session["evidence"]})
        session["decision"] = ctx.decision
        reply = _decision_reply(ctx.decision) if ctx.decision else (ctx.error or {}).get(
            "message", "Something went wrong reaching a decision — please try again.",
        )
    else:
        reply = drafted_reply

    session["messages"].append({"role": "assistant", "content": reply})
    chat_store.save_session(session)
    return ChatTurnResult(session_id, reply, session["evidence"], session["decision"], session["decision"] is not None)
