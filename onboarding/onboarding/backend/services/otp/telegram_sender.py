"""
REAL Telegram Bot API integration for OTP delivery (Phase 6).

Uses the Bot API's sendMessage call against TELEGRAM_BOT_TOKEN (config.py).
If no token is configured (the case in this sandbox -- no bot has been
registered/credentialed here), this module detects that and falls back to
a MOCK "log what would be sent" path. The HTTP call itself (the real
integration code path) is exercised whenever a token IS present -- nothing
else needs to change to "go live".

To make this real in a deployment:
  - set TELEGRAM_BOT_TOKEN (from @BotFather)
  - ensure the target User has a telegram_chat_id on file (captured when
    they first message the bot -- see webhooks.py)
"""
import logging

import requests

from backend import config

logger = logging.getLogger("yono.otp.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"


def send(chat_id: str, code: str):
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        logger.info("[MOCK][telegram] would send OTP %s to chat_id=%s (no TELEGRAM_BOT_TOKEN configured)", code, chat_id)
        return {"real_send": False, "channel": "telegram", "mock_reason": "missing TELEGRAM_BOT_TOKEN or chat_id"}

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    text = f"Your YONO 3.0 verification code is {code}. It expires in a few minutes."
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=8)
        resp.raise_for_status()
        return {"real_send": True, "channel": "telegram", "response": resp.json()}
    except Exception as e:  # pragma: no cover - network path
        logger.warning("[telegram] send failed, falling back to mock log: %s", e)
        logger.info("[MOCK][telegram] would send OTP %s to chat_id=%s", code, chat_id)
        return {"real_send": False, "channel": "telegram", "mock_reason": str(e)}
