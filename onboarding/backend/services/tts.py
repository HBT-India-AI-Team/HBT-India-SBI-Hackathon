"""
Phase 7: text-to-speech, real attempt against the same voice AI server as
stt.py (see /reference/voice_ai_server_client/), same real-call-with-
graceful-fallback pattern used throughout this repo (doc_parser.py,
otp/telegram_sender.py, stt.py above).

synthesize() POSTs {"text", "language", "speaker_style"?} (JSON) to
`{VOICE_SERVER_URL}/synthesize` with `Authorization: Bearer
{VOICE_SERVER_API_KEY}` and returns the raw WAV bytes from the response
body on success.

# MOCK fallback: if VOICE_SERVER_URL isn't configured, or the real call
# fails/times out (expected in this dev sandbox -- see
# backend/scripts/check_voice_server_connectivity.py), synthesize()
# returns None (never raises) -- callers must treat None as "no audio
# available" and degrade gracefully (text-only reply), never block the
# caller's flow on failure.
"""
import logging

import httpx

from backend import config

logger = logging.getLogger("yono.tts")


def synthesize(text: str, language: str = "en", speaker_style: str | None = None) -> bytes | None:
    """Returns raw WAV bytes on success, None if the voice server is
    unavailable. Never raises."""
    if not config.VOICE_SERVER_URL:
        logger.info("[MOCK][tts] no VOICE_SERVER_URL configured -- skipping synthesis, no audio returned")
        return None

    url = f"{config.VOICE_SERVER_URL}/synthesize"
    body = {"text": text, "language": language}
    if speaker_style:
        body["speaker_style"] = speaker_style

    logger.info("[tts] attempting real synthesis via %s (chars=%d, language=%s)", url, len(text or ""), language)
    try:
        with httpx.Client(timeout=httpx.Timeout(config.VOICE_SERVER_TIMEOUT_SECONDS, connect=config.VOICE_SERVER_CONNECT_TIMEOUT_SECONDS)) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {config.VOICE_SERVER_API_KEY}"},
                json=body,
            )
            resp.raise_for_status()
            audio_bytes = resp.content
        latency_ms = resp.headers.get("X-Latency-Ms")
        logger.info("[tts] real synthesis succeeded: %d bytes (latency_ms=%s)", len(audio_bytes), latency_ms)
        return audio_bytes
    except Exception as e:
        logger.info("[MOCK][tts] real synthesis unavailable (%s: %s) -- no audio returned, caller should degrade to text-only", type(e).__name__, e)
        return None
