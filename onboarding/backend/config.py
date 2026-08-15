"""
Central config for YONO 3.0 backend. Values are read from environment
variables with sane demo-friendly defaults. See /docs/ARCHITECTURE.md and
/docs/MOCKS.md for what each of these controls.

A `.env` file (in this `backend/` directory, or the repo root) is loaded
via python-dotenv below, so local/deploy-specific overrides (e.g. real
Ollama endpoint + model) don't need to be exported into the shell manually.
Real environment variables (if already set) always take precedence over
`.env` values (python-dotenv's default `override=False` behavior).
"""
import os

from dotenv import load_dotenv

_BASE_DIR_FOR_ENV = os.path.dirname(os.path.abspath(__file__))
# backend/.env first, then repo-root .env as a fallback -- either works.
load_dotenv(os.path.join(_BASE_DIR_FOR_ENV, ".env"))
load_dotenv(os.path.join(os.path.dirname(_BASE_DIR_FOR_ENV), ".env"))


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# --- Background scheduler ---
SCHEDULER_POLL_INTERVAL_SECONDS = float(os.environ.get("SCHEDULER_POLL_INTERVAL_SECONDS", "3"))
DOCUMENT_REVIEW_DELAY_SECONDS = float(os.environ.get("DOCUMENT_REVIEW_DELAY_SECONDS", "12"))
IDLE_NUDGE_CHECK_INTERVAL_SECONDS = float(os.environ.get("IDLE_NUDGE_CHECK_INTERVAL_SECONDS", "60"))
IDLE_THRESHOLD_SECONDS = float(os.environ.get("IDLE_THRESHOLD_SECONDS", "300"))
NUDGE_COOLDOWN_SECONDS = float(os.environ.get("NUDGE_COOLDOWN_SECONDS", "300"))

# --- OTP ---
OTP_EXPIRY_SECONDS = int(os.environ.get("OTP_EXPIRY_SECONDS", "300"))
OTP_DELIVERY_CHANNEL = os.environ.get("OTP_DELIVERY_CHANNEL", "auto")  # auto/telegram/email/sms

# --- Telegram (Phase 6) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "yono3_bot")

# --- SMTP (Phase 6) ---
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS", "")

# --- WhatsApp deep link (used for handoff link shape only, Phase 10) ---
WHATSAPP_BOT_NUMBER = os.environ.get("WHATSAPP_BOT_NUMBER", "911234567890")

# --- Handoff tokens ---
HANDOFF_TOKEN_TTL_SECONDS = int(os.environ.get("HANDOFF_TOKEN_TTL_SECONDS", "900"))

# --- Ollama / LLM (Phase 8) ---
# OLLAMA_BASE_URL should NOT have a trailing slash -- all call sites build
# URLs as f"{OLLAMA_BASE_URL}/api/..." (e.g. .../ollama/api/generate).
# Default here is a bare localhost Ollama; the checked-in .env currently
# overrides this to a real external ngrok-tunneled instance whose base URL
# itself already includes a `/ollama` path segment -- that's expected and
# handled correctly by the plain f-string join, no urljoin() involved that
# could drop or double the path segment.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
# NOTE on OLLAMA_MODEL / OLLAMA_VISION_MODEL: as of early 2026 "gemma4" is
# not a publicly known Ollama model tag (the latest known Gemma line on
# Ollama is gemma3, e.g. "gemma3:12b"). If the configured value below is
# "gemma4:12B" and that turns out to be a typo, correct it to "gemma3:12b"
# in your .env. If it's a real custom/local tag you've pulled, this is
# fine as-is -- we deliberately do NOT silently rewrite whatever value is
# configured. Verify with `ollama list` on the machine actually running
# Ollama, or via backend/scripts/check_ollama_connectivity.py.
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")  # auto-discovered if empty
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "")  # auto-discovered if empty
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "6"))
ONBOARDING_ENGINE_MODE = os.environ.get("ONBOARDING_ENGINE_MODE", "llm")  # llm | rule_based
# FinGuru answers are longer free-text than the onboarding action JSON, so they
# get a more generous timeout + output-token budget (the short 6s onboarding
# timeout truncates FinGuru's JSON mid-string).
FINGURU_LLM_TIMEOUT_SECONDS = float(os.environ.get("FINGURU_LLM_TIMEOUT_SECONDS", "45"))
FINGURU_LLM_NUM_PREDICT = int(os.environ.get("FINGURU_LLM_NUM_PREDICT", "900"))

