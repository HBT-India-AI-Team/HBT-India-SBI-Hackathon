# FinGuru Build Log

Running log of the 9-phase FinGuru build (`YONO_3.0_FinGuru_ClaudeCode_BuildPrompts.md`).
Each entry: what was built, what was verified (real result), deviations + why, open questions.

Environment note (applies to every phase): the onboarding backend is run on
**port 8001** for verification because an unrelated process already holds port
8000 in this environment. Ollama is reachable from this machine at the
configured ngrok endpoint with model `gemma4:12B` (confirmed during the earlier
Flutter verification), so real grounded LLM calls are exercisable here.

---

## Phase 1 — Setup: wireframe reference, data model, entry point

**Built**
- Extracted the FinGuru wireframes to `/design/finguru_wireframes/` (from the
  `stitch_yono_3.0_gen_z_banking_finguru.zip` the user confirmed) and wrote
  `/design/finguru_wireframes/NOTES.md` — exact gold/blue hex tokens (sampled
  from `finguru_extension/DESIGN.md`), mascot/shape/type language, chat
  conventions, and a folder→screen→phase map for all 13 wireframes. Referenced
  by every later UI phase.
- Backend data model (extended existing `backend/models/models.py`, no parallel
  persistence layer): `FinGuruTopic`, `FinGuruConversation`, `FinGuruMessage`,
  `ResearchRequest` (with `review_item_id` FK to the existing `ReviewItem`).
  Added forward-looking columns so later phases don't need migrations:
  `FinGuruTopic.eligibility_tags` (Phase 6), `.query_count` (Phase 5),
  `.needs_review` (stakeholder-review disclaimer pattern).
- `backend/routers/finguru.py` — new, cleanly separated router (Phase 1: a
  `/finguru/health` ping; real endpoints land in Phases 2/4/5/6/7/8). Registered
  in `backend/main.py`.
- Flutter entry point: gold-accented **"Ask FinGuru"** tile on the home tile
  strip (`home_screen.dart`, `_AskFinGuruTile`) routing to a new **`/finguru`**
  route (`router.dart`) → Phase-1 placeholder `screens/finguru/finguru_home_screen.dart`.
  Added FinGuru gold tokens to `theme/app_colors.dart` (`finguruGold`,
  `finguruGoldText`, `finguruCream`, `finguruPeach`, …).

**Verified (real results)**
- `python -m backend.scripts.init_db` → all 4 FinGuru tables present
  (`finguru_topics`, `finguru_conversations`, `finguru_messages`,
  `research_requests`) alongside the existing tables.
- Backend restarted on :8001; `GET /finguru/health` → `{"ok":true,"feature":"finguru"}` (200).
- `flutter build web --dart-define=API_BASE=http://localhost:8001` → **built
  clean, zero errors** with the new route/tile/tokens.

