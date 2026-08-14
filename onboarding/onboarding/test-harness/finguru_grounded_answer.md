# Scenario: FinGuru grounded answer

**Given** the FinGuru knowledge base is seeded
(`python -m backend.scripts.seed_finguru_knowledge` — loads
`backend/data/finguru_knowledge/*.json` into `FinGuruTopic`)
**And** the backend is running with a reachable Ollama endpoint
(`GET /admin/llm/status` → `reachable: true`)

**When** a user starts a FinGuru conversation
(`POST /finguru/conversations/start`)
**And** asks a question with real topic coverage, e.g. "How does the Sukanya
Samriddhi Yojana work?" (`POST /finguru/conversations/{id}/message`)

**Then** the response has `confidence: "grounded"`
**And** `citations` contains at least one `{topic_id, label}` referencing a
real `FinGuruTopic` row that was actually retrieved (not hallucinated —
`finguru_engine.ask()` validates citations against the retrieved set)
**And** `follow_up_questions` has 1-3 entries
**And** in the Flutter chat UI, the cited topic's title renders as a
gold dotted-underline glossary term; tapping it opens a bottom sheet showing
`GET /finguru/topics/{id}`'s summary + a "Learn more" expansion + source link
**And** tapping a follow-up chip sends it as the next question in the same
conversation

## Verified
2026-08-12 (docs/BUILD_LOG.md Phase 2 + Phase 3): curl walk — "How does SSY
work?" returned `confidence:"grounded"`, `citations:[{"topic_id":"scheme_ssy",
"label":"Sukanya Samriddhi Yojana (SSY)"}]`. UI screenshot confirmed via
Playwright: the answer bubble rendered with the SSY term as a tappable
glossary link, a citation pill, and 2 follow-up chips; tapping the glossary
term opened the topic sheet with real summary/Learn more/Source.