# --- STT (Phase 7, mocked in this build) ---
STT_ENGINE = os.environ.get("STT_ENGINE", "mock")

# --- Voice AI server (STT + TTS + live call, Phase 7 real-integration
# attempt) ---
# This points at a separately-running voice server (own STT/TTS models,
# documented in /reference/voice_ai_server_client/) that the user runs on
# their own machine and exposes via ngrok. The canonical values live in
# that reference client's own `.env` (`reference/voice_ai_server_client/.env`
# -- `YONO_SERVER_URL` / `YONO_SERVER_API_KEY` / `YONO_CLIENT_SAMPLE_RATE` /
# `YONO_CLIENT_FRAME_MS`); we keep a copy in backend/.env (below) so this
# service doesn't have an import-time dependency on a sibling project's
# files, but to avoid the two copies drifting, if VOICE_SERVER_URL /
# VOICE_SERVER_API_KEY are NOT set (neither real env var nor backend/.env),
# we fall back to reading them straight out of that reference .env file --
# see _load_reference_voice_client_env() below. Whenever the user updates
# the voice server's ngrok URL/key, updating
# reference/voice_ai_server_client/.env is enough; backend/.env only needs
# updating if you want it to be the source of truth instead (e.g. once this
# backend is deployed somewhere the reference/ folder won't exist).
#
# VOICE_SERVER_URL already includes the `/voice` path prefix the real
# server is mounted under (confirmed from the reference client's .env) --
# all call sites build URLs as f"{VOICE_SERVER_URL}/transcribe" etc, plain
# f-string join, same pattern as OLLAMA_BASE_URL above.


def _load_reference_voice_client_env():
    """Best-effort fallback: parse
    reference/voice_ai_server_client/.env directly (same tiny hand-rolled
    parser style as that project's own client_config.py) so VOICE_SERVER_*
    doesn't need to be duplicated into backend/.env by hand. Returns {} if
    the file doesn't exist or can't be read -- never raises."""
    values = {}
    repo_root = os.path.dirname(_BASE_DIR_FOR_ENV)
    ref_env_path = os.path.join(repo_root, "reference", "voice_ai_server_client", ".env")
    try:
        if os.path.exists(ref_env_path):
            with open(ref_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except Exception:
        pass
    return values


_REF_VOICE_ENV = _load_reference_voice_client_env()

VOICE_SERVER_URL = (
    os.environ.get("VOICE_SERVER_URL") or _REF_VOICE_ENV.get("YONO_SERVER_URL", "")
).rstrip("/")
VOICE_SERVER_API_KEY = os.environ.get("VOICE_SERVER_API_KEY") or _REF_VOICE_ENV.get("YONO_SERVER_API_KEY", "")
VOICE_CLIENT_SAMPLE_RATE = int(
    os.environ.get("VOICE_CLIENT_SAMPLE_RATE") or _REF_VOICE_ENV.get("YONO_CLIENT_SAMPLE_RATE", "16000")
)
VOICE_CLIENT_FRAME_MS = int(
    os.environ.get("VOICE_CLIENT_FRAME_MS") or _REF_VOICE_ENV.get("YONO_CLIENT_FRAME_MS", "30")
)
VOICE_SERVER_TIMEOUT_SECONDS = float(os.environ.get("VOICE_SERVER_TIMEOUT_SECONDS", "45"))
VOICE_SERVER_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("VOICE_SERVER_CONNECT_TIMEOUT_SECONDS", "10"))

# --- Logging ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
LOG_DIR = os.path.join(BASE_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
