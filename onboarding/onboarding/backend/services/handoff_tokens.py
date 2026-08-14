"""
Generic short-lived single-use token mechanism (Phase 10), reused as-is by
Phase 9 for guardian session links -- both are just "give someone a way in
to an existing Application without full re-auth".
"""
import secrets
from datetime import datetime, timedelta

from backend import config
from backend.models import models as m


def generate_handoff_token(application_id: str, target_channel: str, db, purpose: str = "handoff", extra: dict | None = None):
    token = secrets.token_urlsafe(24)
    row = m.HandoffToken(
        token=token,
        application_id=application_id,
        target_channel=target_channel,
        purpose=purpose,
        extra=extra or {},
        used=False,
        expires_at=datetime.utcnow() + timedelta(seconds=config.HANDOFF_TOKEN_TTL_SECONDS),
    )
    db.add(row)
    db.flush()
    return token


def consume_handoff_token(token: str, db):
    """Validates not expired/not used, marks used, returns the application_id
    (and scope, for the guardian-link reuse case)."""
    row = db.query(m.HandoffToken).filter_by(token=token).first()
    if row is None:
        return {"ok": False, "error": "token_not_found"}
    if row.used:
        return {"ok": False, "error": "token_already_used"}
    if datetime.utcnow() > row.expires_at:
        return {"ok": False, "error": "token_expired"}
    row.used = True
    db.flush()
    scope = "guardian" if row.purpose == "guardian_link" else row.extra.get("scope") if row.extra else None
    return {"ok": True, "application_id": row.application_id, "target_channel": row.target_channel, "scope": scope}


def build_deep_link(channel: str, token: str) -> str:
    if channel == "whatsapp":
        return f"https://wa.me/{config.WHATSAPP_BOT_NUMBER}?text={token}"
    if channel == "telegram":
        return f"https://t.me/{config.TELEGRAM_BOT_USERNAME}?start={token}"
    # web / generic
    return f"/continue?token={token}"
