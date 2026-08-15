# YONO 3.0 — FinGuru Build Prompts (Frontend + Backend)

Purpose: adds FinGuru — the India-context financial Q&A chat assistant — to
the existing YONO 3.0 app. Builds on top of the main app (Flutter Web +
FastAPI backend, Requirement Graph, Ollama config) and the admin dashboard
(shares its HITL queue and WebSocket feed). Run phases in order.

Before starting: place the downloaded Stitch wireframe images in
/design/finguru_wireframes/ in the repo (unzip if needed). Every phase below
that touches UI instructs Claude Code to look at the actual wireframe images
first — match their exact layout, spacing, and color choices rather than
reinterpreting from a text description.

---

## Phase 1 — Setup: wireframe reference, data model additions, entry point

```
Set up the foundation for FinGuru.

1. Unzip/locate /design/finguru_wireframes/ in the repo. View every image
   in that folder now, and write a short /design/finguru_wireframes/NOTES.md
   summarizing: the exact color values used (base SBI blue + the FinGuru
   gold/amber accent — sample actual hex values from the images rather than
   guessing), the mascot/icon style, spacing patterns, and a list of which
   screen each image corresponds to (home, chat/answer, glossary popup,
   gap-filling states, schemes explorer, calculator, comparison mode, trust/
   safety, handoff). Reference this NOTES.md file explicitly in every later
   phase's UI work in this build so styling stays consistent with the actual
   wireframes, not a re-guessed version of them.

2. Backend data model additions (extend the existing SQLAlchemy models,
   don't create a parallel persistence layer):
   - FinGuruTopic: id, category (fin_wiki/product/govt_scheme), title,
     tags (list), summary, body, source_url, last_verified_at
   - FinGuruConversation: id, user_id (FK, nullable — FinGuru can be used
     before/without onboarding), started_at, last_active_at
   - FinGuruMessage: id, conversation_id (FK), direction, content_type
     (text/audio), content_payload (JSON), citations (JSON, nullable),
     follow_up_suggestions (JSON, nullable), timestamp
   - ResearchRequest: id, conversation_id (FK), user_id (FK, nullable),
     question_text, status (queued/researching/answered), created_at,
     answered_at, review_item_id (FK, nullable — links to the existing
     ReviewItem/HITL model)

3. Add a FinGuru entry point: a new tile "Ask FinGuru" on the home page tile
   strip (gold-accented icon, per the wireframes), routing to a new /finguru
   route in the Flutter app. Add the route + a placeholder page so
   navigation can be verified before building the real screens.

4. Add /backend/routers/finguru.py as a new router file (keep this cleanly
   separated from the onboarding/applications routers — FinGuru is a
   distinct feature sharing infrastructure, not a sub-feature of
   onboarding).

Show me NOTES.md and confirm the app builds with the new route.
```

---

## Phase 2 — Backend: knowledge base + grounded LLM chat engine

```
Build the core FinGuru answer engine — grounded, cited, India-context
answers using the existing Ollama config.

1. Seed /backend/data/finguru_knowledge/ with FinGuruTopic entries covering
   at least: 5-8 Fin Wiki concepts (e.g. SIP, NAV, TDS, KYC, credit score),
   3-5 SBI-relevant product summaries (reuse/reference the same
   product_requirements.json data where it overlaps rather than duplicating
   it), and 5-8 government schemes (e.g. PMJDY, Sukanya Samriddhi, PMAY,
   Atal Pension Yojana, PM-KISAN if relevant). Use web search to source
   accurate, current information for these, same approach as the earlier
   product_requirements.json research — each entry gets a source_url and is
   marked with the same "populated via web research, needs stakeholder
   review" disclaimer pattern used before. Load these into the
   FinGuruTopic table via a seed script /backend/scripts/seed_finguru_knowledge.py.

2. Build /backend/services/finguru_engine.py:
   - retrieve_relevant_topics(query) -> a simple retrieval function
     (keyword/tag matching is fine for the hackathon — note in a comment
     that a real embeddings-based retrieval would replace this later) that
     returns the top few FinGuruTopic entries relevant to a question
   - ask(conversation, question_text) -> constructs a prompt to the
     existing Ollama config (reuse OLLAMA_BASE_URL/OLLAMA_MODEL from
     /backend/config.py, don't create a second config) that includes the
     retrieved topics as grounding context, the recent conversation
     history, and instructs the model to answer ONLY using the provided
     context where possible, to cite which topic(s) it drew from, and to
     propose 2-3 relevant follow-up questions
   - Require structured JSON output: { answer_text: str,
     citations: [{ topic_id, label }], follow_up_questions: [str],
     confidence: "grounded" | "partial" | "not_covered" }
   - If confidence is "not_covered" (the retrieval found nothing relevant
     and the model can't answer from context), do NOT let the model answer
     from its own general knowledge — instead return a response that
     triggers the gap-filling flow (Phase 4), since answering ungrounded
     here would undermine the accuracy/coverage differentiator this
     feature is built around
   - Apply the SAME fallback principle as the onboarding LLM: if Ollama is
     unreachable/times out/returns invalid JSON, return a graceful
     "I'm having trouble right now, try again in a moment" response rather
     than crashing — there's no meaningful rule-based fallback for open Q&A,
     so this is a soft-fail, not a full fallback engine

3. Endpoints in /backend/routers/finguru.py:
   - POST /finguru/conversations/start — creates a FinGuruConversation
   - POST /finguru/conversations/{id}/message — runs ask(), persists both
     the inbound and outbound FinGuruMessage rows (with citations and
     follow_up_suggestions stored), returns the structured response
   - GET /finguru/conversations/{id} — full message history (for resume)
   - GET /finguru/topics/{id} — fetch a single topic (for the glossary
     tap-to-define popup)

4. Write a demo script /backend/scripts/demo_finguru.sh testing: a question
   with good topic coverage (should return grounded answer + citations), a
   question with no coverage (should trigger the not_covered path), and a
   follow-up question in the same conversation.

Run the script and show me the output for all three cases.
```

