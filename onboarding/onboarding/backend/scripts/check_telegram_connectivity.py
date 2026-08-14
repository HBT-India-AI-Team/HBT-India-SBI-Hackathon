"""
Standalone connectivity check for the configured Telegram bot
(backend.config.TELEGRAM_BOT_TOKEN), mirroring
check_ollama_connectivity.py's structure/behavior.

Meant to be run by a human on a machine that actually has network access to
api.telegram.org. In this dev sandbox, that domain is ALSO blocked by the
outbound allowlist (same as the ngrok Ollama domain, confirmed separately --
403 blocked-by-allowlist), so this script is EXPECTED to fail at step 1
there -- that's normal, not a bug. More importantly, in this sandbox no
TELEGRAM_BOT_TOKEN is configured at all, so it should fail gracefully with a
clear, early, non-crashing exit before ever attempting a network call.

Steps:
  1. Confirm TELEGRAM_BOT_TOKEN is configured at all -- clear early exit if
     not, no network call attempted.
  2. GET https://api.telegram.org/bot{TOKEN}/getMe -- confirms the token is
     valid and prints the bot's username.
  3. If a --chat-id argument is passed, sends a real test OTP-shaped
     message to that chat via services/otp/telegram_sender.py::send() (the
     same function the live OTP dispatch path uses) as an end-to-end sanity
     check.

Run with:
  python3 backend/scripts/check_telegram_connectivity.py
  python3 backend/scripts/check_telegram_connectivity.py --chat-id 123456789
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx

from backend import config

CONNECT_TIMEOUT_SECONDS = 5.0

TELEGRAM_API_BASE = "https://api.telegram.org"


def _fail(step: str, detail: str) -> None:
    print(f"❌ {step}: {detail}")


def _ok(step: str, detail: str = "") -> None:
    suffix = f" -- {detail}" if detail else ""
    print(f"✅ {step}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Telegram bot connectivity / send a test OTP message.")
    parser.add_argument("--chat-id", default=None, help="If provided, sends a real test OTP-shaped message to this chat_id.")
    args = parser.parse_args()

    print("=== Telegram connectivity check ===")
    print(f"TELEGRAM_BOT_TOKEN    = {'<set, redacted>' if config.TELEGRAM_BOT_TOKEN else '<empty>'}")
    print(f"TELEGRAM_BOT_USERNAME = {config.TELEGRAM_BOT_USERNAME!r} (used for deep-link construction only)")
    print()

    # --- Step 1: is a token configured at all? ---
    if not config.TELEGRAM_BOT_TOKEN:
        _fail(
            "Step 1/3: TELEGRAM_BOT_TOKEN configured",
            "no TELEGRAM_BOT_TOKEN is set (backend/.env or environment). Get one from @BotFather on Telegram "
            "(send /newbot), then set TELEGRAM_BOT_TOKEN=<token> in backend/.env.",
        )
        print(
            "\nResult: FAILED (not configured). This is expected in this dev sandbox -- no bot has been "
            "registered/credentialed here. Nothing further to check without a token; exiting cleanly."
        )
        return 1
    _ok("Step 1/3: TELEGRAM_BOT_TOKEN configured")

    # --- Step 2: GET /getMe ---
    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
    try:
        with httpx.Client(timeout=CONNECT_TIMEOUT_SECONDS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        _fail("Step 2/3: GET /getMe", f"timed out after {CONNECT_TIMEOUT_SECONDS}s -- api.telegram.org unreachable from this machine")
        print(
            "\nResult: FAILED (network unreachable). If you're running this from a sandboxed/allowlisted "
            "environment, api.telegram.org may be blocked there -- re-run on a machine with real network access."
        )
        return 1
    except httpx.HTTPStatusError as e:
        detail = "invalid token (401 Unauthorized)" if e.response.status_code == 401 else f"HTTP {e.response.status_code}"
        _fail("Step 2/3: GET /getMe", detail)
        return 1
    except httpx.RequestError as e:
        _fail("Step 2/3: GET /getMe", f"request failed ({type(e).__name__}: {e})")
        print(
            "\nResult: FAILED (network unreachable). If you're running this from a sandboxed/allowlisted "
            "environment, api.telegram.org may be blocked there -- re-run on a machine with real network access."
        )
        return 1
    except Exception as e:
        _fail("Step 2/3: GET /getMe", f"unexpected error ({type(e).__name__}: {e})")
        return 1

    if not data.get("ok"):
        _fail("Step 2/3: GET /getMe", f"Telegram responded but ok=false: {data}")
        return 1

    bot_info = data.get("result", {})
    bot_username = bot_info.get("username", "<unknown>")
    _ok("Step 2/3: GET /getMe", f"token is valid, bot username = @{bot_username}")

    # --- Step 3 (optional): send a real test message ---
    if not args.chat_id:
        print()
        print("Step 3/3: skipped (no --chat-id passed). Pass --chat-id <telegram_chat_id> to send a real test message.")
        print("\nResult: TOKEN VALID -- bot is reachable and credentialed. No test message sent.")
        return 0

    from backend.services.otp import telegram_sender

    result = telegram_sender.send(args.chat_id, "000000 (connectivity-check test message)")
    if result.get("real_send"):
        _ok("Step 3/3: test message send", f"sent to chat_id={args.chat_id}")
        print("\nResult: ALL CHECKS PASSED -- Telegram bot is reachable, credentialed, and can send messages.")
        return 0
    else:
        _fail("Step 3/3: test message send", f"did not send for real: {result.get('mock_reason')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
