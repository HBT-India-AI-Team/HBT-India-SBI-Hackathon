# YONO 3.0 Backend -- Mocks, Debug Hooks & Real Integrations Inventory

This is the single source of truth for what is genuinely real vs mocked in
this build, and for the deterministic test values used by demo scripts and
the test-harness. See `/backend/YONO_3.0_Backend_Redesign_BuildPrompts.md`
Phase 5 and Phase 6 for the spec these implement.

## Permanently mocked (require licensed-entity status not obtainable for a
## hackathon prototype)

- **Aadhaar / PAN / GSTIN / DigiLocker verification** -- these require
  UIDAI/NSDL/GSTN/DigiLocker API partner credentials that need a licensed
  entity. Format validation is real (regex, see
  `backend/services/validators.py`); the actual government-database
  verification call is not implemented, and never will be for this
  prototype. In this build, "verification" of PAN/GSTIN requirement types
  is entirely format-validation + the debug hooks below -- there is no
  separate async step for them (unlike documents/mobile OTP).
- **SMS OTP** (`backend/services/otp/sms_sender.py`) -- real SMS OTP
  delivery in India requires DLT template registration (TRAI regulation),
  not feasible to obtain for a prototype. Always logs what would be sent
  and returns `{"real_send": False, ...}`.
- **VLM document field extraction** (`backend/services/doc_parser.py`) --
  `extract_fields()` now genuinely attempts a real VLM call first
  (`_try_vlm_extract()`, same pattern as `classify_document()`'s
  `_try_vlm_classify()`: base64-encodes the image, POSTs to
  `{OLLAMA_BASE_URL}/api/generate` with a doc-type-appropriate JSON
  extraction schema, `format: "json"`, `stream: false`). No Ollama server
  (with a vision model like llava) is reachable from this sandbox, so this
  call fails (confirmed: `httpx` can't complete the SOCKS-proxied request
  here) and it falls back, on any exception, to a canned dict clearly
  tagged `_mock: True`. Both the real-attempt and the fallback are logged
  at INFO (never WARNING/ERROR -- the fallback is the expected, normal
  path in this sandbox). This is informational/assistive only in the real
  design too -- it never determines verified/rejected status. On a machine
  with real Ollama connectivity (see
  `backend/scripts/check_ollama_connectivity.py`), `_try_vlm_extract()` is
  the live code path with no further code changes needed.
- **Telephony (voice call initiate/end)** -- `POST /sessions/{id}/call/*`
  mirror a mock lifecycle only (no real telephony infra). Note: the new
  live-call WS proxy (`WS /sessions/{id}/call/live`, see below) reuses
  `end_call_for_session()` -- the same function `POST
  /sessions/{id}/call/end` calls -- to finalize the call, so this mock
  lifecycle stays the single source of truth for "call ended" state even
  once real voice relaying is wired in.
- **Ollama conversation LLM** -- no Ollama server is running in this
  sandbox, so every turn in this environment specifically falls back to
  the rule-based engine (see Phase 8 fallback behavior below). The
  LLM-first code path (`backend/services/onboarding_llm.py`) is real and
  will activate the moment `OLLAMA_BASE_URL` points at a reachable Ollama
  instance with a model pulled.

## Real integrations (run for real when credentials/services are present,
## clean mock fallback when they're not)

- **Telegram OTP** (`backend/services/otp/telegram_sender.py`) -- real
  Telegram Bot API `sendMessage` call (`POST
  https://api.telegram.org/bot{TOKEN}/sendMessage` with
  `{chat_id, text}` JSON body -- verified correct against the Bot API
  shape). Needs `TELEGRAM_BOT_TOKEN` (from @BotFather) and the target
  `User.telegram_chat_id` on file. Falls back to a mock log line if the
  token or chat_id is missing.
  - **Webhook receiver now exists**: `POST /webhooks/telegram`
    (`backend/routers/webhooks.py`, registered in `backend/main.py`) is a
    real, working handler for Telegram's standard Update payload shape.
    It extracts `message.chat.id`, and if `message.text` is a
    `/start <handoff_token>` deep link (or a bare token) it consumes the
    token via `handoff_tokens.consume_handoff_token()`, sets
    `User.telegram_chat_id` on the resolved Application's user, and opens
    a new `Session(channel="telegram")` -- verified end to end in this
    sandbox: generated a real handoff token via `POST
    /applications/{id}/handoff/telegram`, POSTed a simulated Telegram
    update to `/webhooks/telegram` referencing it, confirmed
    `User.telegram_chat_id` was set and `dispatch._pick_channel()`
    subsequently returns `"telegram"` for that application. Messages with
    no token and no existing linked user are logged and answered with
    `{"ok": true, "linked": false, ...}` rather than erroring; malformed
    JSON bodies return `{"ok": false, "reason": "invalid_json"}` rather
    than crashing.
  - **Still needed to actually receive live Telegram traffic**: (1) a real
    `TELEGRAM_BOT_TOKEN` from @BotFather (none is configured in this
    sandbox -- confirmed via `backend/scripts/check_telegram_connectivity.py`,
    which exits cleanly with a clear "not configured" message rather than
    attempting a network call), and (2) this webhook route deployed behind
    a public HTTPS URL that has been registered with Telegram via the Bot
    API's `setWebhook` call
    (`https://api.telegram.org/bot{TOKEN}/setWebhook?url=<public-https-url>/webhooks/telegram`).
    Neither of these exists in this sandbox (no public URL, no token, and
    `api.telegram.org` is itself blocked by this sandbox's outbound
    allowlist) -- the receiver code is real and tested via direct POSTs,
    but nothing is currently registered with Telegram to route real
    traffic to it.