**Deviations + why**
- **Relaxed `ReviewItem.application_id` to `nullable=True`.** Phase 4 raises a
  `content_research` ReviewItem from a FinGuru conversation that may have no
  onboarding Application. SQLite can't drop a NOT NULL in place and the project
  deliberately has no migration framework ("no migration framework — fine for a
  hackathon", per its own Phase-1 note + create_all-only `init_db`), so I
  **recreated the dev `yono.db`** (dropped the regenerable demo data from the
  earlier Flutter walk; demo scripts re-seed as needed). Existing `kyc_review`
  /`support_request` code paths always set `application_id`, so they're unaffected.
- Prerequisite gate: the required `/design/finguru_wireframes/` did not exist;
  the user confirmed extracting it from the shipped finguru zip before I began.

**Unsure / open**
- The wireframes imply a global 5-tab bottom nav (Home/Game/FinGuru/Accounts/
  Profile); the existing onboarding app has none. Plan: give the FinGuru screens
  their own header + bottom nav in Phase 3 for fidelity, without retrofitting a
  global nav onto the onboarding flow. Will confirm in Phase 3.

---

## Phase 2 — Backend: knowledge base + grounded LLM chat engine

**Built**
- `/backend/data/finguru_knowledge/` seed data (3 JSON files, **19 topics**),
  each with a `source_url` and `needs_review=True` (same "populated via web
  research, needs stakeholder review" disclaimer as product_requirements.json):
  - `fin_wiki.json` (7): SIP, NAV, TDS, KYC, Credit Score/CIBIL, Expense Ratio, Fixed Deposit.
  - `products.json` (4): SBI Savings / MSME Current / Minor Savings (each tagged
    `product_id:<id>` so Phase 8 handoff can map to product_catalog without
    duplicating product data) + SBI Fixed Deposit.
  - `govt_schemes.json` (8): SSY, PPF, APY, NPS, PM-KISAN, PMJDY, PMAY, PMJJBY —
    each with `eligibility_tags` (Phase 6 chips) and a scheme-type tag
    (savings/pension/housing/insurance) for the Phase 6 filter.
  - Fact-sensitive figures (interest rates, limits, eligibility) were sourced via
    **web search** (RBI/SEBI/official portals; rates as of Q1–Q2 FY2026-27).
- `backend/scripts/seed_finguru_knowledge.py` — idempotent upsert by stable slug
  id into `FinGuruTopic`.
- `backend/services/finguru_engine.py`:
  - `retrieve_relevant_topics()` — keyword/tag scoring (threshold 3.0 so
    off-topic questions correctly retrieve nothing); commented that embeddings
    retrieval would replace this later.
  - `ask()` — grounds on retrieved topics, **reuses** the onboarding LLM's
    `_discover_model` + the shared Ollama config, requires strict JSON
    `{answer_text, citations[], follow_up_questions[], confidence}`, validates
    citations against retrieved ids (no hallucinated ids), and:
    - short-circuits to `not_covered` (no LLM call) when nothing relevant is
      retrieved — never answers ungrounded;
    - **context-aware retrieval**: anchors a follow-up to the previous user
      question (keyword retrieval is memoryless on its own);
    - soft-fails to a graceful `confidence="unavailable"` message (distinct from
      `not_covered`) on any Ollama/JSON error, with a one-retry loop and
      first-balanced-object JSON extraction to survive the ngrok endpoint's
      truncation / trailing-"Extra data" quirks.
- Endpoints in `backend/routers/finguru.py`: `POST /finguru/conversations/start`,
  `POST /finguru/conversations/{id}/message` (persists inbound+outbound
  FinGuruMessage with citations + follow_up_suggestions), `GET
  /finguru/conversations/{id}` (history/resume), `GET /finguru/topics/{id}`
  (glossary). `backend/scripts/demo_finguru.sh` exercises all three required cases.

**Verified (real results, live Ollama `gemma4:12B` on :8001)**
- Seed: `created=19 ... {'fin_wiki':7,'govt_scheme':8,'product':4}`.
- **Grounded** ("How does SSY work?") → correct 8.2% p.a. answer, citation
  `scheme_ssy`, 2 follow-ups, `confidence:"grounded"`. Same for PPF (cites
  `scheme_ppf`).
- **not_covered** ("latest update on the upcoming tech merger?") → returns the
  gap response, `confidence:"not_covered"`, no LLM call, no citations.
- **Context-aware follow-up** ("what is the minimum I can deposit each year?"
  after a PPF question) → grounded "₹500 per financial year" answer.

**Deviations + why**
- Added `confidence="unavailable"` (beyond the spec's grounded|partial|not_covered
  enum) for transient LLM outages, so the Phase-4 gap-filling flow is NOT offered
  for what is actually a temporary error.
- Raised the LLM timeout (`FINGURU_LLM_TIMEOUT_SECONDS=45`) and output budget
  (`FINGURU_LLM_NUM_PREDICT=900`) vs the onboarding 6s default — FinGuru answers
  are longer and the short timeout truncated the JSON.

**Unsure / open**
- The ngrok-tunneled Ollama is **intermittently flaky** under rapid back-to-back
  requests (truncated JSON / dropped connection) — the one-retry + soft-fail
  handle it, and spaced-out calls succeed reliably, but a burst demo may show the
  occasional graceful "having trouble" bubble. Not a code bug; external endpoint.
- Simple keyword retrieval occasionally pulls an extra loosely-related topic into
  the grounding set (harmless — the model is told to use only what's relevant and
  cite what it used); embeddings retrieval is the real fix later.

---

## Phase 3 — Frontend: FinGuru home + chat (matches the wireframes)

**Built** (all cross-referenced against design/finguru_wireframes/ + NOTES.md)
- `lib/services/finguru_api.dart` — reuses `apiBase`/`ApiException` from
  api_client.dart (one base-URL + one error type): start/message/getConversation/getTopic.
- `lib/widgets/finguru/finguru_scaffold.dart` — shared FinGuru chrome: gold
  "💡 FinGuru" wordmark header + the 5-tab bottom nav (Home·Game·**FinGuru**·
  Accounts·Profile, FinGuru = blue pill). Home/Game/FinGuru route for real;
  Accounts/Profile are fidelity-only no-ops.
- `lib/widgets/finguru/finguru_widgets.dart` — `FinGuruAnswerBubble` (gold mascot
  avatar, white card, read-aloud speaker, citation pill row, outlined-gold
  follow-up chips) + `FinGuruUserBubble`. **Tap-to-define glossary**: cited topic
  titles inside the answer are rendered as gold dotted-underline links
  (WidgetSpan) that open a bottom sheet (`showTopicSheet` → GET /finguru/topics/{id})
  with summary → "Learn more" (full body + last-verified) → "Source" (url_launcher).
  Supports `researched`/`gap`/`fraud` variants for Phases 4 & 8.
- `lib/screens/finguru/finguru_home_screen.dart` — real home: "How can I guide you
  today?", ask bar (search + gold mic), Trending row, three Explore-Knowledge
  tiles (Fin Wiki/Products/Govt Schemes with accent bars), "What others are
  asking" list. Trending + others are hardcoded stubs (→ real data in Phase 5).
- `lib/screens/finguru/finguru_chat_screen.dart` — starts a conversation, sends
  messages, renders answers/citations/follow-ups; not_covered → gap variant
  (opt-in research chips added in Phase 4). Route `/finguru/chat` (initial
  question via `extra`).
- Backend: `POST /finguru/conversations/{id}/voice` — **reuses** `services/stt.transcribe`
  (same pipeline as onboarding voice), transcribes then runs the same ask() path.

**Verified (real, in a headed Chromium via the Playwright harness, backend :8001)**
- FinGuru **home** renders and matches the wireframe (wordmark, ask bar + gold
  mic, trending cards, 3 accent tiles, "what others are asking", bottom nav).
- Typing "How does the Sukanya Samriddhi Yojana work?" in the ask bar → navigates
  to chat → **grounded answer** rendered with the SSY glossary term as a gold
  dotted-underline link, a "🔖 Sukanya Samriddhi Yojana (SSY)" citation pill, and
  two outlined-gold follow-up chips.
- Tapping the glossary term opens the **tap-to-define bottom sheet** (title +
  summary + Learn more + Source) — GET /finguru/topics/{id} live.
- `flutter build web` compiles clean.

**Deviations + why**
- **Voice input recorder is a frontend stub** (mic shows "coming soon"). The
  Flutter app never ported a MediaRecorder mechanism (only the React app had one),
  so there was nothing to "reuse" per the prompt; rather than build a blind web
  audio recorder, the mic is stubbed while the **backend voice endpoint is real**
  (reuses STT). Same posture as the app's other mic features (live-call streaming
  is already a documented TODO in tasks.md).
- **Read-aloud (TTS) speaker is a stub** (snackbar). Per the Phase 3 prompt's
  "wire to TTS if one exists, else stub + TODO": backend `tts.synthesize` exists
  but there's no FinGuru audio-reply endpoint yet, so the button is a TODO stub.
- The "Last verified: <date>" label from the citation wireframe is shown in the
  glossary sheet (which fetches the topic) rather than inline in the citation pill,
  because the /message citations payload carries only {topic_id, label}.
- FinGuru screens carry their own header + bottom nav (the onboarding app has no
  global nav) — matches the wireframes without retrofitting the onboarding flow.

**Unsure / open**
- App uses hash-based web routing (`/#/finguru`), so deep-linking needs the hash;
  in-app navigation from the home "Ask FinGuru" tile works normally.

---

## Phase 4 — Gap-filling / research queue loop

**Built**
- Backend: `POST /finguru/research-requests` creates a `ResearchRequest(status="queued")`
  **and** a `ReviewItem(type="content_research")` in the **same** unified admin
  HITL table `GET /admin/hitl/queue` already serves (no parallel queue).
  `POST /admin/hitl/{id}/resolve` (existing endpoint, extended, in the real
  admin router) now branches on `type=="content_research"`: creates/stores a new
  `FinGuruTopic` from the admin's `answer_text`/`source_url`, marks the linked
  `ResearchRequest` "answered", appends a `researched:true` `FinGuruMessage` to
  the original conversation, creates a `NotificationLog` entry (**reusing** the
  exact mechanism onboarding nudges use — not a second notification system),
  and emits `research_answered` over the existing `/ws/admin` feed.
  `GET /finguru/conversations/{id}/research-updates` feeds the home banner.
- Frontend: gap bubble now shows **"Yes, research this" (gold filled) / "No
  thanks"** chips (finguru_info_not_found wireframe); opting in posts the
  research request and shows the **"research queued"** cream bubble
  (finguru_research_queued wireframe). FinGuru home shows the peach
  **"FinGuru found an answer…"** banner (finguru_home_answer_ready_notification)
  when `AppState.finguruConversationId` (newly added, persisted like the rest of
  AppState) has an answered research update; tapping it resumes that exact
  conversation (`FinGuruChatScreen(conversationId:)`, hydrated via `GET
  /finguru/conversations/{id}`) and shows the answer with a gold **"💡
  Researched for you"** tag (finguru_researched_answer_result wireframe).

**Verified (real, full loop, both curl and UI via Playwright)**
- curl: research-request → appears in `/admin/hitl/queue` as `content_research`
  → resolve with `answer_text`+`source_url` → `FinGuruTopic` created, `ResearchRequest`
  answered, conversation gains the researched message, `/admin/notifications`
  shows a `channel:"finguru"` entry.
- **UI, screenshot-verified end to end**: asked "Who won the football match last
  night?" → gap bubble + chips rendered → tapped "Yes, research this" → queued
  bubble rendered → resolved as admin (curl) → reloaded FinGuru home → peach
  banner rendered with the exact question quoted → tapped it → resumed the same
  conversation → **"💡 Researched for you"** tagged answer bubble with citation
  pill rendered, matching the wireframe.

**Deviations + why**
- Relaxed `NotificationLog.application_id` to nullable (same SQLite-recreate
  rationale as Phase 1's `ReviewItem` change) since FinGuru notifications may have
  no onboarding Application.
- **Fixed a retrieval-precision bug found while testing this phase**: stopword-like
  tokens (e.g. "the") were substring-matching topic bodies (`"the" in "there"`),
  so some genuinely off-topic questions were spuriously retrieving grounding and
  never reaching `not_covered`. Added a stopword list and switched to word-boundary
  (word-set) matching in `retrieve_relevant_topics()`. Verified: off-topic
  questions now retrieve `[]` deterministically (no LLM call, no flakiness) and
  on-topic questions (SSY/PPF/SIP) still retrieve correctly.
- Also fixed a small pre-existing bug hit during this phase's testing: the FinGuru
  home ask-bar text wasn't cleared after navigating to chat, so a second visit
  could interleave old+new text into one garbled question. Fixed by clearing the
  controller in `_openChat`.

**Unsure / open**
- The client-side-only "queued" confirmation bubble (shown immediately after
  tapping Yes) is not persisted as a FinGuruMessage — by design (it's a UI
  acknowledgement, not part of the answer thread), so it won't reappear if the
  chat is resumed later; only the real question → not_covered → researched-answer
  sequence persists. Worth a note, not a bug.

---

## Phase 5 — Trending topics and discovery

**Built**
- `FinGuruTopic.query_count` (added in Phase 1 already) is incremented in
  `POST /finguru/conversations/{id}/message` for every topic cited in an answer.
- `GET /finguru/trending?limit=` — top topics by `query_count`. **Chose
  all-time** (not a 7-day rolling window): a proper window needs a per-citation
  event log rather than a counter column, which is extra complexity not worth it
  for the hackathon — documented inline in the endpoint's docstring per the
  prompt's "your call, note which you chose".
- `GET /finguru/recent-questions?limit=` — anonymized (text only, no user id)
  feed from recent inbound `FinGuruMessage` rows, de-duplicated.
- Frontend: FinGuru home's Trending row and "What others are asking" now fetch
  these two endpoints on load (loading indicator → real cards, or an explicit
  empty state — "No trending topics yet" / "No questions yet" — instead of silently
  showing nothing or stale stub content). Removed the Phase-3 hardcoded stub lists
  entirely.

**Verified (real)**
- Seeded real activity (asked "What is a SIP?", "Tell me about PPF", "How does
  SSY work?", "What is TDS?") then: `GET /finguru/trending` → 4 topics each
  `query_count:1` (sip, tds, scheme_ssy, scheme_ppf); `GET /finguru/recent-questions`
  → the real recent question text, newest first, de-duplicated.
- **UI screenshot**: FinGuru home's Trending row shows the real topic titles/
  summaries (SIP, TDS, …) and "What others are asking" shows the real recent
  questions ("What is TDS?", "How does the Sukanya Samriddhi Yojana work?", "Tell
  me about PPF") — the Phase 3 stub content is gone.

**Deviations + why**
- None beyond the all-time-vs-7-day call noted above.

**Unsure / open**
- **Process note, not a product bug**: verifying a rebuilt Flutter web app in the
  browser requires disabling cache via CDP (`Network.setCacheDisabled`) before
  reload — a plain reload kept serving the previous build's `main.dart.js` from
  the browser's HTTP cache even after clearing the Cache Storage API and
  unregistering the service worker. Worth remembering for the rest of this build.

---

## Phase 6 — Government schemes explorer + SIP calculator

**Built**
- `GET /finguru/schemes?category=` — `FinGuruTopic` rows where `category="govt_scheme"`,
  optionally filtered by a scheme-type tag (savings/insurance/pension/housing,
  seeded per-scheme in Phase 2). Shaped for the list-card UI (title, summary,
  `eligibility_tags`, source_url).
- `finguru_engine._detect_suggested_widget()` — simple keyword heuristic ("sip"
  + calculat/grow/how much/future value/maturity) attached as `suggested_widget`
  on the `ask()`/message response.
- Frontend: `FinGuruSchemesScreen` (`/finguru/schemes`) — filter chip row (All/
  Savings/Insurance/Pension/Housing), scrollable cards with eligibility chips
  and an expand chevron revealing the full body. `SipCalculatorCard` — pure
  frontend math (`FV = P × (((1+r)^n − 1)/r) × (1+r)`, monthly-compounded),
  three gold sliders, Estimated Value + invested/returns split bar + legend,
  "Invest Now" (demo-only snackbar, no real investment flow exists to call).
  Rendered **alongside** the text answer in chat when `suggested_widget ==
  "sip_calculator"`.

**Verified (real)**
- `GET /finguru/schemes` → all 8; `?category=savings` → exactly `[scheme_ssy,
  scheme_ppf, scheme_pmjdy]`; `?category=housing` → `[scheme_pmay]`.
- Asking a SIP-calculator-worded question → `suggested_widget:"sip_calculator"`.
- **UI screenshots**: schemes explorer renders all 8 cards matching the
  wireframe; tapping "Savings" correctly filters to 3 cards with the chip
  highlighted. Asking "...sip calculator...₹5000/month for 10 years..." in chat
  renders the calculator card **with the exact same ₹11,61,695 estimated value**
  as the wireframe's own example inputs — confirms the FV formula matches.

**Deviations + why:** none.

**Unsure / open:** none new.

---

## Phase 7 — Live comparison mode (FinGuru vs generic AI)

**Built**
- `finguru_engine.ask_generic(question_text)` — the SAME Ollama model, but with
  NO retrieved-topic grounding context and NO citation instruction, just "Answer
  this financial question: ..." — genuinely representing what an ungrounded
  generic assistant would say (not a second call down the grounded path).
- `POST /finguru/compare` — runs the real `ask()` (grounded) and `ask_generic()`
  (ungrounded) for the same question, returns them labeled `finguru_answer` /
  `generic_answer`.
- Frontend: `FinGuruCompareScreen` (`/finguru/compare`) — the split-card view
  from the wireframe (blue outer panel, white "FinGuru" card with a gold "✓
  Cited & India-specific" badge + Sources list, gray italic "Generic AI" card
  with "No specific sources cited"), plus an "Ask another question" flow that
  re-runs the comparison. Triggered by a **balance-scale icon button in the chat
  header** (`_openCompare`) using the last question asked — an explicit opt-in,
  not the default per-question experience, per the prompt.

**Verified (real, live Ollama on both sides)**
- curl: asked "How does the Sukanya Samriddhi Yojana work?" → `finguru_answer`
  grounded with `citations:[{topic_id:"scheme_ssy",...}]`; `generic_answer` a
  differently-worded, uncited paragraph — genuinely two different answers.
- **UI screenshot**: tapped the compare icon in chat → "Live Comparison Mode"
  header → blue panel renders the question, the FinGuru card (gold badge,
  cited, concise, Form-15G/26AS-specific detail) and the Generic AI card
  (gray, italic, much longer, generic, unsourced) — visually and substantively
  distinct, matching the wireframe.

**Deviations + why:** none.

**Unsure / open**
- Same ngrok-Ollama flakiness as earlier phases affects the FinGuru side of a
  compare call more than the generic side (the generic call uses no
  `format:"json"` constraint, so it isn't vulnerable to the JSON-truncation
  failure mode) — occasionally worth a retry on a live demo, not a code bug.

---

## Phase 8 — Trust, safety, and onboarding handoff

**Built**
- Persistent **disclaimer footer** on the FinGuru chat screen: "FinGuru gives
  educational information, not personalized investment advice. Learn more" —
  "Learn more" opens a new short static `FinGuruDisclaimerScreen`
  (`/finguru/disclaimer`).
- **Fraud/scam awareness**: `finguru_engine._detect_fraud()` — a small,
  documented keyword list (guaranteed returns, double your money, risk-free
  high return, …) checked against the user's own message, FIRST in `ask()`
  before retrieval/LLM (fast, deterministic, no model call). On match, returns
  `fraud_warning: true` + 3 warning bullets; the frontend renders the distinct
  red-bordered/pink `FinGuruBubbleVariant.fraud` styling (built in Phase 3,
  wired up now).
- **Onboarding handoff**: `finguru_engine._detect_product_handoff()` — when a
  cited topic carries a `product_id:<id>` tag (set on the 3 account topics in
  Phase 2), the id is **validated against the real `product_catalog.get_product()`**
  (reusing onboarding's catalog, not duplicating product data) and returned as
  `suggested_action:"start_onboarding"` + `suggested_product_id`. Frontend
  renders the gradient gold→blue "Get Started with SBI" handoff card; tapping it
  calls the **SAME `POST /applications/start`** endpoint the rest of the app
  uses (`ApiClient.startApplication(source:"finguru")`), patches `AppState`,
  and navigates into the real onboarding chat (`/chat`) — not a mock or a
  separate flow.

**Verified (real)**
- curl: a fraud-worded message → `fraud_warning:true` + the 3 bullets, no LLM
  call. A product question → `suggested_action:"start_onboarding"`,
  `suggested_product_id:"savings_account"`, citing `product_savings_account`.
- **UI screenshots**: disclaimer footer visible persistently above the input;
  fraud message → red/pink warning bubble with bullets rendered exactly per the
  wireframe; product question → grounded answer + gradient "Get Started with
  SBI" handoff card rendered per the wireframe. **Tapped the button** → landed
  in the real onboarding chat ("YONO Assistant", progress stepper, "Hi! Let's
  get your account set up…"). Confirmed via `GET /admin/applications` that a
  **real new Application row** (`product_id:savings_account`,
  `status:IN_PROGRESS`) was created — genuine handoff, not a mock.

**Deviations + why**
- The wireframe's fraud card also has "Report User" / "Learn More" action
  buttons; only the warning text + bullets + distinct styling were built (what
  the prompt's structured-response spec asked for) — the report/learn-more
  actions have no backend target defined in this build, so adding buttons for
  them would be non-functional UI. Noted for a possible Phase 9 polish pass.

**Unsure / open:** none new.

---

## Phase 9 — Polish and test-harness coverage

**Built**
- **Consistency check**: re-reviewed every FinGuru screen against
  `design/finguru_wireframes/` — home, chat/citation, glossary popover, gap
  bubble, research-queued, answer-ready banner, researched-answer tag, SIP
  calculator, schemes explorer (+ filter), comparison mode, disclaimer footer,
  fraud warning, onboarding handoff — all already screenshot-verified against
  their wireframe counterparts during Phases 3-8 (evidence in each phase's
  entry above); no drift found needing a fix in this pass.
- **Loading/error/empty states** were built incrementally per-phase rather than
  bolted on at the end — confirmed present: Trending/"what others are asking"
  (loading bar → real cards → "No trending topics yet"/"No questions yet"),
  schemes explorer (spinner → cards → "No schemes found for this filter" →
  error text), glossary sheet (spinner → content → error text), comparison
  screen (spinner → cards → error text), chat (typing indicator, graceful
  "having trouble" soft-fail bubble, network-error catch-all bubble).
- **test-harness**: `/test-harness/` existed only as an empty `fixtures/`
  directory (no scenario files, no RUN_ALL.md to extend or position against —
  see Deviations). Added `finguru_grounded_answer.md`,
  `finguru_gap_filling_loop.md`, `finguru_comparison_mode.md`,
  `finguru_onboarding_handoff.md` (Given/When/Then, each citing the specific
  real verification run that confirmed it) and a new `RUN_ALL.md` indexing them.
- **`docs/ARCHITECTURE.md`**: added a "FinGuru" section — data model, what it
  shares with onboarding (Ollama config, product catalog, HITL queue,
  NotificationLog, STT pipeline, `/applications/start`, `/ws/admin` event bus)
  vs. what's distinct (keyword retrieval, grounding discipline, comparison
  mode, lightweight heuristics).
- Fixed the two remaining `flutter analyze` lints (one pre-existing, one from
  this build's glossary-span loop).

**Verified (real, full walkthrough after all 9 phases)**
- `flutter analyze`: 1 pre-existing benign info-level lint only (guarded
  `use_build_context_synchronously` in `track_status_screen.dart`, unrelated to
  FinGuru). `flutter build web`: clean. Backend: `finguru_engine`/`finguru`
  router/`admin` import cleanly.
- **Fresh end-to-end walkthrough** (new browser session, cleared local state):
  FinGuru home rendered → asked "What is a credit score and why does it
  matter?" → grounded answer with citation + glossary term + 3 follow-ups →
  tapped a follow-up ("How can I improve my credit score?") → new grounded
  answer in the same conversation → tapped the glossary term → topic sheet
  opened with real summary/Learn more/Source. (The gap-filling loop, comparison
  mode, and onboarding handoff were each independently verified end-to-end with
  real screenshots in Phases 4, 7, and 8 respectively, and were not re-run here
  since Phase 9 touched none of their code paths.)

**Deviations + why**
- `test-harness/RUN_ALL.md` didn't exist to extend "in a sensible position" —
  created fresh, containing only the 4 FinGuru scenarios, with a note that
  onboarding-flow scenarios can be added in the same format later.

**Still stubbed / not built (full honest list)**
- **Voice input recording** (mic button) — frontend stub only; the backend
  `POST /finguru/conversations/{id}/voice` endpoint is real (reuses the STT
  pipeline), but there was no existing Flutter mic-recording mechanism to
  reuse (only the React app had one), so nothing calls it yet from the UI.
- **Read-aloud (TTS)** — speaker icon is a stub (snackbar); no FinGuru
  TTS-reply endpoint was built (the onboarding voice endpoint's TTS pattern
  exists in `services/tts.py` and could be wired the same way later).
- **Fraud card's "Report User" / "Learn More" action buttons** — only the
  warning text + bullets + distinct red styling were built; the wireframe's
  two buttons have no defined backend action in this build.
- **Trending window is all-time, not a 7-day rolling window** (Phase 5,
  documented choice).
- **Topic retrieval is keyword/tag-based, not embeddings** — documented in
  `finguru_engine.py` as the natural next upgrade.
- **The ngrok-tunneled Ollama endpoint is intermittently flaky** under rapid
  back-to-back requests (truncated JSON / dropped connections) — every phase's
  demo/verification hit this at least once; the one-retry + graceful
  `confidence:"unavailable"` soft-fail handle it correctly every time it was
  hit, and spaced-out requests succeed reliably. Not a code bug in this build,
  but worth knowing before a live jury demo (pace questions a few seconds
  apart, or re-ask if a "having trouble" bubble appears).