---

## Phase 3 — Frontend: FinGuru home + chat, matching the wireframes

```
Build the /finguru page and its chat experience. Look at the actual images
in /design/finguru_wireframes/ (and NOTES.md from Phase 1) before building
each screen below — match colors, spacing, and layout to what's in the
wireframes rather than the text description here, which is a functional
summary only.

1. FinGuru home screen: mascot/wordmark header, prominent "Ask me anything
   about money..." search/ask bar with mic icon, trending topics row
   (wire this to a stub/hardcoded list for now — real trending data comes
   in Phase 5), three category tiles (Fin Wiki / Products / Govt Schemes),
   a "What others are asking" list section (stub content for now).

2. FinGuru chat screen: reuse chat-bubble conventions from the onboarding
   chat where sensible (left/right alignment pattern) but styled per the
   FinGuru wireframes (mascot icon beside bot bubbles, gold accent
   elements). On sending a question, call
   POST /finguru/conversations/{id}/message and render:
   - The answer bubble
   - A citation pill row below it if citations are present ("Per RBI
     guidelines" style tag + a last-verified label if the source topic has
     one)
   - Follow-up question chips (visually distinct outlined-gold style vs
     onboarding's filled quick-reply chips) — tapping one sends it as the
     next question
   - A small read-aloud speaker icon next to each answer bubble (wire this
     to a TTS call if one exists in the backend already; if not, stub the
     button for now and note it as a TODO rather than building TTS from
     scratch in this phase)

3. Tap-to-define glossary: any term in an answer that matches a
   FinGuruTopic title (simple string-match against the topics returned in
   that message's citations, or a basic keyword scan) should be tappable,
   opening a small popover calling GET /finguru/topics/{id} and showing its
   summary + a "Learn more" link that opens the full topic.

4. Voice input: reuse the existing voice-recording mechanism built for
   onboarding voice messages (same MediaRecorder-based approach) — don't
   rebuild it — wire the mic icon on the FinGuru ask bar and chat input to
   upload audio and get a transcript back (reuse the existing /sessions/
   {id}/voice STT pipeline's transcribe() function via a new
   POST /finguru/conversations/{id}/voice endpoint that transcribes then
   calls the same ask() logic as a text message).

Test the full loop: ask a grounded question, see the cited answer and
follow-up chips, tap a follow-up, tap a glossary term, and confirm voice
input produces a sensible transcript and answer. Show me this working.
```

---

## Phase 4 — Gap-filling / research queue loop

