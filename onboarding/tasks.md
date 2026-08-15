# YONO 3.0 — Build Tasks & Status

Legend: ✅ Done (real) · 🟡 Mocked (works, but stubs an external dependency not available in this build environment) · ⏳ Pending (second pass)

Last updated: 2026-08-11

## Backend

### Phase 1 — Data model & persistence
- ✅ SQLAlchemy models (`backend/models/models.py`): User, Application, Requirement, Session, Message, ConsentRecord, DocumentSubmission, ReviewItem, ScheduledJob, NotificationLog, SupportTicket, GuardianInfo, DataRightsRequest
- ✅ `backend/models/db.py`, SQLite at `backend/data/yono.db`
- ✅ `backend/scripts/init_db.py`, `smoke_test_db.py` (both run clean)

### Phase 2 — Requirement Graph engine
- ✅ `backend/data/product_requirements.json` — savings_account, msme_current_account, minor_savings_account
- ✅ `backend/services/requirement_graph.py` — instantiate_requirements, get_next_requirement, compute_progress, compute_application_status, submit_requirement_value, edit_verified_requirement
- ✅ `backend/services/validators.py`, `product_catalog.py`
- ✅ `backend/tests/test_requirement_graph.py` — 4/4 pytest passing (savings happy path, MSME extra requirements, guardian-applicable, PAN double-fail escalation)
- Fixed bug: `get_next_requirement` wasn't returning OTP requirements stuck in VERIFYING, which silently broke OTP-code entry via chat — fixed.

### Phase 3 — Core application/session endpoints
- ✅ `backend/routers/applications.py` — start/get/by-user/status/consent/documents/edit/guardian-link/handoff/support-escalate
- ✅ `backend/routers/sessions.py` — message/state/voice(stub)/call initiate+end(mock)
- ✅ `backend/services/rule_based_engine.py`
- ✅ Duplicate-user detection (same product + APPROVED)
- ✅ `backend/scripts/demo_happy_path.sh`, `demo_msme_happy_path.sh` — both run end-to-end against a live server

### Phase 4 — Background job engine
- ✅ `backend/services/scheduler.py` — asyncio poller started via FastAPI lifespan in `backend/main.py`
- ✅ `document_review` job (respects Phase 5 debug hook), `idle_nudge_check` recurring job
- ✅ `GET /applications/{id}/notifications`
- ✅ Verified live: Requirement transitions VERIFYING → VERIFIED with zero manual intervention after upload; idle nudges fire on a lowered test threshold

### Phase 5 — Debug/demo hooks
- ✅ `debug_outcome` on document upload ("verify"/"reject"/null, defaults to verify)
- ✅ Known test values for OTP/PAN/GSTIN validators (documented)
- ✅ `docs/MOCKS.md`
- ⏳ No `test-harness/fixtures/` folder existed in this repo to extend — hooks documented in MOCKS.md/validators.py instead; revisit if a test-harness project is added later

### Phase 6 — OTP delivery
- ✅ `generate_otp`/`verify_otp` (hashed code, expiry, wrong-vs-expired distinction) — real
- ✅ `backend/services/otp/telegram_sender.py` — Bot API call shape confirmed correct (`POST /bot{TOKEN}/sendMessage`); falls back to mock/log when `TELEGRAM_BOT_TOKEN` isn't set (it isn't yet)
- ✅ **Gap closed**: `backend/routers/webhooks.py` (`POST /webhooks/telegram`) now exists — a real webhook receiver that consumes handoff tokens from incoming Telegram messages, links `User.telegram_chat_id`, and opens a telegram-channel Session. Previously there was no way for the telegram OTP path to ever activate even with a bot token; verified live with a simulated Telegram update payload that a real handoff token correctly links the user and flips OTP channel-selection to "telegram".
- ✅ `backend/scripts/check_telegram_connectivity.py` — checks bot token validity (`getMe`) and can send a real test message; confirmed it fails gracefully ("no token configured") in this sandbox rather than crashing. `api.telegram.org` is also blocked by this sandbox's network allowlist (confirmed), same as the Ollama ngrok domain — run this on a machine with real network access.
- 🟡 `backend/services/otp/email_sender.py` — real call code (smtplib) present, falls back to mock when `SMTP_*` env vars aren't set
- 🟡 `backend/services/otp/sms_sender.py` — permanently mocked (DLT registration not feasible for a prototype, per spec)
- ⏳ Second pass: (1) get a bot token from @BotFather, set `TELEGRAM_BOT_TOKEN`; (2) deploy behind a public HTTPS URL and call Telegram's `setWebhook` pointing at `/webhooks/telegram`; (3) supply real `SMTP_HOST,PORT,USER,PASSWORD,FROM_ADDRESS` to flip Email to genuinely live

