"""
Channel-selection logic for OTP delivery (Phase 6 item 5).

Selection rule (documented also in /docs/ARCHITECTURE.md):
  - if OTP_DELIVERY_CHANNEL is explicitly set to telegram/email/sms, use it
  - else ("auto", the default):
      * if the Session's channel is "telegram" AND the User has a
        telegram_chat_id on file -> telegram_sender (REAL if bot token set)
      * elif the User has an email on file -> email_sender (REAL if SMTP set)
      * else -> sms_sender (always MOCK, see sms_sender.py)
"""
from backend import config
from backend.services.otp import telegram_sender, email_sender, sms_sender


def _pick_channel(application, requirement):
    if config.OTP_DELIVERY_CHANNEL != "auto":
        return config.OTP_DELIVERY_CHANNEL

    user = application.user
    latest_session = sorted(application.sessions, key=lambda s: s.started_at)[-1] if application.sessions else None
    is_guardian_req = requirement.type == "guardian_mobile_otp"

    if latest_session and latest_session.channel == "telegram" and user and user.telegram_chat_id:
        return "telegram"
    if user and getattr(user, "email", None):
        return "email"
    return "sms"


def send_otp(application, requirement, code):
    channel = _pick_channel(application, requirement)
    user = application.user

    if channel == "telegram":
        result = telegram_sender.send(user.telegram_chat_id if user else None, code)
    elif channel == "email":
        result = email_sender.send(user.email if user else None, code)
    else:
        target = requirement.value or (user.mobile_number if user else None)
        result = sms_sender.send(target, code)

    result.setdefault("channel", channel)
    return result
