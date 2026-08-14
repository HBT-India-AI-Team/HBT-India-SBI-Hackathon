"""
Standalone connectivity check for the configured Ollama endpoint
(backend.config.OLLAMA_BASE_URL / OLLAMA_MODEL).

Meant to be run by a human on a machine that actually has network access to
the configured endpoint (e.g. the ngrok-tunneled URL currently checked in to
backend/.env). In this dev sandbox, the domain is blocked, so this script is
EXPECTED to fail at step 1 -- that's normal, not a bug. It should fail
gracefully (clear ❌ message, short reason, non-zero exit code) rather than
hang or raise an unhandled traceback.

Steps:
  1. GET {OLLAMA_BASE_URL}/api/tags -- lists models actually pulled/served.
  2. Checks whether OLLAMA_MODEL (backend/.env) is among the listed models.
  3. Attempts one small, real /api/generate call against OLLAMA_MODEL as an
     end-to-end sanity check (short prompt, short timeout).

Run with: python3 backend/scripts/check_ollama_connectivity.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx

from backend import config

CONNECT_TIMEOUT_SECONDS = 5.0
GENERATE_TIMEOUT_SECONDS = 15.0


def _fail(step: str, detail: str) -> None:
    print(f"❌ {step}: {detail}")


def _ok(step: str, detail: str = "") -> None:
    suffix = f" -- {detail}" if detail else ""
    print(f"✅ {step}{suffix}")


def main() -> int:
    print("=== Ollama connectivity check ===")
    print(f"OLLAMA_BASE_URL = {config.OLLAMA_BASE_URL}")
    print(f"OLLAMA_MODEL    = {config.OLLAMA_MODEL!r} (empty = auto-discover)")
    print(f"OLLAMA_VISION_MODEL = {config.OLLAMA_VISION_MODEL!r} (empty = falls back to OLLAMA_MODEL)")
    print()

    # --- Step 1: list models via /api/tags ---
    models = []
    try:
        with httpx.Client(timeout=CONNECT_TIMEOUT_SECONDS) as client:
            resp = client.get(f"{config.OLLAMA_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
    except httpx.TimeoutException:
        _fail("Step 1/3: GET /api/tags", f"timed out after {CONNECT_TIMEOUT_SECONDS}s -- endpoint unreachable from this machine")
        print("\nResult: FAILED (network unreachable). This is expected in the dev sandbox -- "
              "there is no network path to the configured OLLAMA_BASE_URL from here. "
              "Re-run this script on a machine with real network access to that endpoint.")
        return 1
    except httpx.HTTPStatusError as e:
        _fail("Step 1/3: GET /api/tags", f"endpoint responded with HTTP {e.response.status_code}")
        return 1
    except httpx.RequestError as e:
        _fail("Step 1/3: GET /api/tags", f"request failed ({type(e).__name__}: {e})")
        print("\nResult: FAILED (network unreachable). This is expected in the dev sandbox -- "
              "there is no network path to the configured OLLAMA_BASE_URL from here. "
              "Re-run this script on a machine with real network access to that endpoint.")
        return 1
    except Exception as e:
        _fail("Step 1/3: GET /api/tags", f"unexpected error ({type(e).__name__}: {e})")
        return 1

    if not models:
        _fail("Step 1/3: GET /api/tags", "reachable, but no models are listed -- pull a model with `ollama pull <name>` first")
        return 1
    _ok("Step 1/3: GET /api/tags", f"reachable, {len(models)} model(s) found: {', '.join(models)}")

    # --- Step 2: is OLLAMA_MODEL among them? ---
    target_model = config.OLLAMA_MODEL
    if not target_model:
        _ok("Step 2/3: OLLAMA_MODEL check", "OLLAMA_MODEL is empty -- auto-discovery is configured, using first listed model for the test call")
        target_model = models[0]
    elif target_model in models:
        _ok("Step 2/3: OLLAMA_MODEL check", f"{target_model!r} is present on the server")
    else:
        _fail(
            "Step 2/3: OLLAMA_MODEL check",
            f"{target_model!r} (from backend/.env) is NOT among the models served "
            f"({', '.join(models)}). Check for a typo (e.g. 'gemma4' vs 'gemma3') or pull it with `ollama pull {target_model}`.",
        )
        return 1

    # --- Step 3: one small real /api/generate call ---
    try:
        with httpx.Client(timeout=GENERATE_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json={"model": target_model, "prompt": "Reply with exactly one word: OK", "stream": False},
            )
            resp.raise_for_status()
            body = resp.json()
            text = (body.get("response") or "").strip()
    except httpx.TimeoutException:
        _fail("Step 3/3: /api/generate test call", f"timed out after {GENERATE_TIMEOUT_SECONDS}s")
        return 1
    except httpx.RequestError as e:
        _fail("Step 3/3: /api/generate test call", f"request failed ({type(e).__name__}: {e})")
        return 1
    except Exception as e:
        _fail("Step 3/3: /api/generate test call", f"unexpected error ({type(e).__name__}: {e})")
        return 1

    _ok("Step 3/3: /api/generate test call", f"model responded: {text!r}")
    print("\nResult: ALL CHECKS PASSED -- Ollama is reachable and usable from this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
