"""Sarvam AI — Indic language identification and translation.

Not a capability and deliberately not registered as a tool. The model never
chooses to call this; the pipeline calls it. Two jobs, both of which exist
because of a specific failure we have already seen in production:

**Language identification.** A Tamil question came back answered in Telugu.
The ASR transcript was garbled, our own detection is script-based (see
`style_examples.language_of`), and Devanagari-vs-Tamil is all a Unicode range
can tell you — it cannot separate Hindi from Marathi, or Tamil from Malayalam
transliterated into Latin. Sarvam is trained on exactly these languages, so
it can. The reply language stops being something the model infers from
mangled text and becomes something it is told.

**Translation.** `docs.search` refuses Indic queries and asks the model to
translate and retry (DECISIONS #4), because a raw Hindi query scores ~0.54
against the passage its English translation matches at 0.72. That refusal
works but leans on the model actually retrying. A real translator makes it
deterministic. **Not wired in yet** — see the note on `translate` below.

Design constraints copied from fx_rates.py, for the same reasons:

- **Allowlisted host.** Only _API_BASE is contacted, ever.
- **Fails soft, not closed.** Unlike a rate lookup, a missing answer here
  costs nothing: no key, a timeout, or an unparseable body all return None
  and the pipeline proceeds exactly as it does today. Nothing about grounding
  may depend on this being reachable.
- **Short timeout.** This runs before the answer, so a slow third party would
  be felt on every turn. It gets 4 seconds and then it is skipped.
- **Cached.** The same transcript is identified once per process.

## API details to confirm against their docs

Everything in the CONFIG block below is written from Sarvam's public API as
understood at the time of writing and has **not been verified against a live
key**. When the credentials arrive, check these four things first — they are
the whole surface, and a mismatch in any one produces a silent None:

  1. the header name carrying the key
  2. the two endpoint paths
  3. the request field names
  4. the response field names

`_probe()` at the bottom exists to check all four in one call. Run it before
assuming anything here works.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- CONFIG ---
# Unverified. See the module docstring. Every value here is overridable by
# environment variable so this can be corrected without a code change.
_API_BASE = os.environ.get("SARVAM_API_BASE", "https://api.sarvam.ai").rstrip("/")
_KEY_HEADER = os.environ.get("SARVAM_KEY_HEADER", "api-subscription-key")
_PATH_DETECT = os.environ.get("SARVAM_PATH_DETECT", "/text-lid")
_PATH_TRANSLATE = os.environ.get("SARVAM_PATH_TRANSLATE", "/translate")
_TRANSLATE_MODEL = os.environ.get("SARVAM_TRANSLATE_MODEL", "mayura:v1")
# ---------------------------------------------------------------------------

_TIMEOUT_SECONDS = 4
_MAX_CHARS = 500          # transcripts are short; this only guards a pathological input

# BCP-47-ish codes Sarvam uses. Mapped to the bare codes the rest of this
# codebase speaks (style_examples keys its index by "hi", "ta").
_LANGUAGE_NAMES = {
    "en-IN": ("en", "English"),
    "hi-IN": ("hi", "Hindi"),
    "ta-IN": ("ta", "Tamil"),
    "te-IN": ("te", "Telugu"),
    "kn-IN": ("kn", "Kannada"),
    "ml-IN": ("ml", "Malayalam"),
    "mr-IN": ("mr", "Marathi"),
    "bn-IN": ("bn", "Bengali"),
    "gu-IN": ("gu", "Gujarati"),
    "pa-IN": ("pa", "Punjabi"),
    "od-IN": ("od", "Odia"),
    "or-IN": ("od", "Odia"),
}

_cache: dict[str, dict[str, Any] | None] = {}
_cache_lock = threading.Lock()


def language_name(code: str) -> str:
    """"ta-IN" -> "Tamil". Unknown codes come back unchanged.

    Used on the caller-declared language too, not just detected ones: a
    prompt that says "the user is writing in ta-IN" asks the model to know
    a BCP-47 table, and "Tamil" does not.
    """
    if not isinstance(code, str) or not code.strip():
        return ""
    cleaned = code.strip()
    if cleaned in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[cleaned][1]
    bare = cleaned.split("-")[0].lower()
    for sarvam_code, (short, name) in _LANGUAGE_NAMES.items():
        if short == bare:
            return name
    return cleaned


def available() -> bool:
    """Whether a key is configured. Everything here no-ops without one."""
    return bool(os.environ.get("SARVAM_API_KEY", "").strip())


def _post(path: str, payload: dict) -> dict | None:
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        return None
    try:
        response = requests.post(
            f"{_API_BASE}{path}",
            json=payload,
            headers={_KEY_HEADER: key, "Content-Type": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Sarvam %s unreachable, continuing without it: %s", path, exc)
        return None

    if response.status_code != 200:
        # Logged with the body because the likeliest cause is one of the four
        # unverified names in CONFIG, and the body is what says which.
        logger.warning("Sarvam %s returned %s: %s", path, response.status_code,
                       response.text[:300])
        return None
    try:
        body = response.json()
    except json.JSONDecodeError:
        logger.warning("Sarvam %s returned non-JSON: %s", path, response.text[:200])
        return None
    return body if isinstance(body, dict) else None


def identify_language(text: str) -> dict[str, str] | None:
    """What language and script this text is in, or None.

    Returns e.g. {"code": "ta", "sarvam_code": "ta-IN", "name": "Tamil",
    "script": "Taml"}. None means "could not tell", and every caller must
    treat that as "carry on as before" rather than as an error.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    clipped = text.strip()[:_MAX_CHARS]

    with _cache_lock:
        if clipped in _cache:
            return _cache[clipped]

    body = _post(_PATH_DETECT, {"input": clipped})
    result: dict[str, str] | None = None
    if body:
        sarvam_code = body.get("language_code") or body.get("lang_code")
        if isinstance(sarvam_code, str) and sarvam_code:
            code, name = _LANGUAGE_NAMES.get(sarvam_code, (sarvam_code.split("-")[0], sarvam_code))
            result = {"code": code, "sarvam_code": sarvam_code, "name": name}
            script = body.get("script_code")
            if isinstance(script, str) and script:
                result["script"] = script
        else:
            logger.warning("Sarvam %s gave no language_code; body keys were %s",
                           _PATH_DETECT, sorted(body))

    with _cache_lock:
        _cache[clipped] = result
    return result


