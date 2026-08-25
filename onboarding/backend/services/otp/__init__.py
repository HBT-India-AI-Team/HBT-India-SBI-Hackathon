"""
OTP generation/verification core (Phase 6 item 1). Channel senders
(telegram_sender.py, email_sender.py, sms_sender.py) live alongside this
file and are dispatched by send_otp() based on OTP_DELIVERY_CHANNEL logic.

Debug hooks (Phase 5, see /docs/DEBUG_HOOKS.md): in mock mode any 6-digit
code is accepted UNLESS it doesn't match what was generated -- for demo
determinism the generated code is also echoed into NotificationLog / logs
so a demo script can read it back without a real inbox.
"""
import hashlib
import logging
import random
from datetime import datetime, timedelta

from backend import config

logger = logging.getLogger("yono.otp")


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_otp(application, requirement, db):
    """Generates a 6-digit code, stores it hashed with an expiry on the
    Requirement row, and dispatches it via the appropriate channel sender
    (Phase 6). Returns the plaintext code ONLY for local/demo logging --
    never returned to the end API caller in a real deployment."""
    code = f"{random.randint(0, 999999):06d}"
    requirement.otp_code_hash = _hash_code(code)
    requirement.otp_expires_at = datetime.utcnow() + timedelta(seconds=config.OTP_EXPIRY_SECONDS)
    db.flush()

    from backend.services.otp.dispatch import send_otp
    send_result = send_otp(application, requirement, code)
    logger.info(
        "[OTP] application=%s requirement=%s code=%s (channel=%s, real_send=%s)",
        application.id, requirement.id, code, send_result.get("channel"), send_result.get("real_send"),
    )
    return {"code": code, **send_result}


def verify_otp(requirement, submitted_code: str):
    """Checks match + not expired. Returns {ok, error} with error being one
    of 'wrong_code' | 'expired_code' | None, per the frontend's separate
    copy requirement for each case."""
    if config.OTP_BYPASS:
        # See config.OTP_BYPASS. Deliberately ahead of every other check,
        # including no_otp_pending and expiry, so the step cannot block for any
        # reason while delivery is unavailable.
        logger.warning(
            "[OTP][BYPASS] accepting %r without verification (OTP_BYPASS=true) requirement=%s",
            submitted_code, requirement.id,
        )
        return {"ok": True, "error": None, "bypassed": True}
    if not requirement.otp_code_hash or not requirement.otp_expires_at:
        return {"ok": False, "error": "no_otp_pending"}
    if datetime.utcnow() > requirement.otp_expires_at:
        return {"ok": False, "error": "expired_code"}
    if _hash_code(submitted_code) != requirement.otp_code_hash:
        return {"ok": False, "error": "wrong_code"}
    return {"ok": True, "error": None}