```
Build the "we don't know this yet, want us to look into it" flow end to
end, connecting to the existing admin HITL infrastructure.

1. Backend: when finguru_engine.ask() returns confidence="not_covered",
   the /finguru/conversations/{id}/message endpoint should NOT create a
   ResearchRequest automatically — instead return a response prompting the
   user to opt in (matching the wireframe's "Yes, research this / No
   thanks" chips). Add POST /finguru/research-requests — creates a
   ResearchRequest(status="queued"), and ALSO creates a
   ReviewItem(type="content_research", reason=question_text) so it appears
   in the SAME unified admin HITL queue as kyc_review and support_request
   items (reuse that existing endpoint/table, don't build a parallel one).

2. Admin side: extend GET /admin/hitl/queue and POST /admin/hitl/{id}/resolve
   (from the admin dashboard's existing backend) to handle
   type="content_research" — resolving one should accept an answer_text +
   optional source_url, create a new FinGuruTopic from it (or update an
   existing one), mark the linked ResearchRequest as "answered", and
   broadcast a research_answered event over the existing /ws/admin feed.
   NOTE: this touches the admin dashboard's backend code, not just
   FinGuru's — make sure you're editing the actual admin router file, not
   creating a duplicate.

3. Notification: reuse the EXISTING NotificationLog mechanism from
   onboarding (don't build a second notification system) — when a
   ResearchRequest is answered, create a NotificationLog entry so it can
   surface the same way an onboarding nudge would.

4. Frontend: build the three wireframe states — the "not yet covered"
   bubble with Yes/No chips, the "research queued" confirmation bubble,
   and the "your answer is ready" notification banner (matching the
   onboarding resume banner's visual pattern but gold-accented, per the
   wireframes) shown on the FinGuru home screen when an answered
   ResearchRequest exists for this user. Tapping it should open the
   FinGuru chat scrolled to the new answer, with a small "Researched for
   you" tag on that bubble.

Test: ask a question with no topic coverage, opt into research, then — as
if you were an admin — resolve that content_research HITL item with an
answer via the admin endpoint, and confirm the notification banner and
answered-bubble appear correctly on the FinGuru side. Show me this full
loop.
```

---

## Phase 5 — Trending topics and discovery

```
Replace the Phase 3 stub trending/discovery content with real data.

1. Backend: add a lightweight query-count mechanism — increment a counter
   on the FinGuruTopic (or a separate FinGuruTopicStat table) each time a
   topic is cited in an answer. Add GET /finguru/trending — returns the
   top N topics by recent query count (e.g. last 7 days, or all-time if you
   don't want to add time-windowing complexity for the hackathon — your
   call, note which you chose).

2. Add GET /finguru/recent-questions — a simple recent-questions feed
   (anonymized: question text only, no user identifiers) for the "What
   others are asking" section, pulled from recent FinGuruMessage rows
   where direction=inbound.

3. Wire the FinGuru home screen's trending row and "What others are
   asking" section to these two endpoints instead of the Phase 3 stub data.

Test by asking a few different questions to generate real data, then
reload the FinGuru home screen and confirm trending topics and recent
questions reflect what was actually asked. Show me this.
```

---

## Phase 6 — Government schemes explorer + calculators

```
Build the two structured-content features from the wireframes.

1. Government Schemes Explorer: GET /finguru/schemes (filterable by
   category query param — savings/insurance/pension/housing) returning
   FinGuruTopic entries where category="govt_scheme", shaped for the
   list-card UI (title, one-line summary, eligibility tags — add an
   `eligibility_tags` JSON field to FinGuruTopic if it doesn't already
   fit the existing schema). Build the /finguru/schemes screen per the
   wireframes: filter chip row, scrollable card list, expandable detail.

2. Calculators: build at least a SIP calculator as an embeddable chat card
   component (per the wireframe) — this can be pure frontend logic (no
   backend call needed, it's simple math: future value of a SIP given
   monthly amount, rate, years). Trigger it when a user's question matches
   a calculator-worthy intent (simple keyword detection is fine — e.g.
   "SIP calculator" or "how much will my SIP grow" in the question text) by
   having finguru_engine.ask() include a `suggested_widget: "sip_calculator"
   | null` field in its structured response, and have the frontend render
   the calculator card instead of/alongside the text answer when present.

Test: ask a scheme-related question and browse the schemes explorer, then
ask something that should trigger the SIP calculator and confirm it renders
and computes correctly. Show me this.
```

---

## Phase 7 — Live comparison mode (FinGuru vs generic AI)

```
Build the side-by-side comparison feature — this is the strongest demo
proof point for the "more accurate/India-specific than generic frontier
models" claim, so make sure it's genuinely showing two different answers,
not two calls to the same grounded path.

1. Backend: add POST /finguru/compare — takes a question, and returns TWO
   answers: (a) the normal grounded finguru_engine.ask() result (with
   citations), and (b) a second Ollama call using the SAME model but WITH
   NO retrieved-topic grounding context and no instruction to cite sources
   — just a plain "answer this financial question" prompt, representing
   what a generic ungrounded assistant would say. Label these clearly in
   the response as `finguru_answer` and `generic_answer`.

2. Frontend: build the split-card comparison view per the wireframes (gold
   header "FinGuru" side with citation badge, neutral gray header "Generic
   AI" side), triggered by a toggle/button in the chat UI ("Compare with
   generic AI" or similar) rather than being the default experience for
   every question — this should feel like an intentional demo/trust
   feature, not clutter every answer.

Test with a question where grounding clearly matters (e.g. a specific govt
scheme detail) and show me both sides of the comparison rendering
correctly.
```