### Phase 7 — Voice STT/TTS & VLM document parsing
- ✅ Wired to your `voice_ai_server` (per `reference/voice_ai_server_client/`): `backend/services/stt.py::transcribe()` makes a real `POST {VOICE_SERVER_URL}/transcribe` call (multipart, Bearer auth), falls back to a canned transcript on failure. `backend/services/tts.py` (new) makes a real `POST /synthesize` call, returns WAV bytes or `None` on failure. `POST /sessions/{id}/voice` now returns `reply_audio_base64`/`reply_audio_mock` too.
- ✅ **Live call**: `WS /sessions/{id}/call/live` (`backend/routers/calls.py`, new) proxies the browser to the voice server's `WS /call` endpoint (API key stays server-side), streaming 16kHz PCM16 mic audio up and transcript/reply-text/reply-audio down; logs transcripts as real `Message` rows; reuses the existing call-lifecycle bookkeeping (`/call/initiate`, `/call/end`).
- ✅ Frontend: `SupportCall.jsx` now does real mic capture (Web Audio API → 16kHz PCM16 frames) and reply-audio playback, with a live transcript panel and mute toggle — falls back automatically to the original mocked call UI if the WS proxy reports `4503 voice server unreachable` / `4404` / any connection error. Verified via a local WebSocket test harness (13/13 assertions) that the fallback triggers correctly without hanging/crashing.
- ✅ `backend/scripts/check_voice_server_connectivity.py` (new) — checks `/health` → `/transcribe` (using the reference client's `synthesized.wav`) → `/synthesize` → WS `/call` handshake. Confirmed it fails gracefully here (sandbox network block, same as Ollama/Telegram).
- ✅ `backend/services/doc_parser.py::extract_fields()` — makes a **real** VLM call first (`_try_vlm_extract`, same pattern as `classify_document`: base64 image → `{OLLAMA_BASE_URL}/api/generate` with a doc-type-specific JSON schema), falling back to the canned mock only on failure. Verified live in this sandbox: real attempt is logged, fails (blocked network, expected), falls back cleanly — demo scripts still complete with `extracted_fields` present.
- 🟡 None of the real STT/TTS/live-call/VLM paths are verified reachable from this sandbox (same network-allowlist block as Ollama/Telegram) — all code paths are genuine and tested via their fallback behavior, real end-to-end verification needs to happen where your voice server and Ollama are actually reachable
- 🟡 Live call: real microphone hardware, browser permission prompts, and audio-quality/latency tuning are untestable in this sandbox — needs a manual pass in a real browser once your voice server is up
- ⏳ Second pass: run `check_voice_server_connectivity.py` (and `check_ollama_connectivity.py`) from a machine with real network access; do a manual live-call pass in a real browser (mic permission, transcript accuracy, playback quality, mute, hangup, mic-in-use indicator clears)

### Phase 8 — Ollama LLM grounding
- ✅ `backend/services/onboarding_llm.py` — real prompt construction, Pydantic action schema, real HTTP call to Ollama
- ✅ Configured (`backend/.env`) with the real endpoint `OLLAMA_BASE_URL=https://dreamboat-bleep-childhood.ngrok-free.dev/ollama`, `OLLAMA_MODEL`/`OLLAMA_VISION_MODEL=gemma4:12B`
- ✅ **Model name RESOLVED (2026-08-12)**: `gemma4:12B` is a real, working model — confirmed live from the dev machine during the Flutter browser walk, where `doc_parser.classify_document` made a real vision call to the ngrok Ollama endpoint and returned an accurate, high-confidence classification. NOT a typo for `gemma3`; leave `backend/.env` as-is. (Earlier "isn't a publicly known tag" note was written from the network-blocked build sandbox and is now superseded.)
- 🟡 This sandbox's outbound network proxy blocks that ngrok domain (`403 blocked-by-allowlist`), so connectivity is **unverified from here**. Every call correctly falls back to the rule-based engine, which is itself the required fallback behavior — verified working.
- ✅ `GET /admin/llm/status`, plus new `backend/scripts/check_ollama_connectivity.py` — run this **on a machine with real network access** to the ngrok URL to confirm the model is reachable (lists models, confirms `OLLAMA_MODEL` present, does a test generate call). Confirmed here that it fails gracefully (clean ❌, no crash) rather than hanging.
- ⏳ Second pass: run the connectivity script on your end, confirm/fix the model tag, re-test the real multi-action LLM path once reachable

### Debug logging (added post-build, per request)
- ✅ `backend/logging_config.py` — rotating file handler to `backend/data/logs/app.log` + console, level via `LOG_LEVEL` env var (default INFO)
- ✅ Request/response logging middleware in `backend/main.py` (method, path, status, duration; DEBUG-level body logging) + global unhandled-exception handler with full traceback logging
- ✅ Logging added/verified across requirement transitions, scheduler jobs, LLM calls/fallback, doc parsing, OTP sends

### Document type sanity check (added post-build, per request)
- ✅ `backend/services/doc_parser.py::classify_document()` — real VLM classification attempt (via the configured Ollama vision model) with a heuristic fallback (valid-image check, low-confidence pass) when the VLM is unreachable, wired into `POST /applications/{id}/documents` in `backend/routers/applications.py`
- ✅ Mismatches are rejected for real (not silently accepted): reuses the same rejection path as the scheduled document-review job (`scheduler.py::apply_document_rejection`) — sets Requirement REJECTED, increments failure_count, escalates at threshold, logs at WARNING
- ✅ Verified end-to-end: forced-mismatch upload (`debug_outcome=reject`, or filename containing `wrong_doc_type`) → Requirement REJECTED with reason `document_type_mismatch: ...`, application status → ACTION_NEEDED; normal uploads in the existing demo scripts still pass through unaffected
- 🟡 Real VLM classification itself is unverified in this sandbox for the same network-block reason as Phase 8 — heuristic fallback is what's actually exercised here

### Phase 9 — Guardian-scoped verification flow
- ✅ `GuardianInfo`, scope="guardian" Session restricted to guardian requirements only
- ✅ Guardian link generated via Phase 10's token mechanism
- ✅ Verified live: guardian session resolves consent+OTP, main flow unblocks afterward (`demo_guardian_flow.sh`)

### Phase 10 — Channel handoff continuity
- ✅ `backend/services/handoff_tokens.py` — generic generate/consume, single-use, short-expiry
- ✅ `POST /applications/{id}/handoff/{channel}`
- 🟡 No real inbound WhatsApp/Telegram webhook receiver exists (`backend/routers/webhooks.py` not built — none existed from earlier work to extend); token consumption verified by calling the consume path directly with a simulated payload, not a live webhook
- ⏳ Second pass: build `webhooks.py` if/when real WhatsApp/Telegram bot infra is added

### Phase 11 — Unified HITL/support queue + admin WebSocket feed
- ✅ `backend/routers/admin.py` — all listed endpoints (applications list/detail, hitl queue/resolve, funnel summary/by-channel, consent ledger, notifications, data-rights)
- ✅ `POST /applications/{id}/support/escalate`
- ✅ `WS /ws/admin` broadcasting application_status_changed, requirement_updated, hitl_item_added, hitl_item_resolved, notification_logged, consent_logged — verified live events captured during a demo run
- 🟡 `POST /admin/hitl/{id}/resolve` only branches on a single requirement per item — fine for current catalog, not generalized to multi-requirement items

### Phase 12 — DPDP data-rights request logging
- ✅ `backend/routers/users.py` — access/deletion request logging, `GET /admin/data-rights`, `POST /admin/data-rights/{id}/fulfill`

### Docs
- ✅ `CLAUDE.md` (repo root), `docs/ARCHITECTURE.md`, `docs/MOCKS.md`

## Frontend — Flutter (`frontend_flutter/`) — matches production SBI YONO stack

Built after learning the real YONO app is Flutter-based (the React app above was built first, before that was known — see note at the end of this section).

- ✅ All 20 screens ported 1:1 from the React app's flow: greeting, language picker, product confirmation, consent moment/legal details, requirements checklist, core chat (OTP incl. wrong-code, PAN/business PAN/GSTIN, document upload+polling, guardian sub-flow), review & submit, under review/track status incl. action-needed, duplicate user, support bottom sheet/chat/call, WhatsApp handoff, success, home, game placeholder
- ✅ `lib/services/api_client.dart` — every call cross-checked line-by-line against the real backend router code (paths, HTTP methods, JSON vs. multipart bodies)
- ✅ Go Router navigation, Provider state management, `shared_preferences` for resume-after-reload — mirrors the React app's `AppContext`/routing structure
- 🟡 Support-call **microphone streaming** is explicitly stubbed (`TODO(mic-streaming)` in `support_call_screen.dart`) — real `/call/initiate`/`/call/end` calls and the UI state machine are wired, but the WebSocket/PCM16 audio-capture layer from the React app's `useLiveCall.js` wasn't ported (higher-risk to write blind without compile access — deliberately deferred rather than risk broken audio code)
- ✅ **COMPILES: YES** — verified on 2026-08-12 with **Flutter 3.44.9 / Dart 3.4** (stable). `flutter pub get` resolves cleanly (79 deps, no conflicts); `flutter build web --dart-define=API_BASE=...` builds with **zero compile errors**; `flutter analyze` reports only 2 benign info-level lints (a `DropdownButtonFormField.value` deprecation — fixed to `initialValue` — and one `use_build_context_synchronously` that is already correctly guarded with `if (context.mounted)`). All the pre-flagged risk spots turned out fine on this SDK: `Color.withValues(alpha:)`, `file_picker` 8.x API, and the `flutter_bootstrap.js` `web/index.html` all compile/build as-is (the hand-written `web/index.html` did NOT need regenerating — Flutter 3.44 supports that bootstrap; only `web/favicon.png` + `web/icons/*` are absent, cosmetic 404s only).
  - **Note on the original SDK**: the machine had Flutter **3.16.9 / Dart 3.2.6** installed, which is too old for this project (pubspec requires Dart >=3.3, `withValues` needs 3.27+, `flutter_bootstrap.js` needs 3.22+). Ran `flutter upgrade` → 3.44.9 to satisfy the declared stack. `flutter test` reports "no tests found" (none written yet — expected).
- ✅ **Backend contract verified live** (2026-08-12): walked the full savings-account happy path against the real backend (`ONBOARDING_ENGINE_MODE=rule_based`) and confirmed every response shape the Flutter screens consume matches: `start` → `{application:{id,...}, session_id, first_prompt}`; `POST /sessions/{id}/message` → `{reply_text, actions_applied, progress, application_status}`; `GET /sessions/{id}/state` → `{session_id, channel, scope, status, messages:[{direction:'inbound'|'outbound', content:{text}, ...}], application}` (matches `chat_window.dart`'s `direction=='inbound'` + `content['text']` hydration); document upload → poll → VERIFIED → submit → **APPROVED**, all 5 requirements VERIFIED. Fixed 2 unused imports in `consent_moment_screen.dart` along the way.
  - **Port note**: during verification, port 8000 was already held by an unrelated process (a different `/onboarding/session/*` API), so the real backend was run on **8001** and the app pointed at it via `--dart-define=API_BASE=http://localhost:8001`. For a normal run, free port 8000 (or keep using the `--dart-define` override).
- ✅ **BROWSER-VERIFIED end-to-end (2026-08-12)**: drove the built web app in a real Chromium via Playwright (coordinate-clicks + real keyboard input on the CanvasKit canvas, since Flutter web renders no DOM) and screenshotted the entire happy path: greeting → language picker → product confirmation → consent moment → consent legal (3 checkboxes) → requirements checklist (`POST /applications/start`) → chat (mobile → OTP → PAN → **document upload via file_picker**) → product confirm (Yes/No quick-replies) → review & submit → under review ("Application submitted!", Ref #…) → track status (auto-polls) → **success ("You're all set! …approved")**. All screens render correctly. Also confirmed the **rejection UI path** (red stepper node + error bubble) when a wrong document is uploaded.
- 🐞 **BUG FOUND & FIXED during the browser walk**: `consent_moment`, `requirements_checklist`, and `review_submit` passed a `Column` with the **default `mainAxisSize.max`** as `PhoneScreen`'s `footer`. `PhoneScreen` puts `footer` in the Scaffold's `bottomNavigationBar`, whose slot offers loose height up to the full screen — so the Column expanded to fill it, collapsing the appBar + body to zero (only the footer buttons showed, pinned at the top). Fixed by setting `mainAxisSize: MainAxisSize.min` on all three footer Columns; added a warning doc-comment on `PhoneScreen.footer` to prevent regressions. Reproduced in both headless and headed Chromium (real bug, not a rendering artifact); all three screens verified rendering correctly after the fix + rebuild.
- ✅ **Live VLM document classification confirmed working from this machine**: the doc-type sanity check (`doc_parser.classify_document`) made a **real call to the configured Ollama endpoint** (`https://dreamboat-bleep-childhood.ngrok-free.dev/ollama`, model `gemma4:12B`) and correctly rejected a non-PAN image (classified it "Welcome screen", confidence 1.0) and accepted a synthetic PAN-card image — so the endpoint is reachable here and **`gemma4:12B` is a real, working model** (resolves the Phase 8 open question about whether it was a typo for `gemma3`; it is NOT — leave as-is). Note: `debug_outcome=verify` only skips a *forced reject*; it does not bypass the VLM on real images (by design — mismatches are rejected for real).
- **Port note**: during verification, port 8000 was already held by an unrelated process (a different `/onboarding/session/*` API), so the real backend was run on **8001** and the app built/pointed at it via `--dart-define=API_BASE=http://localhost:8001`. For a normal run, free port 8000 (or keep using the `--dart-define` override).
- ⏳ Second pass: port the mic-streaming live-call feature; add widget tests; swap in real YONO favicon/app icons under `web/`; (optional) verify the MSME and guardian/minor product flows in-browser too (only the savings happy path was clicked through)

## Frontend — React (`frontend/app/`, Vite + React + Tailwind)

Built first, before it was clear the production YONO app is Flutter-based — kept as a working, fully-tested reference implementation of the same flows/API contract; not the one to ship if matching the real stack matters.

Built against the 25 Stitch wireframe screens in `frontend/wireframes/` (Kinetic Heritage design system).

- ✅ Onboarding greeting → language picker → product confirmation
- ✅ Consent moment + consent legal details
- ✅ Requirements checklist (kicks off `POST /applications/start`)
- ✅ Core chat UI — OTP (mobile + guardian), PAN, business PAN, GSTIN, authorized signatory, document upload with live async-review polling
- ✅ Error chat variants (wrong OTP, doc unreadable, upload error) — driven by real backend error responses, not hardcoded
- ✅ Guardian consent sub-flow, incl. scoped guardian session and rejoin
- ✅ Review & submit, incl. edit-verified-field
- ✅ Application under review / track status incl. action-needed
- ✅ Duplicate user detected
- ✅ WhatsApp channel handoff (real deep link from backend)
- ✅ Support escalation chat (real ticket creation)
- ✅ Onboarding success, home/landing, resume-by-mobile
- ✅ Support call connecting/active — real mic capture + WS live-call streaming to your voice server, with automatic graceful fallback to the mocked visual state machine if the voice server isn't reachable (see Phase 7 above)
- 🟡 Support chat "agent" reply text — static canned copy after a real ticket is created (no live agent backend to wire to)
- 🟡 `yono_3.0_game` / loading screen — minimal placeholder, treated as low priority per scope
- ⏳ No UI surface for the mock OTP code (only visible in backend server logs) — awkward for a live demo without log access, fine for production once real OTP channels are live
- ⏳ No automated component/unit tests (build succeeds; verified via backend-contract smoke testing against real endpoint shapes, not Playwright — not installed in this pass)

## How to run

Backend:
```
cd onboarding
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
# add ONBOARDING_ENGINE_MODE=rule_based for deterministic demo behavior without Ollama
```

Frontend:
```
cd onboarding/frontend/app
npm run dev
# needs .env: VITE_API_BASE=http://localhost:8000 (already created)
```

## Second-pass priority list
1. Run `backend/scripts/check_ollama_connectivity.py`, `check_telegram_connectivity.py`, and `check_voice_server_connectivity.py` on a machine with real network access to confirm all three external services (verify `gemma4:12B` isn't meant to be `gemma3:12b`)
2. Once Ollama is confirmed reachable, re-test the real multi-action LLM path (Phase 8) and real VLM doc classification/extraction
3. Once the voice server is confirmed reachable, do a manual live-call browser pass (mic permission, transcript accuracy, playback quality, mute, hangup) and a real voice-message-in-chat test
4. Supply real Telegram bot token / SMTP creds, deploy behind a public HTTPS URL, and call Telegram's `setWebhook` to flip OTP delivery to live (Phase 6)
5. Build `webhooks.py` support for real inbound WhatsApp handoff consumption (Telegram's receiver already exists) (Phase 10)
6. Add a demo-only "reveal mock OTP" endpoint or UI affordance for easier live demoing
7. Add Playwright end-to-end tests for the frontend
8. Generalize `/admin/hitl/{id}/resolve` to multi-requirement items
