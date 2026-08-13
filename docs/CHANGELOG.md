# CHANGELOG

**Generated from git history — do not edit by hand.**
Run `python scripts/update_changelog.py` after committing.

A hand-maintained changelog drifts out of sync and then misleads. The
commit message is the source of truth; this is a readable view of it.

---

## 2026-08-13 · Anchor FinGuru's Hindi on how a real advisor speaks

`7886712` — 15 files, +1505/−19

Retrieves passages of finance creators explaining money and puts them in the
prompt that writes the answer, so the register comes from recorded speech
rather than from a hand-written word list. 431 passages from 61 videos'
transcripts, embedded with bge-m3.

Style is allowed to change wording and nothing else, which took three
guards. Passages carrying a figure are dropped at build time, so an
unverified number can never sit beside real tool output. The prompt block
says plainly that these are tone and not sources. And they go only on the
call that writes prose -- never the tool loop, which picks tools and writes
English numeric arguments, and which a test pins by reading the source.

Not sentence-transformers and Chroma, which the upstream corpus project
uses: that pulls ~2 GB of torch into a service with no ML dependency at all.
Embedding runs over the same Ollama host as everything else and the index is
plain JSON. bge-m3 rather than the document index's nomic, because these
queries are untranslated Devanagari against a Devanagari corpus -- the
pure-Indic case, where bge-m3 measured 8/10 at rank 1 against nomic's 0/10.

MIN_SCORE is 0.60, set high because measurement found no clean separation:
the on-topic floor is 0.496 and the off-topic ceiling 0.584, a NEGATIVE gap
-- "मेरे फोन की बैटरी जल्दी खत्म हो जाती है" outscores six of ten finance
questions, the corpus being thick with app walkthroughs. So roughly four in
ten questions get no examples at all and are answered exactly as before. A
missing exemplar costs nothing; a mismatched one re-voices a correct answer
toward an unrelated topic.

Measured over 12 questions against the same agent with style off: tools
matched 12/12, no number was corrupted or invented, and answers ran 10%
shorter. Three rows dropped a figure; read individually, one is material --
an emergency-fund paragraph. Two were my own checker: "₹2,00,000" rewritten
as "₹2 लाख" is the same sum said the way a person says it, which is the
point of the layer, and it was being counted as a loss.

The register table in the instructions is now evidence rather than taste.
Counted over 1,518 scraped passages, products keep their English names
(लोन 156 vs ऋण 1) but concepts stay Hindi (ब्याज 45 vs इंटरेस्ट 1, निवेश 21
vs इन्वेस्ट 7). Three rows I had written from intuition pointed the wrong
way: reaching for the English word to sound casual overshoots.

Deleting capabilities_impl/fixtures/style_index.json turns all of this off.

## 2026-08-13 · Add FinGuru, a grounded India personal-finance agent

`4620edb` — 135 files, +11606/−1423

Every figure FinGuru states comes from a tool call, and each one carries its
source and the bank's published effective date through into the answer. The
rate fixture was verified against sbi.bank.in and rbi.org.in rather than
recalled -- an earlier draft was wrong on every deposit and lending rate.

Two date fields, not one: `effective_from` is the w.e.f. date the bank
published and is what the user is told; `as_of` is when a human last checked
the page and is what staleness is measured from. Conflating them told users
"as of 1 June" about a rate unchanged since 15 December.

Retrieval covers 528 chunks across 43 SBI and RBI sources, with a relevance
floor measured against the corpus (0.58) rather than guessed. Indic-script
queries are refused with an instruction to retry in English, since the index
is English -- translate-then-retrieve beat a second embedding model, which
fixed Indic but destroyed romanized Hinglish.

Hindi specifically:
  - Scheme names come from the tool. Left to recall the model invented two
    different non-existent Hindi names for PPF and misspelled सुकन्या.
  - Senior-citizen age rides on every FD result. A Hindi answer put a
    62-year-old in a "super senior" band SBI does not have, while the
    English answer to the same question was correct.
  - The register is the Hindi people speak -- सेविंग्स अकाउंट, not बचत खाता.
    Textbook Hindi reads like a circular to the people this is for.

Ollama's `think` is set per call, not per adapter: tool selection needs
thinking on, structured answer generation needs it off. One shared setting
either drops every tool call or returns 3,457 characters of reasoning and 81
of answer.

Chat replies render as markdown instead of literal asterisks, and the
composer grows with its content and stops at six lines.

## 2026-08-09 · Fix stray WebSocket crash on static mount; raise builder timeout to match wrapper

`cde984c` — 2 files, +25/−3

- backend/main.py: the catch-all StaticFiles mount at "/" crashed with an
  uncaught AssertionError whenever a stray WebSocket handshake hit it
  (StaticFiles only handles http scope). Wrap it so non-http scopes close
  cleanly instead of taking down the ASGI connection with a traceback.

