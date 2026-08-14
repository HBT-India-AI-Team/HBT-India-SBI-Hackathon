"""
# MOCK: real SMS OTP requires DLT template registration in India (TRAI
# regulation), not feasible to obtain for a prototype/hackathon. This
# module always logs what would be sent and returns a mock result. See
# /docs/MOCKS.md.
"""
import logging

logger = logging.getLogger("yono.otp.sms")


def send(mobile_number: str, code: str):
    logger.info("[MOCK][sms] would send OTP %s to %s (SMS OTP is mocked -- see docs/MOCKS.md)", code, mobile_number)
    return {"real_send": False, "channel": "sms", "mock_reason": "SMS OTP requires DLT registration, not available in this build"}
