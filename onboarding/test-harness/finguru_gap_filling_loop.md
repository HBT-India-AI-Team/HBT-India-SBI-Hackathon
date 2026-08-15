# Scenario: FinGuru gap-filling / research queue loop

**Given** a FinGuru conversation exists
**And** the admin dashboard's unified HITL queue is available
(`GET /admin/hitl/queue`)

**When** a user asks a question with NO topic coverage, e.g. "Who won the
football match last night?" (`POST /finguru/conversations/{id}/message`)

**Then** the response has `confidence: "not_covered"` with no citations, and
**no LLM call was made** (retrieval found nothing relevant — verified via
`retrieve_relevant_topics()` returning `[]` deterministically)
**And** the Flutter chat shows the cream gap bubble with "Yes, research this" /
"No thanks" chips

**When** the user taps "Yes, research this"
(`POST /finguru/research-requests` with the question + conversation_id)

**Then** a `ResearchRequest(status="queued")` is created
**And** a `ReviewItem(type="content_research")` appears in the SAME unified
admin queue `GET /admin/hitl/queue` (not a parallel queue)
**And** the Flutter chat shows the "research queued" confirmation bubble

**When** an admin resolves that HITL item with an answer
(`POST /admin/hitl/{item_id}/resolve` with `answer_text` + `source_url`)

**Then** a new `FinGuruTopic` is created from the answer
**And** the `ResearchRequest` is marked `status: "answered"`
**And** a `NotificationLog` entry is created (reusing the same mechanism as
onboarding nudges — `channel: "finguru"`)
**And** a `research_answered` event is emitted on `/ws/admin`
**And** `GET /finguru/conversations/{id}/research-updates` now returns the
answered update

**When** the user reloads the FinGuru home screen

**Then** the peach "FinGuru found an answer to your question about '...'"
banner is shown
**And** tapping it resumes the original conversation and shows the answer with
a gold "💡 Researched for you" tag and a citation pill

## Verified
2026-08-12 (docs/BUILD_LOG.md Phase 4): full loop verified twice — once via
curl end-to-end (research-request → HITL queue → resolve → research-updates →
conversation message → `/admin/notifications`), and once via Playwright UI
screenshots at every step (gap bubble → chips → queued bubble → admin resolve
→ home banner → resumed chat with "Researched for you" tag).
