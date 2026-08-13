# FLOW — how a request moves through FinGuru

Written to answer one question fast: **something is wrong in the reply — which
stage produced it?** Read the symptom table at the bottom first if you are
debugging; the rest is the map it refers to.

---

## The whole path

```
POST /agents/finguru/chat  {session_id, message}
  backend/main.py:117  → checks X-API-Key
        │
        ▼
  agent_platform/runtime/chat.py :: handle_chat_turn
    - loads or creates the session (chat_sessions/)
    - extracts evidence from the message
    - calls invoke_agent(agent_id, {"evidence": {...}})   ← NOTE THE WRAPPING
        │
        ▼
  agent_platform/runtime/pipeline.py — runs the stages named in agent.yaml
        │
        ├─ load_input          normalises raw_input
        ├─ gather_evidence     pulls anything the skill declares
        ├─ reason_llm_with_tools     ← everything interesting happens here
        ├─ validate_output     checks the reply against output_contract.json
        ├─ hitl_gate           decides whether a human must review
        └─ explain             writes the decision record
        │
        ▼
  runs/run_<id>.json  (stage timings)      logs/ollama_calls.jsonl  (full trace)
```

**The wrapping on line 3 matters.** `/invoke` passes fields flat; the chat route
nests them under `evidence`. Code that reads `raw_input["message"]` works in
tests and returns nothing in production. This has already caused one silent
failure — see `_user_message` in `pipeline_stages.py`.

---

## Inside `reason_llm_with_tools`

`agent_platform/stages/pipeline_stages.py:769`. Two model calls, and they are
configured **oppositely**.

```
  system_prompt = skill.instructions_text          (skills_library/finguru/instructions.md)
  user_prompt   = "message: <what the user typed>"

┌── CALL 1 — run_tool_loop ──────────────────── think ON ──┐
│  Model sees _TOOL_SCHEMAS and picks tools.               │
│  Arguments are ALWAYS English and numeric.               │
│  Up to 3 turns. Each call resolves through               │
│  DEFAULT_REGISTRY → capabilities_impl/.                  │
│                                                          │
│  ⛔ style examples are NOT added here                    │
└──────────────────────────────────────────────────────────┘
        │  tool results appended to user_prompt verbatim:
        │  "Real tool results already gathered — use these exact values"
        ▼
┌── CALL 2 — generate_structured ────────────── think OFF ─┐
│  system = instructions.md + _style_section(ctx)          │
│  Writes the final prose against output_contract.json.    │
└──────────────────────────────────────────────────────────┘
```

**Why the two differ.** Measured on qwen3.6:35b: with thinking OFF the tool loop
made *no* tool calls at all; with thinking ON the final call produced 3,457
characters of reasoning and 81 characters of answer. One shared setting breaks
one of the two. `think` is therefore a per-call argument in
`agent_platform/llm/ollama_adapter.py`, deliberately not an adapter field.

---

## Where facts come from

Four sources, split by how often the underlying truth changes.

```
india.get_fd_rate            ┐
india.get_savings_rate       │
india.get_policy_rate        ├─► capabilities_impl/fixtures/india_reference_rates.json
india.get_loan_rate          │      hand-verified against sbi.bank.in / rbi.org.in
india.get_scheme_details     │      every entry carries effective_from + as_of
india.get_tax_saving_limits  ┘

money.fd_maturity            ──► capabilities_impl/money_math.py   (Python, never the model)
fx.get_rate                  ──► live ECB via Frankfurter

docs.search                  ──► capabilities_impl/doc_search.py
                                   nomic-embed-text  →  fixtures/doc_index.json
                                   528 chunks / 43 sources, MIN_SCORE 0.58
```

**`effective_from` vs `as_of`** — the first is the bank's published w.e.f. date
and is what the user is told; the second is when a human last checked the page
and is what staleness is measured from. Quoting `as_of` to a user states a date
the bank never published.

---

## The two retrieval paths

They look alike and do opposite jobs. Confusing them is the likeliest way to
break this.

| | `docs.search` | `style_examples` |
|---|---|---|
| answers | **what is true** | **how to say it** |
| model | nomic-embed-text | **bge-m3** |
| index | `doc_index.json` | `style_index.json` + `register/*.md` |
| query | translated to English first | user's raw Devanagari or Tamil |
| floor | 0.58 | 0.60 |
| output | quoted and cited | never quoted, never cited |
| is a tool? | yes — model chooses it | **no** — injected by the pipeline |

### docs.search — Indic queries are refused, not embedded

`doc_search.py:228`. A Devanagari query returns
`available: false` with an instruction to translate and retry. This is
deliberate: a raw Hindi query scores ~0.54 against the passage its English
translation matches at 0.72 — close enough to the floor to *sometimes* work,
which is worse than never working, because the failure is invisible.

