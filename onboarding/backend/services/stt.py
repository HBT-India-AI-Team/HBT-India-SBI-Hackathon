"""
Phase 7: speech-to-text for voice messages.

transcribe() now genuinely attempts a real STT call first, against the
separately-running voice AI server documented in
`/reference/voice_ai_server_client/` (own Whisper-shaped STT model, run by
the user on their own machine, exposed here via `VOICE_SERVER_URL` /
`VOICE_SERVER_API_KEY` in backend/config.py): `POST
{VOICE_SERVER_URL}/transcribe`, multipart form with the audio file,
`Authorization: Bearer {VOICE_SERVER_API_KEY}`. Same pattern already used
elsewhere in this repo (doc_parser.py's `_try_vlm_classify`/`_try_vlm_extract`,
otp/telegram_sender.py) -- real call, real (generous, since this is a
slower model than Ollama text generation) timeout, real try/except, INFO
(not WARNING/ERROR) logging on both the attempt and the expected fallback.

# MOCK fallback: if VOICE_SERVER_URL isn't configured, or the real call
# fails/times out (expected in this dev sandbox -- no network path to the
# configured ngrok endpoint; see /docs/MOCKS.md and
# backend/scripts/check_voice_server_connectivity.py), transcribe() falls
# back to a canned placeholder string, clearly tagged `_mock: True` in the
# returned dict.
#
# The wiring IS real either way: POST /sessions/{id}/voice
# (routers/sessions.py) calls transcribe(audio_file) and then feeds the
# resulting text through the exact same message-handling path as a normal
# text message (rule-based engine, or the LLM engine once
# ONBOARDING_ENGINE_MODE=llm) -- voice is not a parallel pipeline, just a
# different way of producing text input.
#
# Known limitation to document, not fix here: Whisper-family STT's
# Hindi/vernacular accuracy is meaningfully weaker than English.
"""
import logging
import os

import httpx

from backend import config

logger = logging.getLogger("yono.stt")

_CANNED_TRANSCRIPT = "my mobile number is 9876543210"


def _try_real_transcribe(audio_file: str, language: str | None = None) -> dict:
    """Real attempt at STT via the voice AI server's /transcribe endpoint.
    Raises on any failure -- caller (transcribe()) falls back to the canned
    mock. On a machine with real network access to VOICE_SERVER_URL, this
    is the live code path with no further code changes needed."""
    if not config.VOICE_SERVER_URL:
        raise RuntimeError("no VOICE_SERVER_URL configured")

    url = f"{config.VOICE_SERVER_URL}/transcribe"
    logger.info("[stt] attempting real transcription via %s (file=%s)", url, audio_file)

    filename = os.path.basename(audio_file) or "audio.wav"
    with open(audio_file, "rb") as f:
        files = {"file": (filename, f, "application/octet-stream")}
        data = {"language": language} if language else {}
        with httpx.Client(timeout=httpx.Timeout(config.VOICE_SERVER_TIMEOUT_SECONDS, connect=config.VOICE_SERVER_CONNECT_TIMEOUT_SECONDS)) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {config.VOICE_SERVER_API_KEY}"},
                files=files,
                data=data,
            )
            resp.raise_for_status()
            body = resp.json()

    return {
        "text": body["text"],
        "language": body.get("language"),
        "latency_ms": body.get("latency_ms"),
        "_mock": False,
        "_source": "stt.transcribe (voice server, live)",
    }


def transcribe(audio_file: str, language: str | None = None) -> dict:
    """Returns a dict: {"text", "language", "latency_ms"?, "_mock", "_source"}.
    Never raises -- always falls back to the canned mock transcript on any
    failure."""
    try:
        result = _try_real_transcribe(audio_file, language)
        logger.info("[stt] real transcription succeeded: %r (latency_ms=%s)", result["text"], result.get("latency_ms"))
        return result
    except Exception as e:
        logger.info("[MOCK][stt] real transcription unavailable (%s: %s) -- falling back to canned placeholder", type(e).__name__, e)
        return {
            "text": _CANNED_TRANSCRIPT,
            "language": language,
            "latency_ms": None,
            "_mock": True,
            "_source": "stt.transcribe (canned mock fallback)",
        }
