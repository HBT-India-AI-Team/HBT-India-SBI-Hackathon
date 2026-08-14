# YONO 3.0 Backend Redesign — Claude Code Build Prompts

Purpose: this is a FRESH backend design that SUPERSEDES the backend sections
of earlier build-prompt files (the original rule-based-state-machine Phase 1,
and the Ollama integration in the Agent Docs/Test Harness file's Phase F).
If Claude Code has already built against those earlier specs, treat this as
a redesign/migration, not an addition — flag conflicts rather than running
both models side by side.

Core structural change from the earlier design: the backend is now built
around an **Application** (durable, product-specific, multi-session) rather
than a single ephemeral Session, and onboarding logic is driven by a
**Requirement Graph** derived from the product catalog rather than a
hand-maintained state enum. This is what makes product-varying KYC, resume-
after-drop-off, channel handoff, review/pending states, and nudges all work
as consequences of one model rather than as separate bolted-on features.

Run phases in order. Paste each into Claude Code, verify before continuing.

---

## Phase 0 — Context to paste once (save/update as `CLAUDE.md` in repo root)

```
PROJECT: YONO 3.0 Backend (redesigned)

CORE MODEL:
- User: a durable identity (mobile number, PAN, language preference).
- Application: the durable business object — "this user is trying to open
  this product." Has a real lifecycle and outlives any single conversation.
  This is the unit that Track Status, resume-after-drop-off, and channel
  handoff all revolve around.
- Session: one conversation instance (one web chat, one WhatsApp thread,
  one voice call). Always belongs to exactly one Application. A user can
  have multiple Sessions across time/channels against the same Application.
- Requirement Graph: instead of a fixed linear state enum, each Application
  has a set of Requirements derived from product_requirements.json for its
  product (mobile OTP, PAN, GSTIN, documents, guardian consent, product
  confirmation, etc.), each independently tracked as
  NOT_STARTED -> AWAITING_INPUT -> SUBMITTED -> VERIFYING -> VERIFIED |
  REJECTED -> ESCALATED. The Application's overall status
  (IN_PROGRESS / UNDER_REVIEW / ACTION_NEEDED / APPROVED / REJECTED /
  ABANDONED) and its macro progress step (1-5, for the UI stepper) are
  BOTH DERIVED from the state of these Requirements — never stored/updated
  independently, always computed from the graph, to avoid drift.
- Ordering: Requirements are pursued in NATURAL ORDER (mobile -> identity ->
  guardian if applicable -> product confirm -> documents -> review) unless
  the user explicitly asks to go back and edit an already-VERIFIED one (a
  distinct, user-initiated code path — never something the LLM decides on
  its own).

ARCHITECTURE PRINCIPLES (unchanged from earlier design, still apply):
1. All AI/LLM output is structured JSON validated server-side. The LLM
   proposes actions against Requirements; it never directly mutates
   Application/Requirement state. Every action is independently validated
   (format regex, dependency check, current requirement state) before being
   applied.
2. The canonical message envelope remains the cross-channel contract for
   Web/WhatsApp/Telegram/Voice — unchanged in spirit from the original
   design, just now attached to a Session that resolves to an Application.
3. Every meaningful change emits an event for the admin dashboard's
   WebSocket feed (requirement_updated, application_status_changed,
   hitl_item_added, etc.).
4. Persistence is now REAL (SQLite via SQLAlchemy), not in-memory — this is
   required for background jobs (simulated review turnaround, idle nudges)
   to work correctly across process time.
5. Real integrations where legally/practically possible for a hackathon
   (Telegram OTP send, Email/SMTP OTP send, local STT on voice messages);
   mocked with clear MOCK-tagged comments where not possible (SMS OTP,
   Aadhaar/PAN/DigiLocker/GSTIN verification — these require licensed
   entity status not obtainable for a prototype).

STACK ADDITIONS FOR THIS REDESIGN:
- SQLAlchemy + SQLite for persistence
- A simple in-process background poller (asyncio periodic task) checking a
  ScheduledJob table for due jobs — no external job queue infra needed
- Ollama for the conversation LLM (existing config pattern: OLLAMA_BASE_URL,
  auto-discovered model, LLM-first with automatic rule-based fallback)
- A local STT engine for voice messages (Whisper, self-hosted) — document
  as swappable for Bhashini or another ASR service later
- A vision-capable Ollama model (llava or similar, auto-discovered) for
  document field-extraction assistance — NOT for the actual verify/reject
  decision, which stays deterministic/mock-controlled

REPO LAYOUT:
/backend/models         — SQLAlchemy models
/backend/services        — requirement_graph.py, product_catalog.py,
                            scheduler.py, otp/, stt.py, doc_parser.py,
                            onboarding_llm.py
/backend/routers          — applications.py, sessions.py, admin.py,
                            webhooks.py
/backend/data              — product_requirements.json
/docs                        — architecture notes, this file
```

---

## Phase 1 — Data model and persistence

```
Set up SQLAlchemy + SQLite persistence for the redesigned backend.

1. Define these models in /backend/models/ (one file per model or grouped
   sensibly — your call, but keep it organized):

   User: id, mobile_number (unique, nullable until verified), pan_masked,
     language, created_at

   Application: id, user_id (FK), product_id, status (enum: IN_PROGRESS,
     UNDER_REVIEW, ACTION_NEEDED, APPROVED, REJECTED, ABANDONED),
     channel_origin, created_at, updated_at. Do NOT store current_step
     directly — add a computed property/method get_progress() that derives
     it from associated Requirements (built in Phase 2).

   Requirement: id, application_id (FK), type (enum matching product
     catalog requirement types), label, format_hint, mapped_step (1-5),
     state (enum: NOT_STARTED, AWAITING_INPUT, SUBMITTED, VERIFYING,
     VERIFIED, REJECTED, ESCALATED), failure_count, value (nullable,
     stores submitted value e.g. masked PAN), created_at, updated_at

   Session: id, application_id (FK), channel (web/whatsapp/telegram/voice),
     scope (nullable — used for guardian-scoped sessions, see Phase 9),
     started_at, last_active_at, status (active/idle/ended)

   Message: id, session_id (FK), direction, content_type, content_payload
     (JSON), timestamp — this persists the full conversation now, since
     sessions must be reconstructable after a restart

   ConsentRecord: id, application_id (FK), purpose, granted, timestamp

   DocumentSubmission: id, requirement_id (FK), file_ref, status,
     submitted_at, verified_at, extracted_fields (JSON, nullable — for
     VLM-assisted parsing results from Phase 7)

   ReviewItem (HITL): id, application_id (FK), type (enum: kyc_review,
     support_request), reason, status (open/resolved), decision, note,
     created_at, resolved_at

   ScheduledJob: id, application_id (FK), job_type (enum:
     document_review, idle_nudge_check), scheduled_for, status
     (pending/done/failed), payload (JSON)

   NotificationLog: id, application_id (FK), channel, message, mock_sent
     (bool), timestamp

   SupportTicket: id, application_id (FK), session_id (FK), type
     (chat/callback), status, created_at

   GuardianInfo: id, application_id (FK), mobile_number, relationship,
     proof_doc_ref, otp_verified (bool)

   DataRightsRequest: id, user_id (FK), request_type (access/deletion),
     status, created_at, fulfilled_at (nullable — fulfillment stays
     manual for the prototype, but the request itself is logged for real)

2. Set up the SQLAlchemy engine/session management
   (/backend/models/db.py), SQLite file at /backend/data/yono.db, and an
   Alembic-free simple approach: a script /backend/scripts/init_db.py that
   creates all tables from the models (fine for hackathon — no migration
   framework needed).

3. Write a smoke-test script /backend/scripts/smoke_test_db.py that creates
   a User, an Application, two Requirements, a Session, and a Message,
   commits, then reads them all back and prints them, to prove the models
   and relationships work before building anything on top.

Run init_db.py and smoke_test_db.py and show me the output.
```

---

## Phase 2 — Requirement Graph engine

```
Build the Requirement Graph engine — the core logic that replaces the old
rule-based state machine.

1. Extend /backend/data/product_requirements.json (if it already exists
   from earlier work) or create it now, ensuring each product entry
   includes a `requirements` array with entries shaped as:
   { type, label, format_hint, mapped_step, depends_on: [type,...],
     applicable_if: condition_or_null, escalation_threshold }
   Cover at minimum: mobile_otp, pan, gstin, business_pan,
   authorized_signatory, document (parameterized by doc name, e.g.
   "PAN Card photo", "GST Certificate"), guardian_consent,
   guardian_mobile_otp, product_confirm, review_submit. Not every product
   needs every type — a savings account gets mobile_otp + pan +
   product_confirm + review_submit; an MSME current account adds gstin +
   business_pan + authorized_signatory + relevant documents; add
   guardian_consent + guardian_mobile_otp when applicable_if matches a
   minor customer_type.

2. Build /backend/services/requirement_graph.py with these functions:
   - instantiate_requirements(application) -> creates the Requirement rows
     for a new Application based on its product's catalog entry (called
     once at Application creation)
   - get_next_requirement(application) -> the earliest NOT_STARTED or
     AWAITING_INPUT or REJECTED Requirement in natural order (respecting
     depends_on — skip any whose dependencies aren't yet VERIFIED)
   - compute_progress(application) -> { current_step, total_steps: 5,
     steps: [{index, label, status}] } derived purely from Requirement
     states — this becomes Application.get_progress()
   - compute_application_status(application) -> derives IN_PROGRESS /
     UNDER_REVIEW / ACTION_NEEDED / APPROVED / REJECTED from the current
     Requirement states (e.g. any Requirement in VERIFYING with none
     REJECTED/ESCALATED -> UNDER_REVIEW; any REJECTED or ESCALATED or
     awaiting guardian action -> ACTION_NEEDED; all VERIFIED -> APPROVED)
   - submit_requirement_value(application, requirement_id, value) ->
     validates the value against that requirement's type-specific
     validator (regex for PAN/GSTIN/mobile, accept-any-6-digit for OTP in
     mock mode), transitions state accordingly, increments failure_count on
     rejection, and auto-creates a ReviewItem (type=kyc_review) when
     failure_count reaches escalation_threshold
   - edit_verified_requirement(application, requirement_id, new_value) ->
     the explicit user-initiated "Edit" path from the Review screen —
     re-validates and re-triggers verification (moves back to SUBMITTED/
     VERIFYING) rather than accepting provisionally. Keep this function
     clearly separate from the natural-order flow, with a comment noting
     it's the ONLY legitimate way to touch an already-VERIFIED requirement.

3. Write unit tests (plain pytest, no need for elaborate fixtures) covering:
   a savings-account Application progressing through its requirements in
   order, an MSME Application with the extra requirements, a guardian-
   applicable Application including guardian requirements, and a PAN
   submission failing twice triggering escalation.

Run the tests and show me the results.
```

---

## Phase 3 — Core application/session endpoints

```
Build the core REST API using the Requirement Graph engine from Phase 2.

1. /backend/routers/applications.py:
   - POST /applications/start — accepts a handoff payload (source, product
     hint, language, channel, existing user identifier if known). Checks
     for an existing IN_PROGRESS/UNDER_REVIEW/ACTION_NEEDED Application for
     this user+product before creating a new one (resume instead of
     duplicate). Creates User if new, creates Application +
     instantiate_requirements() if new, creates a Session, returns the
     first bot prompt (from get_next_requirement()) plus full progress.
   - GET /applications/{id} — full Application detail: status, progress,
     all Requirements with their states, product info
   - GET /applications/by-user/{mobile} — list all Applications for a user
     (handles the "more than one product in progress" case flagged
     earlier — return as a list, let the frontend decide how to present it)
   - GET /applications/{id}/status — lighter-weight version of the above,
     specifically shaped for the Track Status screen
   - POST /applications/{id}/consent — creates a ConsentRecord, also
     resolves the corresponding `consent` Requirement if the product
     catalog models consent as a Requirement type
   - POST /applications/{id}/documents — file upload, creates a
     DocumentSubmission linked to the relevant Requirement, moves it to
     SUBMITTED then VERIFYING, schedules a document_review ScheduledJob
     (built in Phase 4)
   - POST /applications/{id}/edit/{requirement_id} — calls
     edit_verified_requirement()

2. /backend/routers/sessions.py:
   - POST /sessions/{id}/message — resolves session -> application, runs
     the message through the onboarding engine (rule-based version for
     now — Ollama comes in Phase 8), persists the Message rows (both
     inbound and outbound), returns the outbound envelope(s) + updated
     progress
   - GET /sessions/{id}/state — full reconstructable session state:
     message history + current application progress (for resume-after-
     reload)
   - POST /sessions/{id}/voice — stub for now (real STT comes in Phase 7),
     just accept the upload and return a placeholder response so the
     endpoint shape exists
   - POST /sessions/{id}/call/initiate and /call/end — same mock lifecycle
     as originally planned

3. For NOW (before Phase 8 replaces it), implement a simple rule-based
   message handler in /backend/services/rule_based_engine.py: takes the
   Application's get_next_requirement(), emits its templated prompt, and on
   the next inbound message attempts to match it against that
   requirement's expected input shape, calling submit_requirement_value().
   This is deliberately simple — it becomes the FALLBACK once Phase 8 adds
   the LLM, so don't over-build it.

4. Duplicate-user detection: when POST /applications/start would create a
   new Application, check whether a User already exists with a VERIFIED
   mobile_otp Requirement on any prior Application (i.e. their mobile
   number was previously confirmed) — if so and this is a genuinely new
   product request, that's fine (multiple products allowed), but if it's
   the SAME product already APPROVED, return a distinct duplicate-detected
   response instead of creating a new Application, for the frontend's
   "Looks like you already have an account" screen.

5. Write a demo script /backend/scripts/demo_happy_path.sh (curl-based,
   like before) walking a full savings-account application end to end
   using the rule-based engine, and a second script
   demo_msme_happy_path.sh walking an MSME application through its
   different requirement set, proving the graph genuinely varies by
   product rather than being hardcoded to one flow.

Run both scripts and show me the output.
```

---

## Phase 4 — Background job engine (real scheduler, mocked sends)

```
Build a real, working background job system — this is what makes
"Application Under Review" and nudges genuinely functional rather than
static UI.

1. /backend/services/scheduler.py: a simple asyncio-based periodic poller
   (runs every few seconds, configurable) that queries ScheduledJob for
   rows with status=pending and scheduled_for <= now, and dispatches each
   to a handler based on job_type. Start this poller as a background task
   on FastAPI app startup (lifespan event), not as a separate process — no
   external job queue infrastructure needed for the hackathon.

2. document_review job handler: when dispatched, looks up the
   DocumentSubmission, determines outcome via the DEBUG HOOK described in
   Phase 5 (not randomly — deterministic and controllable for demo
   reliability), sets the Requirement to VERIFIED or REJECTED accordingly,
   recomputes Application status, and broadcasts a requirement_updated
   admin event. When POST /applications/{id}/documents schedules this job,
   default the delay to a short, demo-friendly window (e.g. 15-30 seconds,
   configurable via config) — long enough to be visibly "under review" on
   screen, short enough not to stall a live demo.

3. idle_nudge_check job handler: a recurring job (schedule a fresh one for
   itself each time it runs, e.g. every few minutes) that finds
   Applications with status IN_PROGRESS or ACTION_NEEDED whose most recent
   Session's last_active_at is older than a configurable idle threshold AND
   that don't already have a NotificationLog entry within some cooldown
   window, and creates one: NotificationLog(channel=<a sensible guess based
   on channel_origin>, message=<a templated nudge referencing the specific
   next Requirement, e.g. "Finish verifying your PAN to complete your SBI
   account">, mock_sent=True). Actually sending stays mocked (this is what
   "fake the actual send" meant) — but the DECISION LOGIC and the
   scheduling itself are real and inspectable.

4. Add GET /applications/{id}/notifications to let the frontend (or you,
   for verification) see what nudges would have fired for a given
   Application.

5. Seed the idle_nudge_check recurring job once at app startup if one
   doesn't already exist in the table.

Test: create an application via the demo script, upload a document, and
show me — using GET /applications/{id} polled over time — that the
Requirement actually transitions from VERIFYING to VERIFIED/REJECTED after
the scheduled delay, without you manually triggering it. Then show the
idle_nudge_check logic firing for an application you've deliberately left
inactive past the threshold (lower the threshold temporarily for testing
if needed).
```

---

## Phase 5 — Debug/demo hooks for deterministic outcomes

```
Add explicit, documented hooks so document/requirement outcomes are
controllable on demand rather than random — critical for reliable demoing
and for the test-harness scenarios to be deterministic.

1. Add a `debug_outcome` optional field to the document upload request
   (POST /applications/{id}/documents) — accepts "verify" | "reject" |
   null. If provided, the scheduled document_review job uses this directly
   instead of any default logic. If null, default to "verify" (optimistic
   default so the normal happy-path demo doesn't require passing anything
   extra).

2. Similarly, allow the OTP validators (Phase 6) and PAN/GSTIN format
   validators to accept a small set of KNOWN TEST VALUES that deterministically
   pass or fail regardless of real format-checking, documented clearly
   (e.g. mobile number "0000000000" always fails as "already registered",
   PAN "FAILFAILFF" always fails format check) — list these in
   /docs/MOCKS.md or a new /docs/DEBUG_HOOKS.md, whichever this repo
   already uses for that kind of inventory (check /docs/MOCKS.md first,
   extend it rather than creating a duplicate file if it already exists
   from earlier work).

3. Add these debug hooks as fixtures in /test-harness/fixtures/ (if that
   folder exists from earlier work) so the automated validation agent can
   reliably trigger rejected/escalated states, not just the happy path.

This phase is small but important — reference /docs/DEBUG_HOOKS.md (or
wherever you documented it) explicitly in Phase 4 and 6's related code with
a comment pointing back to it.
```

---

## Phase 6 — OTP delivery: real Telegram, real Email/SMTP, mocked SMS

```
Implement OTP generation and delivery across three channels, being honest
about which are real and which are mocked.

1. /backend/services/otp/__init__.py: a generate_otp(application,
   requirement_id) function — generates a 6-digit code, stores it
   (hashed, with an expiry timestamp — add an otp_code_hash and
   otp_expires_at column to Requirement or a small separate OtpChallenge
   table, your call) — and a verify_otp(application, requirement_id, code)
   function checking match + not expired, with a clear distinct response
   for "wrong code" vs "expired code" (the frontend has separate copy for
   these per the wireframe review).

2. /backend/services/otp/telegram_sender.py: REAL implementation — uses
   the Telegram Bot API's sendMessage call (bot token from config) to
   deliver the OTP to a user's Telegram chat_id, IF this session's channel
   is telegram or the user has a linked Telegram chat_id on file. This
   reuses the bot token/adapter infrastructure from the earlier
   WhatsApp/Telegram adapter work if it exists — check for it before
   building a new one.

3. /backend/services/otp/email_sender.py: REAL implementation — uses
   smtplib (config: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
   SMTP_FROM_ADDRESS) to send the OTP code to an email address. Since the
   current flow collects mobile number, not email, add an OPTIONAL email
   field to the mobile_otp Requirement flow (or a distinct email_otp
   Requirement type) so this channel has something to send to — document
   this as a minor product-catalog addition if needed.

4. /backend/services/otp/sms_sender.py: MOCKED — clearly commented
   # MOCK: real SMS OTP requires DLT template registration in India,
   not feasible for a prototype. Log what would be sent instead, and
   accept the debug-hook test values from Phase 5.

5. Add config OTP_DELIVERY_CHANNEL (default depends on session channel:
   telegram sessions use telegram_sender, others default to sms_sender
   unless an email is provided, in which case prefer email_sender) —
   document the selection logic clearly in code comments and in
   /docs/ARCHITECTURE.md.

6. Update /docs/MOCKS.md: mark SMS OTP as mocked with its reason: Aadhaar/
   PAN/GSTIN/DigiLocker verification remain mocked (unchanged from before);
   mark Telegram OTP and Email OTP as REAL integrations, moving them out of
   the mocks inventory (or into a "real integrations" section if that
   distinction is useful to keep visible).

Test: trigger an OTP send via Telegram (if a bot token is available) and via
email (if SMTP credentials are available) and confirm actual delivery. If
credentials aren't available in this environment, show me the code path is
correct and tell me exactly what config values you'd need to test it for
real.
```

---

## Phase 7 — Voice: STT on messages, VLM-assisted document parsing

```
Add real speech-to-text for voice messages, and vision-assisted (not
authoritative) document field extraction.

1. /backend/services/stt.py: integrate a self-hosted Whisper model (use
   the openai-whisper or faster-whisper Python package, whichever installs
   more cleanly in this environment) to transcribe uploaded audio files.
   Wrap it behind a transcribe(audio_file) -> str function so it can be
   swapped for a different engine (e.g. Bhashini) later without touching
   callers. Document in a comment that Whisper's Hindi/vernacular accuracy
   is weaker than English — note this as a known limitation, not something
   to try to fix here.

2. Wire this into POST /sessions/{id}/voice (replacing the Phase 3 stub):
   transcribe the audio, then feed the resulting text through the SAME
   message-handling path as a normal text message (rule-based engine for
   now, LLM path once Phase 8 exists) — voice should not be a parallel
   pipeline, just a different way of producing the text input.

3. /backend/services/doc_parser.py: when a document is uploaded (PAN card
   photo, GST certificate, etc.), send the image to the auto-discovered
   vision-capable Ollama model with a prompt asking it to extract
   structured fields relevant to that document type (e.g. for a PAN card:
   {name, pan_number, date_of_birth}) as JSON. Store this in
   DocumentSubmission.extracted_fields. IMPORTANT: this extraction is
   informational/assistive only — it does NOT determine verified/rejected
   status (that's still the deterministic debug-hook-controlled job from
   Phase 4/5). Make this extraction best-effort: if the VLM call fails or
   times out, log it and continue — never block the upload flow on this
   succeeding.

4. Surface extracted_fields via GET /applications/{id} so the frontend
   could optionally show "here's what we read from your document, does
   this look right?" — this is a nice-to-have UI hook, not required to
   build the UI for it now, just expose the data.

Test: upload a real or sample document image and a real or sample audio
clip, show me the transcription output and the extracted fields output.
```

---

## Phase 8 — Ollama LLM grounding (multi-action, unified with the graph)

```
Upgrade the conversation engine from the Phase 3 rule-based handler to an
LLM-first engine, with the rule-based handler becoming its automatic
fallback — NOT a parallel system, but literally the same
get_next_requirement()-based logic from Phase 3, reused as-is for fallback.

1. Config (extend /backend/config.py from earlier if it exists, or create
   it now): OLLAMA_BASE_URL, OLLAMA_MODEL (auto-discovered via
   GET {base}/api/tags if unset, same pattern as before),
   OLLAMA_TIMEOUT_SECONDS, ONBOARDING_ENGINE_MODE default "llm".

2. Build /backend/services/onboarding_llm.py:
   - On each inbound message, construct a system prompt including: the
     Application's product + status, ALL currently outstanding
     Requirements (not just the next one) with their type/label/
     format_hint/state, a `next_suggested_requirement` pointer (from
     get_next_requirement() — natural order, as decided), recent
     conversation history, and language preference
   - Instruct the model to steer toward `next_suggested_requirement`
     unless the user's message is clearly a correction, question, or
     escalation request — but the model may propose MULTIPLE actions in
     one turn if the user provided multiple pieces of information at once
   - Require structured JSON output only:
     { reply_text: str, actions: [ { action: "submit_value",
     requirement_id: str, value: str } | { action: "escalate_to_human" } |
     { action: "switch_language", lang: str } | { action: "none" } ] }
   - Parse with Pydantic. For each submit_value action, call
     submit_requirement_value() from Phase 2 — which independently
     re-validates format and dependency state regardless of what the LLM
     claimed, exactly as the architecture principle requires. An action
     targeting an already-VERIFIED requirement (outside the explicit Edit
     path) or one whose dependencies aren't met should be silently
     dropped/logged, not applied.
   - escalate_to_human creates a ReviewItem(type=support_request) — see
     Phase 11 for the unified queue this feeds
   - switch_language updates User.language going forward only (not
     retroactive, as decided earlier)

3. FALLBACK: if Ollama is unreachable, times out, or returns invalid/
   unparseable JSON, fall back to the exact rule-based handler from
   Phase 3 for that turn only (log a warning noting which check failed).
   Since both paths ultimately call the same submit_requirement_value(),
   this fallback can't drift out of sync with the LLM path's validation —
   confirm this is actually true in your implementation, not just
   structurally similar.

4. Add GET /admin/llm/status reporting Ollama reachability + active model.

5. Update /docs/ARCHITECTURE.md with this orchestration layer, matching the
   note in the original Agent Docs file to keep this doc in sync (that
   file's Phase F is now superseded by this one — update or remove it if
   present, don't leave two conflicting descriptions in the repo).

Test: run the happy-path and MSME demo scripts from Phase 3 again, this
time through the LLM path (ONBOARDING_ENGINE_MODE=llm), including one
message that provides two pieces of information at once (e.g. "my number
is 9876543210 and my PAN is ABCDE1234F") to prove multi-action extraction
works. Then test the fallback by pointing OLLAMA_BASE_URL at an
unreachable address and confirming the conversation still completes
correctly. Show me both results.
```

---

## Phase 9 — Guardian-scoped verification flow

```
Implement the guardian co-onboarding flow using a scoped Session rather
than routing everything through the minor's own session.

1. When an Application's product/customer_type triggers guardian_consent +
   guardian_mobile_otp Requirements (per the product catalog's
   applicable_if condition), and the flow reaches those Requirements,
   create a GuardianInfo row (mobile_number collected via the normal
   conversation first) and a new Session with scope="guardian" linked to
   the same Application.

2. A scope="guardian" Session's message handler (in both the rule-based
   and LLM paths) should ONLY expose/resolve the guardian_consent and
   guardian_mobile_otp Requirements — reject or redirect any attempt to
   touch other Requirements through this session, even if asked, since
   this session represents the guardian, not the minor.

3. Generate a short-lived access link for this guardian session (reuse
   whatever token mechanism you build in Phase 10 for channel handoff —
   build that pattern once and reuse it here rather than inventing a
   second one) that could be sent to the guardian's number (actual sending
   can use the real Telegram/Email senders from Phase 6 if applicable, or
   just be returned in the API response for manual testing/demo purposes).

4. Confirm both the minor's own light KYC (mobile OTP at minimum, per the
   earlier decision to require both) and the guardian's consent+OTP are
   independently tracked as separate Requirements, both required for the
   Application to progress past that step.

Test: run a demo script for a minor-customer Application, showing the main
session gets blocked/waiting at the guardian step, the guardian link/token
is generated, and using that token in a separate scoped session resolves
the guardian requirements, after which the main flow can proceed. Show me
this end to end.
```

---

## Phase 10 — Channel handoff continuity

```
Implement "Continue on WhatsApp" (and the reverse: continuing a WhatsApp/
Telegram conversation on Web) using short-lived deep-link tokens.

1. /backend/services/handoff_tokens.py: generate_handoff_token(application,
   target_channel) -> a random, single-use, short-expiry (e.g. 15 minutes)
   token stored with a reference to the application_id and target_channel.
   consume_handoff_token(token) -> validates not expired/not used, marks
   used, returns the application_id.

2. Add POST /applications/{id}/handoff/{channel} — generates a token and
   returns a channel-appropriate link (e.g. a wa.me/<bot_number>?text=
   <token> style deep link for WhatsApp, a t.me/<bot>?start=<token> style
   link for Telegram, or a plain URL param for continuing on web from
   another channel).

3. Update the webhook handlers (WhatsApp/Telegram, from earlier work if
   present) and the web session-start endpoint to check for an incoming
   handoff token and, if present and valid, create a new Session against
   the EXISTING Application (via consume_handoff_token) rather than
   starting a fresh Application.

4. This is the same token mechanism Phase 9 reuses for guardian links —
   make sure handoff_tokens.py is generic enough to support both use cases
   (it already should be, since both are just "give someone a way in to an
   existing Application without full re-auth").

Test: generate a handoff token via the API, then simulate consuming it
through a fake webhook payload (reuse the sample payload pattern from
earlier Telegram/WhatsApp adapter work if it exists) and confirm it
resolves to the same Application rather than creating a new one. Show me
this.
```

---

## Phase 11 — Unified HITL/support queue + admin WebSocket feed

```
Unify KYC review escalations and customer-initiated support escalations
into one queue, and (re)build the admin WebSocket feed against the new
data model.

1. Confirm ReviewItem (Phase 1) already supports both type=kyc_review
   (auto-created by submit_requirement_value on escalation_threshold) and
   type=support_request (created by the LLM's escalate_to_human action in
   Phase 8, or a direct customer-facing "Talk to us" UI action — add
   POST /applications/{id}/support/escalate for that direct path, creating
   both a SupportTicket and a linked ReviewItem).

2. /backend/routers/admin.py — rebuild or update against the new model:
   - GET /admin/applications — list/filter Applications (status, channel,
     product) — this replaces the old session-centric /admin/sessions
   - GET /admin/applications/{id} — full detail: Application + all
     Requirements + Sessions + Messages
   - GET /admin/hitl/queue — unified list of open ReviewItems, both types,
     with a `type` field so the admin UI can visually distinguish them but
     query/manage them in one place
   - POST /admin/hitl/{item_id}/resolve — for kyc_review items, approving
     should call the appropriate Requirement transition (e.g. force
     VERIFIED); for support_request items, approving/resolving just closes
     the ticket — branch on `type` inside this one endpoint rather than
     building two
   - GET /admin/funnel/summary and /admin/funnel/by-channel — recompute
     against Application-level events now (application_started,
     requirement_verified events per type, application_approved) rather
     than the old session-based funnel events
   - GET /admin/consent/ledger, GET /admin/notifications (surfacing
     NotificationLog across all applications), GET /admin/data-rights
     (surfacing DataRightsRequest entries from Phase 1's model)

3. WebSocket /ws/admin: broadcast application_status_changed,
   requirement_updated, hitl_item_added, hitl_item_resolved,
   notification_logged, consent_logged — wire these into every relevant
   code path from Phases 2-10 (find each place state actually changes and
   add the broadcast call, same pattern as originally planned).

4. If earlier admin-dashboard work already exists against the old
   session-centric endpoints, update it to match these new shapes rather
   than maintaining both — flag clearly if this requires frontend changes
   in the separate admin dashboard project, since that's a different repo.

Test: run a demo script that triggers both a kyc_review escalation (PAN
fails twice) and a support_request escalation (direct escalate call), open
a WebSocket connection to /ws/admin, and show me both items appearing in
the unified queue with correct types, both resolvable through the same
endpoint.
```

---

## Phase 12 — DPDP data-rights request logging

```
Make the "Access your data" / "Request deletion" stub screens back onto a
real, logged request — fulfillment stays manual, but the request itself
should be genuine and auditable.

1. Add POST /users/{id}/data-rights/access and
   POST /users/{id}/data-rights/deletion — each creates a
   DataRightsRequest row (status defaults to "pending_manual_review") and
   returns a confirmation.

2. Add GET /admin/data-rights (if not already added in Phase 11) listing
   all requests, with a manual "mark fulfilled" action
   (POST /admin/data-rights/{id}/fulfill) an admin could use — this is
   intentionally manual, not automated deletion, since automated deletion
   across a real system needs more care than a hackathon timeline allows.

3. Document in /docs/ARCHITECTURE.md (or MOCKS.md, whichever fits better)
   that this is a REAL request-logging mechanism but fulfillment is a
   manual/future step — this distinction matters for being honest about
   what's built if a judge asks about DPDP compliance specifically.

Test: submit one access request and one deletion request, confirm both
appear via the admin endpoint, mark one fulfilled, and show me the result.
```

---

## Notes on sequencing

- Phases 1-3 are foundational — everything else depends on the data model
  and Requirement Graph existing correctly. Don't parallelize these.
- Phase 4 (background jobs) and Phase 5 (debug hooks) are tightly coupled —
  build them together or Phase 4's testing will be unreliable.
- Phase 8 (LLM) depends on Phase 2's Requirement Graph and reuses Phase 3's
  rule-based handler as its fallback — don't attempt Phase 8 before both
  exist.
- Phases 6, 7, 9, 10 are relatively independent of each other once Phases
  1-3 exist — can be reordered based on which part of the demo narrative
  you want to strengthen first (real OTP channels vs voice vs guardian flow
  vs channel handoff).
- Phase 11 touches the separate admin dashboard project's expectations —
  budget time to also update that project's frontend if its earlier build
  assumed the old session-centric endpoint shapes.
- This file should become the new source of truth referenced from
  /docs/ARCHITECTURE.md — Phase 0's context block explicitly says to
  update CLAUDE.md, make sure that actually happens rather than leaving
  the old context file describing the superseded design.