### style_examples — never chosen, only injected

`pipeline_stages.py :: _style_section` → `capabilities_impl/style_examples.py`.
Not registered as a tool, because "write like this" is an instruction, not a
fact the model should be able to look up.

Two independent halves, either of which can be empty:

```
  language_of(message)        script → "hi" | "ta" | None
        ├── register_guide()  fixtures/register/<lang>.md   fires whenever the
        │                                                   script is known
        └── for_query()       style_index.json              fires only ≥ 0.60
```

The guide is the half that works without a corpus — Tamil has none, and Hindi's
reaches only 5 of 12 real questions. Deleting `fixtures/style_index.json` turns
off retrieval; deleting a `register/*.md` turns off that language's guide.

**Both are cached in module memory, so editing either needs a backend restart.**

Every failure path produces empty text and the prompt goes out exactly as it
would have — which is why `_style_section` also returns a detail dict saying
which of the several silent nothings actually happened. The Playground renders
it under each reply.

### Turning style off

`style: false` in the chat body. Defaults on; `/invoke`, the embed page and the
public API send nothing and keep the shipped behaviour.

It is listed in `_TEXT_ROUTING_KEYS`, and **anything added to `raw_input` must
be** — `_build_text_prompt` renders every other key into the user prompt. When
`style` was missing from that set the model read `style: True` as though the
user had typed it, and tool selection changed.

---

## Building the indexes

Neither is built at runtime. Both are committed files, cached in module memory
on first use — **so any rebuild needs a backend restart.**

```
scripts/build_doc_index.py     43 web sources    → doc_index.json
scripts/build_style_index.py   <lang>.jsonl      → style_index.json
      ├─ drops passages carrying a figure        (style must not carry facts)
      ├─ drops app screen-narration              (2+ UI markers)
      ├─ drops presenter boilerplate             ("नमस्कार, मैं हूं आपके साथ…")
      ├─ drops solicitations                     (loan touts, app pitches)
      └─ drops romanized                         (index is Devanagari-keyed)
```

Both write the index **only at the end**, so a mid-build crash leaves the live
index intact. Both cache embeddings by `sha256(model + text)`, so a re-run after
a dropped tunnel resumes instead of restarting.

---

## Frontend

```
frontend/src/components/Playground.tsx
  └─ ChatWindow.tsx      composer, Enter-to-send with IME guard, stop button
       └─ ContentRenderer.tsx
            └─ Markdown.tsx     builds React nodes — no innerHTML anywhere
```

Vite proxies `/agents`, `/admin`, `/runs`, `/healthz` to `127.0.0.1:8080`. The
backend binds localhost only, so the ngrok tunnel reaches it *through* Vite.

---

## Symptom → where to look

| symptom | almost always |
|---|---|
| **Answer is confident and 3–5s** | tools were skipped. Check `logs/ollama_calls.jsonl` for `tool_calls`. Grounded replies take 7–25s |
| **A number is wrong** | the fixture, not the model. Check `india_reference_rates.json` against the source URL in the tool result |
| **A date is wrong** | `as_of` quoted where `effective_from` was meant |
| **"We don't cover that" for something we do** | `docs.search` floor, or the query described a circumstance rather than asking for the fact |
| **Hindi answer with no citations** | `docs.search` refused the Devanagari query and the model didn't retry in English |
| **Reply is empty** | `think` got switched on for `generate_structured` |
| **No tool calls at all** | `think` got switched off for `run_tool_loop` |
| **Style changed nothing** | read the badge under the reply — it says which: toggled off, script with no corpus, or below 0.60. All are silent in the answer itself |
| **A flag changed the model's behaviour in a way it shouldn't** | it's in `raw_input` but not `_TEXT_ROUTING_KEYS`, so the model is being shown it |
| **Style dropped a fact** | known: the corpus is speech. Measured at 2/12 |
| **Edited instructions did nothing** | backend caches the skill; it has no `--reload`. Restart it |
| **Rebuilt an index and nothing changed** | same — index is cached in module memory |
| **Frontend change didn't appear** | Vite's watcher has been unreliable here. Hard-refresh |

---

## The two silent failures that have already happened

Both built cleanly, passed tests, and did nothing. Neither raised an error.

1. **Language key mismatch.** The corpus file is `hi_transcript.jsonl` but its
   records are tagged `language: hi`. Keying the index off the *filename*
   produced 454 passages that `language_of()` could never match.

2. **Message path.** `_style_section` read `raw_input["message"]` while the chat
   route nests it under `evidence`. Style silently never fired.

The lesson for anything added here: **an integration that can no-op must be
tested by observing an effect, not by checking it was wired up.**
`scripts/compare_style.py` exists for exactly this.
