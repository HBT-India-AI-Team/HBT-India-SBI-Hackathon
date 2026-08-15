"""
REAL SMTP integration for OTP delivery (Phase 6).

Uses smtplib against SMTP_HOST/PORT/USER/PASSWORD/FROM_ADDRESS (config.py).
If SMTP isn't configured (the case in this sandbox), falls back to a MOCK
"log what would be sent" path -- the smtplib call path itself is the real
integration and runs unchanged whenever credentials ARE present.

To make this real in a deployment, set:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_ADDRESS
"""
import logging
import smtplib
from email.mime.text import MIMEText

from backend import config

logger = logging.getLogger("yono.otp.email")


def send(to_address: str, code: str):
    if not config.SMTP_HOST or not to_address:
        logger.info("[MOCK][email] would send OTP %s to %s (no SMTP_HOST configured)", code, to_address)
        return {"real_send": False, "channel": "email", "mock_reason": "missing SMTP_HOST or recipient address"}

    msg = MIMEText(f"Your YONO 3.0 verification code is {code}. It expires in a few minutes.")
    msg["Subject"] = "Your YONO 3.0 verification code"
    msg["From"] = config.SMTP_FROM_ADDRESS or config.SMTP_USER
    msg["To"] = to_address

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=8) as server:
            server.starttls()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(msg["From"], [to_address], msg.as_string())
        return {"real_send": True, "channel": "email"}
    except Exception as e:  # pragma: no cover - network path
        logger.warning("[email] send failed, falling back to mock log: %s", e)
        logger.info("[MOCK][email] would send OTP %s to %s", code, to_address)
        return {"real_send": False, "channel": "email", "mock_reason": str(e)}
