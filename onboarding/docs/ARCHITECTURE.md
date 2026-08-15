# YONO 3.0 Backend -- Architecture

Source of truth for the design this implements:
`/backend/YONO_3.0_Backend_Redesign_BuildPrompts.md`. This doc supersedes
any earlier session-centric / rule-based-state-machine architecture notes
from prior build-prompt files -- if you find an older architecture doc
describing a single ephemeral Session model without an Application layer,
it is stale and should be treated as superseded by this one.

## Core model

- **User** -- durable identity (mobile number, PAN, language).
- **Application** -- the durable business object ("this user is trying to
  open this product"). Outlives any single conversation. `status` and
  `get_progress()` are NEVER stored independently -- always derived from
  the Application's `Requirement` rows via
  `backend/services/requirement_graph.py`.
- **Session** -- one conversation instance (web/whatsapp/telegram/voice),
  always belongs to exactly one Application.
- **Requirement Graph** -- each Application has `Requirement` rows derived
  from `backend/data/product_requirements.json` for its product. States:
  `NOT_STARTED -> AWAITING_INPUT -> SUBMITTED -> VERIFYING -> VERIFIED |
  REJECTED -> ESCALATED`. Natural order + `depends_on` dependency gating
  determine `get_next_requirement()`.

## Request flow (text/voice message)

`POST /sessions/{id}/message` (or `/voice`, after transcription) ->
`backend/routers/sessions.py::_process_inbound_text` -> engine selection
(`ONBOARDING_ENGINE_MODE`, default `"llm"`) ->
`backend/services/onboarding_llm.py::handle_message` (falls back to
`backend/services/rule_based_engine.py::handle_message` on any Ollama
unreachable/timeout/invalid-JSON condition) -> both paths ultimately call
`backend/services/requirement_graph.py::submit_requirement_value()`, which
independently re-validates format/dependency state regardless of what
either engine claims -- this is what keeps the LLM path from being able to
drift out of sync with the fallback's validation.

## OTP channel selection (Phase 6)

`backend/services/otp/dispatch.py::_pick_channel`:
1. If `OTP_DELIVERY_CHANNEL` env var is explicitly `telegram`/`email`/`sms`,
   use it.
2. Else (`"auto"`, default): if the latest Session's channel is
   `"telegram"` and the User has a `telegram_chat_id` on file -> Telegram
   (real if `TELEGRAM_BOT_TOKEN` set, else mock-logged).
3. Else if the User has an `email` on file -> Email (real if `SMTP_HOST`
   set, else mock-logged).
4. Else -> SMS (always mock -- see `/docs/MOCKS.md`).

## Background jobs (Phase 4)

`backend/services/scheduler.py::poller_loop()` runs as an asyncio task
started from `backend/main.py`'s FastAPI `lifespan`, polling the
`ScheduledJob` table every `SCHEDULER_POLL_INTERVAL_SECONDS` (default 3s).
Two job types:
- `document_review` -- scheduled by `POST /applications/{id}/documents`
  with a `DOCUMENT_REVIEW_DELAY_SECONDS` (default 12s) delay. Outcome is
  controlled by the Phase 5 `debug_outcome` hook on the
  `DocumentSubmission` row (see `/docs/MOCKS.md`), defaulting to
  `"verify"`.
- `idle_nudge_check` -- a recurring, self-rescheduling job (interval
  `IDLE_NUDGE_CHECK_INTERVAL_SECONDS`, default 60s) that finds
  Applications idle past `IDLE_THRESHOLD_SECONDS` (default 300s) without a
  `NotificationLog` entry inside `NUDGE_COOLDOWN_SECONDS` (default 300s),
  and logs one (`mock_sent=True` -- decision logic and scheduling are
  real, the actual send is mocked). Seeded once at startup by
  `scheduler.seed_idle_nudge_job_if_missing()`.

## Admin WebSocket feed (Phase 11)

`backend/services/events.py` is a tiny in-process event bus: any
state-changing code path calls `events.emit(event_type, payload)`. It is a
safe no-op until a broadcaster is registered. `backend/routers/admin.py`
registers a `_ConnectionManager.broadcast` as that broadcaster at import
time and exposes `/ws/admin`, which fans every emitted event out to
connected admin WebSocket clients. Event types emitted:
`application_status_changed`, `requirement_updated`, `hitl_item_added`,
`hitl_item_resolved`, `notification_logged`, `consent_logged`. Emit call
sites: `requirement_graph.py` (state transitions, escalation),
`applications.py` (consent, document upload, handoff, support escalate,
guardian link), `onboarding_llm.py` (LLM-driven escalate_to_human),
`scheduler.py` (document_review outcome, idle nudges), `admin.py` (HITL
resolution).

## Channel handoff / guardian links (Phase 9 & 10)

Both reuse the same generic `HandoffToken` table and
`backend/services/handoff_tokens.py` (`generate_handoff_token` /
`consume_handoff_token` / `build_deep_link`) -- single-use, short-expiry
(`HANDOFF_TOKEN_TTL_SECONDS`, default 900s) tokens. Guardian links set
`purpose="guardian_link"`, which `consume_handoff_token` maps to
`scope="guardian"` on the resulting Session; ordinary channel handoff uses
`purpose="handoff"` with no scope. A `scope="guardian"` Session's message
handler (both engine paths) only resolves `guardian_consent` /
`guardian_mobile_otp` Requirements -- see
`requirement_graph.get_next_requirement(scope=...)` and the guardian-type
guard in `onboarding_llm.handle_message`.

A live Telegram webhook receiver now exists: `POST /webhooks/telegram`
(`backend/routers/webhooks.py`, registered in `backend/main.py`) accepts
Telegram's standard Update payload, resolves a `/start <handoff_token>`
deep-link message (or a bare token) via the same
`handoff_tokens.consume_handoff_token()` used by
`POST /applications/start`, and on success sets `User.telegram_chat_id`
and opens a new `Session(channel="telegram")` against the resolved
Application -- this is what makes the `OTP channel selection` telegram
branch above practically reachable (previously nothing ever populated
`telegram_chat_id`). Verified in this sandbox by generating a real handoff
token via `POST /applications/{id}/handoff/telegram` and POSTing a
simulated Telegram update referencing it directly to `/webhooks/telegram`
(see `/docs/MOCKS.md` for the exact request/response proof). Non-token or
malformed messages are handled without crashing (`{"ok": true, "linked":
false, ...}` / `{"ok": false, "reason": "invalid_json"}`).

What's still needed to receive REAL Telegram traffic (not just simulated
POSTs to this route): a real `TELEGRAM_BOT_TOKEN` from @BotFather (none is
configured here), and this route deployed behind a public HTTPS URL
registered with Telegram via `setWebhook`
(`https://api.telegram.org/bot{TOKEN}/setWebhook?url=<public-https-url>/webhooks/telegram`).
`backend/scripts/check_telegram_connectivity.py` (mirrors
`check_ollama_connectivity.py`) verifies the configured token against
`GET .../getMe` and can optionally send a real test message via
`telegram_sender.send()` given `--chat-id`; in this sandbox it exits
cleanly at step 1 with a clear "no token configured" message (both because
no token is set and because `api.telegram.org` is itself blocked by this
sandbox's outbound allowlist, same as the Ollama ngrok domain).
There is still no live WhatsApp webhook receiver -- WhatsApp handoff
remains token-generation/consumption only, demonstrated via
`backend/scripts/demo_handoff.sh` rather than a simulated webhook call.

## Voice: STT / TTS / live call (Phase 7, extended)

A separately-running voice AI server (own Whisper-shaped STT + TTS models,
documented in `/reference/voice_ai_server_client/`, run by the user on
their own machine and exposed via an ngrok tunnel) is now genuinely
integrated, following the exact same real-call-with-graceful-fallback
pattern used elsewhere (`doc_parser.py`'s VLM calls, `telegram_sender.py`).
Config lives in `backend/config.py` (`VOICE_SERVER_URL`,
`VOICE_SERVER_API_KEY`, `VOICE_CLIENT_SAMPLE_RATE`,
`VOICE_CLIENT_FRAME_MS`) -- if not set in `backend/.env`/real env vars, it
falls back to reading `reference/voice_ai_server_client/.env` directly so
the two don't have to be hand-kept-in-sync.

- **STT** (`backend/services/stt.py::transcribe()`) -- real `POST
  {VOICE_SERVER_URL}/transcribe` (multipart file upload, Bearer auth,
  ~45s timeout since this is a slower model call than Ollama text). Falls
  back to a canned transcript on any failure, returning a dict
  (`{"text", "language", "latency_ms", "_mock", "_source"}`) instead of a
  bare string so callers can tell real vs mock apart.
  `POST /sessions/{id}/voice` (`backend/routers/sessions.py`) consumes
  this dict and feeds `result["text"]` through the exact same
  `_process_inbound_text` path as a normal text message -- unchanged from
  before, voice is not a parallel pipeline.
- **TTS** (`backend/services/tts.py::synthesize()`) -- real `POST
  {VOICE_SERVER_URL}/synthesize` (JSON body, Bearer auth), returns raw WAV
  bytes on success or `None` on any failure (never raises).
  `POST /sessions/{id}/voice`'s response now additionally includes
  `reply_audio_base64` (base64-encoded WAV, or `null`) and
  `reply_audio_mock` (bool) alongside the pre-existing `transcript` /
  `reply_text` / `actions_applied` / `progress` fields -- existing fields
  are unchanged, this is a pure addition.
- **Live call proxy** (`backend/routers/calls.py`, `WS
  /sessions/{id}/call/live`) -- a bidirectional relay between a browser
  client and the voice server's own `WS /call` endpoint. We proxy rather
  than let the browser connect to the upstream voice server directly
  because the upstream connection requires `VOICE_SERVER_API_KEY` as a
  query-string token, and that key must never reach the browser -- the
  backend holds it and opens the outbound `websockets` connection itself.
  Binary PCM16 audio frames and JSON control frames are relayed as-is in
  both directions (two concurrent `asyncio` pump tasks). While relaying,
  upstream `{"type":"transcript",...}` / `{"type":"reply_text",...}`
  frames are persisted as `Message(content_type="voice_transcript"
  |"voice_reply_text")` rows on the Session, and an upstream
  `{"type":"call_ended",...}` frame (or the browser disconnecting first)
  finalizes the call via `sessions.end_call_for_session()` -- the exact
  same function `POST /sessions/{id}/call/end` calls, so there is one
  "call ended" state transition, not two parallel concepts. If the
  upstream connect itself fails/times out (the path actually exercised in
  this sandbox -- no network route to the configured ngrok domain), the
  browser WS is closed with code `4503` / reason `"voice server
  unreachable"` rather than hanging; a resolvable-but-missing session
  closes with `4404`.
- **Connectivity check**: `backend/scripts/check_voice_server_connectivity.py`
  (mirrors `check_ollama_connectivity.py` / `check_telegram_connectivity.py`)
  checks `/health`, `/transcribe` (using
  `reference/voice_ai_server_client/synthesized.wav` as the fixture),
  `/synthesize`, and a brief `WS /call` handshake, in that order, with a
  clear pass/fail per step and non-zero exit on any failure. In this
  sandbox it fails at every step (network to the ngrok domain is blocked
  by the outbound allowlist) -- expected, meant to be re-run by the user
  on a machine with real access to `VOICE_SERVER_URL`.

## DPDP data-rights (Phase 12)

`POST /users/{id}/data-rights/access` / `/deletion`
(`backend/routers/users.py`) create real, auditable `DataRightsRequest`
rows. `GET /admin/data-rights` lists them, `POST
/admin/data-rights/{id}/fulfill` is a manual admin action. Fulfillment
itself is NOT automated -- see `/docs/MOCKS.md` for why.

## FinGuru (India-context financial Q&A assistant)

Added by the FinGuru build (`YONO_3.0_FinGuru_ClaudeCode_BuildPrompts.md`), a
distinct feature that lives alongside onboarding rather than inside it --
`backend/routers/finguru.py` is a separate router, `backend/services/finguru_engine.py`
a separate engine -- but deliberately SHARES infrastructure instead of
duplicating it. Wireframe reference + exact palette: `design/finguru_wireframes/NOTES.md`.

### Data model (its own rows, `backend/models/models.py`)
- `FinGuruTopic` -- a grounded, citable knowledge entry (`category`:
  fin_wiki/product/govt_scheme). Seeded from `backend/data/finguru_knowledge/*.json`
  via `backend/scripts/seed_finguru_knowledge.py`. Carries `eligibility_tags`
  (schemes explorer chips), `query_count` (trending), `needs_review` (same
  "populated via web research" disclaimer pattern as product_requirements.json).
- `FinGuruConversation` / `FinGuruMessage` -- FinGuru's own chat history (its
  `user_id` is nullable: FinGuru can be used before/without onboarding).
  `FinGuruMessage` carries `citations` + `follow_up_suggestions` alongside the
  normal `content_payload`.
- `ResearchRequest` -- the gap-filling queue row; links to the shared HITL
  queue via `review_item_id` (FK to the EXISTING `ReviewItem` table) rather
  than a parallel queue table.

### What FinGuru shares with onboarding (not duplicated)
- **Ollama config** -- `finguru_engine.py` reads `backend/config.py`'s
  `OLLAMA_BASE_URL`/`OLLAMA_MODEL` and reuses `onboarding_llm._discover_model()`
  directly, rather than a second model-discovery implementation. (It does use
  its own timeout/output-budget constants, `FINGURU_LLM_TIMEOUT_SECONDS` /
  `FINGURU_LLM_NUM_PREDICT` -- FinGuru answers are longer free text than
  onboarding's short action JSON, and needed more headroom to avoid truncation.)
- **Product catalog** -- the onboarding-handoff feature (Phase 8) validates a
  cited topic's `product_id:<id>` tag against `product_catalog.get_product()`,
  the exact function `applications.py` uses, instead of hand-rolling a second
  product lookup.
- **HITL queue** -- gap-filling (Phase 4) raises a `ReviewItem(type="content_research")`
  into the SAME table/`GET /admin/hitl/queue` that `kyc_review`/`support_request`
  items use; `POST /admin/hitl/{id}/resolve` (the existing endpoint, extended)
  branches on the new type to create a `FinGuruTopic` from the admin's answer.
- **NotificationLog** -- a research-answered notification is logged through the
  exact same table/pattern as onboarding idle nudges (`channel:"finguru"`).
- **STT pipeline** -- `POST /finguru/conversations/{id}/voice` calls
  `services/stt.transcribe()`, the same function `POST /sessions/{id}/voice`
  calls, then feeds the transcript through the identical `ask()` path as text.
- **`/applications/start`** -- the onboarding-handoff card (Phase 8) calls this
  exact endpoint with `source:"finguru"`, producing a real `Application`, not a
  separate/mocked transition.
- **Admin `/ws/admin` event bus** -- `research_answered` is emitted through the
  same `services/events.py` used everywhere else.

### What's distinct to FinGuru
- **Retrieval**: `finguru_engine.retrieve_relevant_topics()` -- simple
  stopword-filtered, word-boundary keyword/tag scoring over `FinGuruTopic`
  (deliberately not embeddings-based, documented in-code as the natural next
  upgrade). Follow-up questions are retrieved with the previous user message
  appended, since plain keyword matching has no memory of its own.
- **Grounding discipline**: `ask()` will NOT let the model answer outside the
  retrieved context -- empty retrieval short-circuits straight to
  `confidence:"not_covered"` without an LLM call at all. A soft-fail state
  (`confidence:"unavailable"`) is distinguished from `not_covered` so a
  transient Ollama outage never triggers the gap-filling "want me to research
  this?" flow for what's actually just a retry-worthy error.
- **Comparison mode** (`POST /finguru/compare`): a second, genuinely ungrounded
  Ollama call (no retrieved context, no citation instruction) exists ONLY for
  this explicit side-by-side feature -- it is never used as a fallback path.
- **Lightweight heuristics, not ML**: `_detect_suggested_widget()` (SIP
  calculator trigger) and `_detect_fraud()` (scam-keyword awareness) are both
  small, documented keyword checks, not classifiers -- fraud detection in
  particular runs before retrieval/LLM, so it's instant and deterministic.

## What's mocked vs real

See `/docs/MOCKS.md` for the full, precise inventory -- do not duplicate
that list here; this file covers where each mocked/real piece is wired
into the request flow, not which pieces are which.