- backend/agent_builder.py: the wrapper's own read timeout for /ollama was
  raised from 30s to 300s (was cutting off legitimate large structured-
  generation calls early). Our OllamaAdapter's builder timeout was still
  150s, so it would now time out first and mask whatever the wrapper/Ollama
  actually did. Raised to 320s to exceed theirs.

## 2026-08-07 · Add a Logs page showing every Ollama call, in the admin UI

`2be73c5` — 8 files, +214/−6

New "Logs" nav item lists every recent call from logs/ollama_calls.jsonl
(most recent first), each expandable to the full request/response or
error -- so diagnosing an LLM failure no longer requires digging through
the raw JSONL file by hand.

Also fixes a test-pollution bug the new call-logging surfaced: tests
exercising OllamaAdapter._post_chat were writing fake test data into the
real logs/ollama_calls.jsonl. Fixed with a global autouse fixture in
conftest.py, same fix pattern already used once for agent_api_keys.json.

## 2026-08-07 · Log every Ollama call attempt to logs/ollama_calls.jsonl

`64f399f` — 2 files, +147/−1

One JSONL line per HTTP attempt (including failed retries, not just the
final outcome) -- full request (messages, schema, options) and full
response (or error), through OllamaAdapter._post_chat, the one chokepoint
every caller in the platform goes through. Needed for diagnosing the
intermittent 503s from the shared Ollama server without depending on
server-side log access.

## 2026-08-07 · Stop refine_agent from wiping hand-edited instructions/output_contract

`442517f` — 2 files, +39/−2

render_skill_files always regenerates skill.yaml/instructions.md/
output_contract.json from a fixed generic template, regardless of what's
actually in the corrected spec -- refine_agent was rewriting all of it on
every correction, silently discarding any manual edit made in the Files
tab. Now only the 4 rule files (the only ones actually derived from the
correction) get rewritten.

## 2026-08-07 · Make "Fix with AI" correct every rule-bearing skill, not just the first

`9550183` — 5 files, +108/−14

refine_agent only ever touched bundle.definition.skills[0] -- on a
multi-skill agent (the common case for a decomposed description), any
skill after the first was unreachable no matter what feedback was given.
Now applies the same feedback to every rule-bearing skill independently
(one refine_spec call each, mirroring generate_agent_skills' existing
per-skill pattern), reports per-skill success/failure, and only fails the
whole request if every skill's correction was invalid.

## 2026-08-07 · Add per-agent input mode (chat/form/json/trigger) to Playground

`9866fe6` — 8 files, +127/−22

Different agents genuinely need different input shapes: evidence-driven
agents work well as chat, flat-field search agents fit a form, agents with
nested object/array input (proposal) need raw JSON, and lookup-only agents
just need a trigger. agent.yaml now declares input_mode (default "chat");
Playground shows the matching interface and lets it be changed inline via
a new POST /admin/agents/{id}/input-mode endpoint instead of requiring a
hand-edit of agent.yaml.

## 2026-08-07 · Add a one-click "Accept draft" button

`f4c159e` — 1 file, +37/−5

Flips draft/routable in agent.yaml and saves, instead of requiring someone
to hand-edit two YAML lines to get a generated agent out of draft status.

## 2026-08-07 · Add a chat interface (internal + embeddable) and redesign the admin UI

`577b495` — 18 files, +986/−154

Free-text conversation now maps to an agent's real rule fields (extracted
via LLM against each skill's gates/factors, since input_schema alone never
carries real field names) with session persistence so multi-turn context
survives a server restart. Same engine powers the Playground's new
chat-first mode and a standalone GET /embed/{agent_id} page a client site
can iframe directly — no CORS needed since it's same-origin. Raw JSON/form
testing stays available under an "Advanced" toggle.

Also a visual pass on the admin UI (Dashboard, AgentEditor, Sidebar, ui.tsx
primitives) for more breathing room and a consistent type/spacing system.

## 2026-08-07 · Open up for LAN demo access

`eb49981` — 2 files, +6/−7

CORS wide open on the public API (invoke needs to be callable from a
second device's browser JS), and run_backend.ps1 binds to 0.0.0.0 so
the server is reachable over WiFi, not just localhost.

## 2026-08-06 · Add per-agent API keys for the public invoke endpoint

`661afd4` — 8 files, +271/−7

Every agent now gets a key on creation; POST /agents/{id}/invoke requires
it via X-API-Key. A new "Integrate" tab in the editor shows the key plus
a copy-pasteable fetch() snippet for embedding the agent elsewhere.

## 2026-08-06 · Add Skill-Driven Agent Runtime platform

`9e75235` — 190 files, +15503/−0

Backend (FastAPI + agent_platform runtime/pipeline), the admin/editor
frontend (React), demo agents and skill packages, and tests.

## 2026-08-06 · Initial commit

`ec6ae1b` — 1 file, +2/−0
