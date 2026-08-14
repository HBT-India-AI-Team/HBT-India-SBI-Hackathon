"""
Standalone connectivity check for the configured voice AI server
(backend.config.VOICE_SERVER_URL / VOICE_SERVER_API_KEY), mirroring
check_ollama_connectivity.py / check_telegram_connectivity.py's
structure/behavior.

Meant to be run by a human on a machine that actually has real network
access to the configured ngrok URL (the user's own voice server, see
/reference/voice_ai_server_client/). In this dev sandbox, that domain is
blocked by the outbound allowlist (same as the Ollama/Telegram domains --
403 blocked-by-allowlist), so this script is EXPECTED to fail at step 1
here -- that's normal, not a bug. It fails gracefully (clear (checkmark)/(cross)
per step, short reason, non-zero exit code) rather than hanging or raising
an unhandled traceback.

Steps:
  1. GET  {VOICE_SERVER_URL}/health              -> {"status": "ready"}
  2. POST {VOICE_SERVER_URL}/transcribe           using
     reference/voice_ai_server_client/synthesized.wav as the test fixture
  3. POST {VOICE_SERVER_URL}/synthesize           with a short test string
  4. WS   {VOICE_SERVER_URL}/call?token=...       brief handshake: connect,
     wait for {"type":"ready"}, close cleanly

Run with: python3 backend/scripts/check_voice_server_connectivity.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
import websockets

from backend import config

CONNECT_TIMEOUT_SECONDS = 8.0
TRANSCRIBE_TIMEOUT_SECONDS = 60.0
SYNTHESIZE_TIMEOUT_SECONDS = 60.0
WS_HANDSHAKE_TIMEOUT_SECONDS = 10.0

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEST_AUDIO_FIXTURE = os.path.join(REPO_ROOT, "reference", "voice_ai_server_client", "synthesized.wav")


def _fail(step: str, detail: str) -> None:
    print(f"[FAIL] {step}: {detail}")


def _ok(step: str, detail: str = "") -> None:
    suffix = f" -- {detail}" if detail else ""
    print(f"[OK] {step}{suffix}")


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {config.VOICE_SERVER_API_KEY}"}


def check_health() -> bool:
    url = f"{config.VOICE_SERVER_URL}/health"
    try:
        with httpx.Client(timeout=CONNECT_TIMEOUT_SECONDS) as client:
            resp = client.get(url, headers=_auth_header())
            if resp.status_code == 503:
                _fail("Step 1/4: GET /health", "503 -- server reachable but models not loaded yet")
                return False
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        _fail("Step 1/4: GET /health", f"timed out after {CONNECT_TIMEOUT_SECONDS}s -- endpoint unreachable from this machine")
        return False
    except httpx.RequestError as e:
        _fail("Step 1/4: GET /health", f"request failed ({type(e).__name__}: {e})")
        return False
    except Exception as e:
        _fail("Step 1/4: GET /health", f"unexpected error ({type(e).__name__}: {e})")
        return False

    if data.get("status") != "ready":
        _fail("Step 1/4: GET /health", f"reachable but status={data.get('status')!r} (expected 'ready')")
        return False
    _ok("Step 1/4: GET /health", "status=ready")
    return True


def check_transcribe() -> bool:
    if not os.path.exists(TEST_AUDIO_FIXTURE):
        _fail("Step 2/4: POST /transcribe", f"test fixture not found: {TEST_AUDIO_FIXTURE}")
        return False
    try:
        with open(TEST_AUDIO_FIXTURE, "rb") as f:
            files = {"file": (os.path.basename(TEST_AUDIO_FIXTURE), f, "application/octet-stream")}
            with httpx.Client(timeout=TRANSCRIBE_TIMEOUT_SECONDS) as client:
                resp = client.post(f"{config.VOICE_SERVER_URL}/transcribe", headers=_auth_header(), files=files)
        if resp.status_code == 401:
            _fail("Step 2/4: POST /transcribe", "401 unauthorized -- check VOICE_SERVER_API_KEY")
            return False
        if resp.status_code == 503:
            _fail("Step 2/4: POST /transcribe", "503 -- server not ready")
            return False
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        _fail("Step 2/4: POST /transcribe", f"timed out after {TRANSCRIBE_TIMEOUT_SECONDS}s -- endpoint unreachable from this machine")
        return False
    except httpx.RequestError as e:
        _fail("Step 2/4: POST /transcribe", f"request failed ({type(e).__name__}: {e})")
        return False
    except Exception as e:
        _fail("Step 2/4: POST /transcribe", f"unexpected error ({type(e).__name__}: {e})")
        return False

    _ok("Step 2/4: POST /transcribe", f"text={data.get('text')!r} latency_ms={data.get('latency_ms')}")
    return True


def check_synthesize() -> bool:
    try:
        with httpx.Client(timeout=SYNTHESIZE_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{config.VOICE_SERVER_URL}/synthesize",
                headers=_auth_header(),
                json={"text": "Hello, this is a connectivity check.", "language": "en"},
            )
        if resp.status_code == 401:
            _fail("Step 3/4: POST /synthesize", "401 unauthorized -- check VOICE_SERVER_API_KEY")
            return False
        if resp.status_code == 503:
            _fail("Step 3/4: POST /synthesize", "503 -- server not ready")
            return False
        resp.raise_for_status()
        audio_bytes = resp.content
    except httpx.TimeoutException:
        _fail("Step 3/4: POST /synthesize", f"timed out after {SYNTHESIZE_TIMEOUT_SECONDS}s -- endpoint unreachable from this machine")
        return False
    except httpx.RequestError as e:
        _fail("Step 3/4: POST /synthesize", f"request failed ({type(e).__name__}: {e})")
        return False
    except Exception as e:
        _fail("Step 3/4: POST /synthesize", f"unexpected error ({type(e).__name__}: {e})")
        return False

    latency_ms = resp.headers.get("X-Latency-Ms", "?")
    _ok("Step 3/4: POST /synthesize", f"{len(audio_bytes)} bytes returned, latency_ms={latency_ms}")
    return True


async def _check_ws_handshake_async() -> bool:
    ws_base = config.VOICE_SERVER_URL.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_base}/call?token={config.VOICE_SERVER_API_KEY}"
    try:
        async with websockets.connect(ws_url, open_timeout=WS_HANDSHAKE_TIMEOUT_SECONDS) as ws:
            message = await asyncio.wait_for(ws.recv(), timeout=WS_HANDSHAKE_TIMEOUT_SECONDS)
            payload = json.loads(message)
            if payload.get("type") != "ready":
                _fail("Step 4/4: WS /call handshake", f"connected but first frame was {payload!r}, expected type=ready")
                return False
            _ok(
                "Step 4/4: WS /call handshake",
                f"ready (input_sample_rate={payload.get('input_sample_rate')}, output_sample_rate={payload.get('output_sample_rate')})",
            )
            return True
    except asyncio.TimeoutError:
        _fail("Step 4/4: WS /call handshake", f"no message received within {WS_HANDSHAKE_TIMEOUT_SECONDS}s")
        return False
    except Exception as e:
        _fail("Step 4/4: WS /call handshake", f"{type(e).__name__}: {e}")
        return False


def check_ws_handshake() -> bool:
    try:
        return asyncio.run(_check_ws_handshake_async())
    except Exception as e:
        _fail("Step 4/4: WS /call handshake", f"unexpected error running WS check ({type(e).__name__}: {e})")
        return False


def main() -> int:
    print("=== Voice AI server connectivity check ===")
    print(f"VOICE_SERVER_URL          = {config.VOICE_SERVER_URL or '<empty>'}")
    print(f"VOICE_SERVER_API_KEY      = {'<set, redacted>' if config.VOICE_SERVER_API_KEY else '<empty>'}")
    print(f"VOICE_CLIENT_SAMPLE_RATE  = {config.VOICE_CLIENT_SAMPLE_RATE}")
    print(f"VOICE_CLIENT_FRAME_MS     = {config.VOICE_CLIENT_FRAME_MS}")
    print()

    if not config.VOICE_SERVER_URL or not config.VOICE_SERVER_API_KEY:
        _fail(
            "Pre-check: VOICE_SERVER_URL / VOICE_SERVER_API_KEY configured",
            "one or both are empty (backend/.env, real env vars, and the "
            "reference/voice_ai_server_client/.env fallback all came up empty). "
            "Set VOICE_SERVER_URL / VOICE_SERVER_API_KEY in backend/.env, or "
            "make sure reference/voice_ai_server_client/.env is populated.",
        )
        print("\nResult: FAILED (not configured). Nothing further to check without a URL/key; exiting cleanly.")
        return 1

    results = [
        check_health(),
        check_transcribe(),
        check_synthesize(),
        check_ws_handshake(),
    ]

    print()
    if all(results):
        print("Result: ALL CHECKS PASSED -- voice server is reachable and usable from this machine.")
        return 0

    print(
        "Result: FAILED ({}/4 steps passed). This is EXPECTED when run from the dev/build sandbox -- "
        "there is no network path to the configured ngrok domain from here (confirmed separately: "
        "403 blocked-by-allowlist). Re-run this script on a machine with real network access to "
        "VOICE_SERVER_URL (e.g. the machine running the voice server itself, or one on the same LAN/"
        "with the ngrok tunnel reachable).".format(sum(1 for r in results if r))
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
