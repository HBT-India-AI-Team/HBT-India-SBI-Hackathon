PROJECT: YONO 3.0 Backend (redesigned)

Full spec (source of truth): `/backend/YONO_3.0_Backend_Redesign_BuildPrompts.md`.
Architecture detail: `/docs/ARCHITECTURE.md`. Mocks/debug-hooks inventory:
`/docs/MOCKS.md`.

CORE MODEL:
- User: a durable identity (mobile number, PAN, language preference).
- Application: the durable business object -- "this user is trying to open
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
  BOTH DERIVED from the state of these Requirements -- never stored/updated
  independently, always computed from the graph, to avoid drift.
- Ordering: Requirements are pursued in NATURAL ORDER (mobile -> identity ->
  guardian if applicable -> product confirm -> documents -> review) unless
  the user explicitly asks to go back and edit an already-VERIFIED one (a
  distinct, user-initiated code path -- never something the LLM decides on
  its own).

ARCHITECTURE PRINCIPLES:
1. All AI/LLM output is structured JSON validated server-side. The LLM
   proposes actions against Requirements; it never directly mutates
   Application/Requirement state. Every action is independently validated
   (format regex, dependency check, current requirement state) before being
   applied -- see `requirement_graph.submit_requirement_value()`.
2. The canonical message envelope is attached to a Session that resolves to
   an Application.
3. Every meaningful change emits an event (`services/events.py::emit()`)
   for the admin dashboard's WebSocket feed (`/ws/admin`).
4. Persistence is REAL (SQLite via SQLAlchemy) -- required for background
   jobs (simulated review turnaround, idle nudges) to work correctly across
   process time.
5. Real integrations where legally/practically possible for a hackathon
   (Telegram OTP send, Email/SMTP OTP send); mocked with clear MOCK-tagged
   comments where not (SMS OTP, STT, VLM doc extraction, Ollama LLM in this
   sandbox specifically since no Ollama server runs here,
   Aadhaar/PAN/DigiLocker/GSTIN government verification). Full precise
   inventory: `/docs/MOCKS.md`.

STACK:
- SQLAlchemy + SQLite for persistence (`backend/models/`)
- An in-process asyncio background poller (`backend/services/scheduler.py`)
  checking a ScheduledJob table for due jobs -- no external job queue infra
- Ollama for the conversation LLM (LLM-first with automatic rule-based
  fallback, `backend/services/onboarding_llm.py` / `rule_based_engine.py`)
- Whisper-shaped STT interface (`backend/services/stt.py`, mocked in this
  sandbox -- see MOCKS.md)
- Vision-capable-Ollama-shaped doc extraction interface
  (`backend/services/doc_parser.py`, mocked in this sandbox)

REPO LAYOUT:
/backend/models          -- SQLAlchemy models (models.py, db.py)
/backend/services         -- requirement_graph.py, product_catalog.py,
                             validators.py, scheduler.py, otp/, stt.py,
                             doc_parser.py, onboarding_llm.py,
                             rule_based_engine.py, handoff_tokens.py,
                             events.py
/backend/routers           -- applications.py, sessions.py, admin.py,
                             users.py  (no webhooks.py in this build --
                             see /docs/ARCHITECTURE.md's handoff section)
/backend/data                -- product_requirements.json, yono.db, uploads/
/backend/scripts               -- init_db.py, smoke_test_db.py,
                             demo_happy_path.sh, demo_msme_happy_path.sh,
                             demo_guardian_flow.sh, demo_handoff.sh
/backend/tests                  -- pytest unit tests for the Requirement Graph
/backend/main.py                 -- FastAPI app entrypoint (CORS, lifespan,
                             scheduler startup, router mounting)
/docs                              -- ARCHITECTURE.md, MOCKS.md, this file's
                             longer-form counterparts

RUNNING:
- `python3 -m backend.scripts.init_db` -- create tables
- `python3 -m backend.scripts.smoke_test_db` -- verify models work
- `python3 -m pytest backend/tests/` -- unit tests
- `uvicorn backend.main:app --reload --port 8000` -- run the API (run from
  repo root so the `backend.` package imports resolve)
- `backend/scripts/demo_*.sh` -- end-to-end curl demo scripts (server must
  already be running)

STATUS: see the build agent's final report for the authoritative
phase-by-phase built/mocked/skipped breakdown as of the last pass; this
file is kept in sync at a high level but the report is more precise about
what's freshly verified.