- **Email OTP** (`backend/services/otp/email_sender.py`) -- real `smtplib`
  send. Needs `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
  `SMTP_FROM_ADDRESS`. Falls back to a mock log line if `SMTP_HOST` or the
  recipient address is missing.
- **Background scheduler** (`backend/services/scheduler.py`) -- a real
  in-process asyncio poller against the `ScheduledJob` table. Not mocked
  at all; only the *outcome* of `document_review` jobs is
  debug-hook-controlled (see below), not the scheduling/dispatch itself.
- **Voice: STT / TTS / live call** (`backend/services/stt.py`,
  `backend/services/tts.py`, `backend/routers/calls.py`) -- real
  integration against a separately-running voice AI server the user runs
  on their own machine (own Whisper-shaped STT + TTS models, documented in
  `/reference/voice_ai_server_client/`), reachable via
  `VOICE_SERVER_URL`/`VOICE_SERVER_API_KEY` (`backend/config.py`, falls
  back to reading `reference/voice_ai_server_client/.env` directly if
  unset in `backend/.env`).
  - `stt.transcribe()` -- real `POST {VOICE_SERVER_URL}/transcribe`
    (multipart file upload, Bearer auth, ~45s timeout). Confirmed in this
    sandbox: the real attempt is genuinely made (logged at INFO,
    `[stt] attempting real transcription via .../voice/transcribe`) and
    genuinely fails (no network route to the configured ngrok domain --
    same allowlist block as Ollama/Telegram), falling back at INFO (not
    WARNING/ERROR -- expected here) to a canned placeholder transcript,
    returned as `{"text", "language", "latency_ms", "_mock": True,
    "_source": ...}` rather than a bare string, so callers can
    distinguish real vs mock. `POST /sessions/{id}/voice` feeds
    `result["text"]` through the normal message-handling path unchanged.
  - `tts.synthesize()` -- real `POST {VOICE_SERVER_URL}/synthesize`
    (JSON body, Bearer auth). Same real-attempt/INFO-fallback pattern;
    returns raw WAV bytes on success, `None` on any failure (never
    raises, never blocks the caller). Wired into `POST
    /sessions/{id}/voice`'s response as `reply_audio_base64` (base64 WAV
    or `null`) + `reply_audio_mock` (bool), additive to the existing
    `transcript`/`reply_text`/`actions_applied`/`progress` fields.
  - `WS /sessions/{id}/call/live` (`backend/routers/calls.py`) -- proxies
    a browser WS connection to the voice server's own `WS /call`
    endpoint so `VOICE_SERVER_API_KEY` stays server-side. Confirmed in
    this sandbox: a fake-browser test client connecting gets a clean,
    immediate close with code `4503` / reason `"voice server
    unreachable"` (upstream `websockets.connect()` genuinely attempted
    and genuinely fails against the blocked ngrok domain) -- no hang, no
    crash, no unhandled exception. A nonexistent `session_id` closes with
    `4404` / `"session_not_found"` before any upstream attempt.
  - Connectivity check: `backend/scripts/check_voice_server_connectivity.py`
    (mirrors `check_ollama_connectivity.py`/`check_telegram_connectivity.py`)
    -- checks `/health`, `/transcribe` (using
    `reference/voice_ai_server_client/synthesized.wav`), `/synthesize`,
    and a brief WS `/call` handshake, each with a clear pass/fail line,
    non-zero exit on any failure. Confirmed in this sandbox: fails
    gracefully at every step (network/proxy errors caught and reported,
    no hang/crash), exit code 1 -- meant to be re-run by the user on a
    machine with real network access to `VOICE_SERVER_URL`.