---

## Phase 8 — Trust, safety, and onboarding handoff

```
Add the remaining trust/safety and integration screens from the wireframes.

1. Persistent disclaimer footer on the FinGuru chat screen (per the
   wireframes): small "educational information, not personalized
   investment advice" bar with a "Learn more" link opening a short static
   info screen.

2. Fraud/scam awareness: add simple keyword-based detection in
   finguru_engine.ask() (or a pre-check before calling it) for phrases
   commonly associated with scam patterns (e.g. "guaranteed returns",
   "double your money") — when matched, include a fraud_warning: true flag
   and short warning bullet points in the structured response, rendered as
   the distinct warning-styled bubble from the wireframes. Keep the
   keyword list simple and documented, not a heavy ML classifier — this is
   a lightweight awareness feature, not a fraud-detection system.

3. Handoff to onboarding: when a FinGuru answer relates to a specific SBI
   product (match against the product catalog from the onboarding backend
   — reuse product_catalog.py's lookup, don't duplicate product data),
   include a suggested_action: "start_onboarding" with the relevant
   product_id in the structured response. Frontend renders the handoff
   card from the wireframes ("Ready to start a SIP? → Get Started with
   SBI") — tapping it should call the SAME POST /applications/start
   endpoint the game-embed handoff uses, with source="finguru" and the
   relevant product_id, then navigate to the onboarding chat.

Test: trigger the fraud warning with a scam-pattern question, and trigger
the onboarding handoff with a product-related question, confirming it
correctly starts a real Application via the existing endpoint. Show me
both.
```

---

## Phase 9 — Polish and test-harness coverage

```
Final polish pass and test coverage for FinGuru.

1. Consistency check against /design/finguru_wireframes/ — go back through
   every screen built in Phases 3-8 and confirm it actually matches the
   wireframe images (colors, spacing, iconography), not just the earlier
   functional descriptions. Fix any drift.

2. Add loading/error/empty states throughout (e.g. no trending topics yet,
   Ollama unreachable during a FinGuru question, empty schemes list for a
   filter with no matches).

3. If /test-harness exists from earlier work, add FinGuru scenarios
   following the same Given/When/Then format as the existing scenarios:
   finguru_grounded_answer.md, finguru_gap_filling_loop.md,
   finguru_comparison_mode.md, finguru_onboarding_handoff.md. Add these to
   /test-harness/RUN_ALL.md in a sensible position.

4. Update /docs/ARCHITECTURE.md with a new section describing FinGuru:
   its data model, how it shares infrastructure with onboarding (Ollama
   config, product catalog, HITL queue, notification log) vs what's
   distinct to it (FinGuruConversation/Message, topic retrieval).

Do a full walkthrough: home page -> Ask FinGuru tile -> ask a grounded
question -> tap a follow-up -> tap a glossary term -> ask an uncovered
question -> opt into research -> (as admin) resolve it -> see the
notification -> try the comparison mode -> trigger a product handoff into
onboarding. Confirm every step works and tell me what's still broken or
stubbed.
```

---

## Notes on sequencing

- Phase 1 must come first — later phases assume the wireframe NOTES.md and
  data model exist.
- Phase 2 (backend engine) before Phase 3 (frontend) — the frontend phase
  calls real endpoints from the start rather than working against stubs.
- Phase 4 depends on the admin dashboard's HITL queue already existing from
  the earlier admin dashboard build — if that project hasn't been built yet
  in this environment, flag it rather than inventing a standalone queue.
- Phases 5-8 are independent of each other once Phases 1-4 exist — reorder
  based on which differentiator you want to demo-strengthen first (the
  comparison mode in Phase 7 is likely your strongest single jury moment,
  worth prioritizing if time is tight).
- FinGuru deliberately REUSES existing infrastructure throughout (Ollama
  config, product catalog, HITL queue, NotificationLog, voice/STT pipeline,
  Application creation endpoint) rather than building parallel systems —
  if Claude Code finds itself about to duplicate any of these, it should
  stop and reuse the existing one instead.
