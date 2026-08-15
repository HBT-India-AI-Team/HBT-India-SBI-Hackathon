"""
Phase 8: LLM-first conversation engine grounded in the Requirement Graph.
Falls back to the exact rule-based handler from Phase 3
(rule_based_engine.handle_message) for a turn if Ollama is unreachable,
times out, or returns invalid/unparseable JSON -- both paths ultimately
call submit_requirement_value(), so the fallback can't drift out of sync
with the LLM path's validation.
"""
import json
import logging

import httpx
from pydantic import BaseModel, ValidationError

from backend import config
from backend.services import requirement_graph as rg
from backend.services import rule_based_engine

logger = logging.getLogger("yono.llm")


class LLMAction(BaseModel):
    action: str  # submit_value | escalate_to_human | switch_language | none
    requirement_id: str | None = None
    value: str | None = None
    lang: str | None = None


class LLMResponse(BaseModel):
    reply_text: str
    actions: list[LLMAction] = []


def _discover_model(client: httpx.Client) -> str | None:
    if config.OLLAMA_MODEL:
        return config.OLLAMA_MODEL
    try:
        resp = client.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=config.OLLAMA_TIMEOUT_SECONDS)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return models[0]["name"] if models else None
    except Exception as e:
        logger.warning("[llm] model auto-discovery failed: %s", e)
        return None


def check_ollama_status():
    """Used by GET /admin/llm/status."""
    logger.info("[llm] check_ollama_status: probing %s/api/tags", config.OLLAMA_BASE_URL)
    try:
        with httpx.Client() as client:
            resp = client.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=config.OLLAMA_TIMEOUT_SECONDS)
            resp.raise_for_status()
            models = [mo["name"] for mo in resp.json().get("models", [])]
            active_model = config.OLLAMA_MODEL or (models[0] if models else None)
            logger.info("[llm] Ollama reachable at %s, active_model=%s, available=%s", config.OLLAMA_BASE_URL, active_model, models)
            return {"reachable": True, "base_url": config.OLLAMA_BASE_URL, "active_model": active_model, "available_models": models}
    except Exception as e:
        logger.warning("[llm] Ollama unreachable at %s: %s", config.OLLAMA_BASE_URL, e)
        return {"reachable": False, "base_url": config.OLLAMA_BASE_URL, "active_model": None, "error": str(e)}


def _build_system_prompt(application, history, next_req):
    outstanding = [
        {"id": r.id, "type": r.type, "label": r.label, "format_hint": r.format_hint, "state": r.state}
        for r in application.requirements if r.state not in ("VERIFIED",)
    ]
    return (
        "You are the YONO 3.0 onboarding assistant. You must reply with STRICT JSON only, "
        "matching this schema: {\"reply_text\": str, \"actions\": "
        "[{\"action\": \"submit_value\", \"requirement_id\": str, \"value\": str} "
        "| {\"action\": \"escalate_to_human\"} | {\"action\": \"switch_language\", \"lang\": str} "
        "| {\"action\": \"none\"}]}. No prose outside the JSON.\n\n"
        f"Product: {application.product_id}. Application status: {application.get_status()}.\n"
        f"Outstanding requirements: {json.dumps(outstanding)}\n"
        f"next_suggested_requirement: {next_req.id if next_req else None} ({next_req.type if next_req else None})\n"
        "Steer the user toward next_suggested_requirement unless their message is clearly a "
        "correction, question, or escalation request. You may propose multiple submit_value "
        "actions in one turn if the user provided multiple pieces of information at once.\n\n"
        f"Recent conversation: {json.dumps(history[-6:])}"
    )


def handle_message(application, text, db, scope=None, history=None):
    """Returns the same shape as rule_based_engine.handle_message. Falls
    back to the rule-based handler on any Ollama/parse failure."""
    next_req = rg.get_next_requirement(application, scope=scope)
    history = history or []

    try:
        with httpx.Client() as client:
            model = _discover_model(client)
            if not model:
                raise RuntimeError("no Ollama model available")
            system_prompt = _build_system_prompt(application, history, next_req)
            logger.debug("[llm] calling %s/api/generate model=%s application_id=%s", config.OLLAMA_BASE_URL, model, application.id)
            resp = client.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": f"{system_prompt}\n\nUser: {text}", "format": "json", "stream": False},
                timeout=config.OLLAMA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            parsed = LLMResponse.model_validate(json.loads(raw))
            logger.info("[llm] Ollama turn succeeded: application_id=%s model=%s actions=%d", application.id, model, len(parsed.actions))
    except Exception as e:
        logger.warning("[llm] falling back to rule-based engine for this turn (application_id=%s, reason: %s)", application.id, e)
        return rule_based_engine.handle_message(application, text, db, scope=scope)

    actions_applied = []
    for action in parsed.actions:
        if action.action == "submit_value" and action.requirement_id and action.value is not None:
            target = next((r for r in application.requirements if r.id == action.requirement_id), None)
            if target is None:
                logger.info("[llm] dropped action: unknown requirement_id %s", action.requirement_id)
                continue
            if scope == "guardian" and target.type not in ("guardian_consent", "guardian_mobile_otp"):
                logger.info("[llm] dropped action: guardian session tried to touch %s", target.type)
                continue
            result = rg.submit_requirement_value(application, action.requirement_id, action.value, db)
            actions_applied.append({"requirement_id": action.requirement_id, "type": target.type, "result": "accepted" if result.get("ok") else "rejected"})
        elif action.action == "escalate_to_human":
            from backend.models import models as m
            item = m.ReviewItem(application_id=application.id, type="support_request", reason="LLM-detected escalation request", status="open")
            db.add(item)
            db.flush()
            from backend.services import events
            events.emit("hitl_item_added", {"item_id": item.id, "application_id": application.id, "type": "support_request"})
            actions_applied.append({"action": "escalate_to_human"})
        elif action.action == "switch_language" and action.lang:
            application.user.language = action.lang
            db.flush()
            actions_applied.append({"action": "switch_language", "lang": action.lang})
        # "none" -> no-op

    db.refresh(application)
    return {
        "reply_text": parsed.reply_text,
        "actions_applied": actions_applied,
        "progress": rg.compute_progress(application),
    }