To exercise Telegram/Email OTP for real in this sandbox: set
`TELEGRAM_BOT_TOKEN` + ensure a `User.telegram_chat_id` is populated, or
set the `SMTP_*` env vars + ensure `User.email` is populated, then trigger
an OTP send via the normal mobile_otp submission flow.

## Debug hooks (Phase 5) -- known test values for deterministic demo/test

These override normal format-checking so demo scripts and the test-harness
can reliably hit reject/escalation paths, not just the happy path. Defined
in `backend/services/validators.py`.

| Field   | Value              | Effect                                      |
|---------|--------------------|-----------------------------------------------|
| mobile  | `0000000000`       | Always fails as "already registered"        |
| PAN     | `FAILFAILFF`       | Always fails format check                   |
| PAN     | `ABCDE1234F`       | Always passes format check (documented pass)|
| GSTIN   | `FAILGSTIN00`      | Always fails format check                   |
| GSTIN   | `22AAAAA0000A1Z5`  | Always passes format check (documented pass)|
| OTP     | any correct 6-digit code matching the stored hash | passes; any mismatched 6-digit code fails as `wrong_code` |

Document upload debug hook (`POST /applications/{id}/documents`):
- optional form field `debug_outcome`: `"verify"` | `"reject"` | omitted
- if provided, the scheduled `document_review` job (Phase 4, see
  `backend/services/scheduler.py::_handle_document_review`) uses it
  directly instead of any default
- if omitted, defaults to `"verify"` (optimistic default -- the normal
  happy-path demo doesn't need to pass anything extra)

Two `PAN`/document rejections in a row (or hitting `escalation_threshold`,
default 2) auto-creates a `ReviewItem(type=kyc_review)` -- this is how the
escalation demo (Phase 11) is triggered deterministically.

### Document-type sanity check (`doc_parser.classify_document()`)

`POST /applications/{id}/documents` also runs a synchronous document-type
sanity check (`backend/services/doc_parser.py::classify_document()`)
immediately after the file is saved -- unlike `extract_fields()` (informational
only, never gates outcome), `classify_document()` DOES gate the upload:

- It first attempts a real VLM classification call to the configured Ollama
  endpoint (`OLLAMA_BASE_URL`/`OLLAMA_VISION_MODEL`). This is a genuine
  network call and is expected to fail in this dev sandbox (no route to the
  configured ngrok endpoint) -- on any exception it falls back to a
  heuristic check (file readability / valid-image check via Pillow), which
  defaults `matches_expected=True` at low confidence unless the file is
  unreadable or 0 bytes. This is the path actually exercised by the demo
  scripts and normally results in a pass, so `demo_happy_path.sh` /
  `demo_msme_happy_path.sh` keep working unmodified.
- To deterministically force a mismatch for demo/test purposes (same
  Phase-5-debug-hook style as the table above), either:
  - pass form field `debug_outcome=reject` on the upload request (reused
    from the existing document-review debug hook), or
  - name the uploaded file containing the marker string `wrong_doc_type`
    (see `validators.DOC_MISMATCH_FILENAME_MARKER`) -- lets a mismatch be
    forced purely via filename without touching the `debug_outcome` field
    (which also controls the deferred `document_review` job's outcome).
- When `matches_expected` is `False`, the upload is rejected immediately
  (synchronously, not deferred to the scheduled `document_review` job) via
  the same `scheduler.apply_document_rejection()` path the scheduled job's
  `debug_outcome=="reject"` branch uses -- so both rejection paths share
  identical state-transition, failure-count, and escalation logic. The
  `document_review` job is never scheduled in this case. The response body
  reflects the rejection directly: `{"ok": false, "rejected": true,
  "requirement_state": "REJECTED", "escalated": bool, "classification": {...}}`.
  The classification result (`detected_type`, `confidence`, `reason`) is
  always stored on `DocumentSubmission.classification` regardless of
  outcome, and surfaced in `GET /applications/{id}`'s `documents[]` list
  alongside `rejection_reason`.

## DPDP data-rights request logging (Phase 12)

`POST /users/{id}/data-rights/access` and
`POST /users/{id}/data-rights/deletion` are REAL request-logging endpoints
-- they create a genuine, auditable `DataRightsRequest` row. Fulfillment
(`POST /admin/data-rights/{id}/fulfill`) is intentionally a MANUAL step for
this prototype -- automated deletion across a real system needs more care
than a hackathon timeline allows. Be precise about this distinction if
asked about DPDP compliance: the request/audit trail is real, the
fulfillment action is a manual admin action, not automated deletion.