def translate(text: str, *, source: str = "auto", target: str = "en-IN") -> str | None:
    """Translate, or None.

    **Written but not wired in.** The intended use is giving `docs.search` a
    real English query instead of refusing Indic ones and relying on the model
    to translate and retry. That is a change to a deliberate decision
    (DECISIONS #4), so it wants measuring against the current
    translate-then-retry path before being switched on — a translator that is
    subtly wrong on financial vocabulary would degrade retrieval silently,
    which is the failure mode that decision exists to avoid.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    body = _post(_PATH_TRANSLATE, {
        "input": text.strip()[:_MAX_CHARS],
        "source_language_code": source,
        "target_language_code": target,
        "model": _TRANSLATE_MODEL,
    })
    if not body:
        return None
    translated = body.get("translated_text") or body.get("output")
    if isinstance(translated, str) and translated.strip():
        return translated.strip()
    logger.warning("Sarvam %s gave no translated_text; body keys were %s",
                   _PATH_TRANSLATE, sorted(body))
    return None


def _probe() -> int:
    """Check all four unverified names in one go, and say which one is wrong.

        python -m capabilities_impl.sarvam
    """
    import io
    import sys

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="  ! %(message)s")

    print(f"base    {_API_BASE}")
    print(f"header  {_KEY_HEADER}")
    print(f"key     {'set' if available() else 'MISSING — set SARVAM_API_KEY'}")
    if not available():
        return 1

    sample = "எனக்கு சேமிப்பு கணக்கு பத்தி தெரிஞ்சுக்கணும்"
    print(f"\ndetect  {_PATH_DETECT}")
    detected = identify_language(sample)
    print(f"        -> {detected}   {'OK' if detected else 'FAILED'}")

    print(f"\ntranslate {_PATH_TRANSLATE}")
    english = translate(sample, source="ta-IN", target="en-IN")
    print(f"        -> {english!r}   {'OK' if english else 'FAILED'}")

    ok = bool(detected and english)
    print("\nAll four names look right." if ok else
          "\nSomething above is wrong. The warnings say which — usually a path "
          "or a response field name. Override it with the SARVAM_* env vars "
          "rather than editing this file.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_probe())
