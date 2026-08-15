"""
/backend/routers/webhooks.py -- Telegram webhook receiver (fills the gap
flagged in /docs/ARCHITECTURE.md's "Channel handoff / guardian links"
section and /docs/MOCKS.md: previously nothing ever populated
`User.telegram_chat_id`, so the telegram OTP path in
`services/otp/dispatch.py::_pick_channel` was practically unreachable
outside manually poking the DB).

This is a REAL webhook receiver -- it accepts Telegram's standard Bot API
"Update" payload shape (https://core.telegram.org/bots/api#update) and
does real work with it:

  POST /webhooks/telegram
  {
    "message": {
      "chat": {"id": 12345, ...},
      "text": "/start <handoff_token>",   # or just "<handoff_token>"
      "from": {"id": ..., "username": ..., ...}
    },
    ...
  }

Handling:
  1. If the message text looks like a handoff token (either Telegram's
     `/start <payload>` deep-link convention -- see
     `handoff_tokens.build_deep_link()`'s `?start={token}` links -- or the
     raw token by itself), consume it via
     `services/handoff_tokens.py::consume_handoff_token()`, resolve the
     Application it belongs to, set `telegram_chat_id` on that
     Application's User, and open a new `Session(channel="telegram")`
     against that Application. This mirrors the Phase 10 webhook-based
     handoff consumption described in the build spec.
  2. If there's no token (a fresh/unrelated message), best-effort: if a
     User already has this `chat_id` on file (a returning user messaging
     again), just touch it; otherwise log that an unrecognized/unlinked
     telegram message arrived and return a benign 200 -- never crash on
     this case, since arbitrary text messages from Telegram users we can't
     yet identify are expected, not an error condition.

What this does NOT do (see /docs/MOCKS.md for the precise real-vs-mocked
line): it doesn't call Telegram's `setWebhook` API for you, and it can't
receive real traffic from Telegram until (a) `TELEGRAM_BOT_TOKEN` is set to
a real bot token from @BotFather and (b) this endpoint is reachable at a
public HTTPS URL that's been registered with `setWebhook`. In this sandbox
there is no such public URL, so this route is only reachable by POSTing to
it directly (as the demo/verification step does) -- that's expected.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session as DBSession

from backend.models.db import get_db
from backend.models import models as m
from backend.services import events
from backend.services.handoff_tokens import consume_handoff_token

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("yono.routers.webhooks")


def _extract_token_candidate(text: str | None) -> str | None:
    """Telegram deep links (`https://t.me/<bot>?start=<token>`) cause the
    client to send the message text `/start <token>` when the user opens
    the link and hits "Start". Also accept the raw token by itself (e.g. a
    user who pastes/forwards just the token text)."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else None
    # A bare message that isn't an obvious command -- treat as a possible
    # raw-token paste. consume_handoff_token() will reject anything that
    # isn't actually a valid/unexpired/unused token, so this is safe to
    # attempt speculatively.
    if not text.startswith("/"):
        return text
    return None


@router.post("/telegram")
async def telegram_webhook(request: Request, db: DBSession = Depends(get_db)):
    """Minimal real Telegram webhook receiver. Always returns 200 with a
    small JSON body describing what happened -- Telegram retries webhooks
    that don't return 2xx, and we never want a malformed/unexpected update
    shape to cause retry storms, so this handler is deliberately
    defensive/best-effort throughout."""
    try:
        payload = await request.json()
    except Exception as e:
        logger.info("[webhooks][telegram] could not parse request body as JSON: %s", e)
        return {"ok": False, "reason": "invalid_json"}

    message = payload.get("message") or payload.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    from_user = message.get("from") or {}

    if chat_id is None:
        logger.info("[webhooks][telegram] update with no message.chat.id, ignoring: %s", payload)
        return {"ok": False, "reason": "no_chat_id"}

    chat_id = str(chat_id)
    logger.info(
        "[webhooks][telegram] inbound message from chat_id=%s (username=%s): %r",
        chat_id, from_user.get("username"), text,
    )

    token_candidate = _extract_token_candidate(text)
    if token_candidate:
        result = consume_handoff_token(token_candidate, db)
        if result["ok"]:
            app = db.query(m.Application).filter_by(id=result["application_id"]).first()
            if app is None:
                logger.warning(
                    "[webhooks][telegram] handoff token consumed but application_id=%s not found",
                    result["application_id"],
                )
                return {"ok": False, "reason": "application_not_found", "application_id": result["application_id"]}

            user = app.user
            if user is None:
                # Defensive fallback only -- Application.user_id is
                # non-nullable in practice, but don't crash if the data
                # is ever in a surprising state.
                user = m.User(language="en")
                db.add(user)
                db.flush()
                app.user_id = user.id

            user.telegram_chat_id = chat_id

            session = m.Session(application_id=app.id, channel="telegram", scope=result.get("scope"))
            db.add(session)
            db.commit()
            db.refresh(session)

            events.emit(
                "requirement_updated",
                {
                    "application_id": app.id,
                    "event": "telegram_linked",
                    "user_id": user.id,
                    "session_id": session.id,
                },
            )

            logger.info(
                "[webhooks][telegram] linked chat_id=%s to user_id=%s (application_id=%s), opened session_id=%s",
                chat_id, user.id, app.id, session.id,
            )
            return {
                "ok": True,
                "linked": True,
                "application_id": app.id,
                "user_id": user.id,
                "session_id": session.id,
                "scope": result.get("scope"),
            }

        logger.info(
            "[webhooks][telegram] message from chat_id=%s looked like a handoff token but failed to consume (%s) -- "
            "treating as a regular/unlinked message",
            chat_id, result.get("error"),
        )

    # No token, or token consumption failed -- best-effort: if this
    # chat_id is already linked to a User (returning user messaging
    # again), nothing further to do. Otherwise this is an
    # unrecognized/unlinked telegram message; log and move on.
    existing_user = db.query(m.User).filter_by(telegram_chat_id=chat_id).first()
    if existing_user:
        logger.info(
            "[webhooks][telegram] message from already-linked chat_id=%s (user_id=%s), no action needed",
            chat_id, existing_user.id,
        )
        return {"ok": True, "linked": False, "already_linked_user_id": existing_user.id}

    logger.info(
        "[webhooks][telegram] unrecognized/unlinked telegram message from chat_id=%s -- no handoff token and no "
        "existing User with this chat_id on file; nothing to link yet",
        chat_id,
    )
    return {"ok": True, "linked": False, "reason": "no_handoff_token_and_no_existing_link"}
