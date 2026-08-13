# DECISIONS — why the code is like this

Only the calls that look wrong until you know why, and that someone would
reasonably "fix" back. Routine choices are not here; the code comments carry
those.

Each entry: what was decided, what it cost, and **what evidence would overturn
it** — because a decision you cannot argue against is a habit, not a decision.

---

## 1. `think` is a per-call argument, not an adapter setting

**Decided:** `OllamaAdapter._post_chat` takes `think` per call. `run_tool_loop`
never passes it; `generate_structured` passes `think=self.think` (false).

**Why:** measured on qwen3.6:35b under FinGuru's prompt —

| | tool loop | final answer |
|---|---|---|
| thinking ON | tools called at every prompt length | 3,457 chars of reasoning, 81 of answer |
| thinking OFF | **no tool calls at 12k or 21k** | a full answer |

The two calls need opposite settings. One shared field breaks one of them, and
it breaks it *silently* — the tool-off case looks like a model that just decided
not to use tools.

**Cost:** the adapter has a parameter that looks redundant.

**Would overturn it:** a model where both calls behave the same. Re-measure on
any model change; this is model-specific, not a general truth.

---

## 2. Two embedding models, two indexes

**Decided:** `docs.search` uses nomic-embed-text; `style_examples` uses bge-m3.

**Why:** measured across 17 languages on this stack —

| | Tamil / Indic | romanized Hinglish |
|---|---|---|
| nomic | 0.470 | **0.618** |
| bge-m3 | **0.639** | 0.438 |

Neither wins everywhere. Document queries arrive *translated into English*, so
nomic is correct there. Style queries are untranslated Devanagari against a
Devanagari corpus — the pure-Indic case, where bge-m3 scored 8/10 at rank 1
against nomic's 0/10.

**Cost:** two models must exist on the Ollama host.

**Would overturn it:** one model that beats both on Indic *and* romanized.
Switching docs.search to bge-m3 alone would break Hinglish, which is a large
share of real Indian usage.

---

## 3. No torch, no Chroma, no sentence-transformers

**Decided:** everything embeds over HTTP to Ollama; indexes are plain JSON;
cosine similarity is a Python loop.

**Why:** the upstream corpus project uses sentence-transformers + Chroma, which
pulls ~2 GB of torch. This service has **no ML dependency at all** — it is
FastAPI talking to Ollama. Keeping that means one runtime to keep alive during a
demo instead of two, and a `pip install` that finishes.

**Cost:** we reimplemented retrieval that a library provides. ~150 lines.

**Would overturn it:** a corpus large enough that a linear scan hurts. At 528
and 380 vectors it is not close.

---

## 4. Indic queries to `docs.search` are refused, not embedded

**Decided:** a Devanagari query returns `available: false` with an instruction to
translate and retry.

**Why:** a raw Hindi query scores ~0.54 against the passage its English
translation matches at 0.72. That is close enough to the 0.58 floor to *sometimes*
return the right answer and sometimes return nothing — the worst case, because
it is invisible. A refusal is a fact the model can act on.

**Cost:** a translate step, and reliance on the model to actually retry.

**Would overturn it:** a bilingual document index. Then the query would need no
translation at all.

---

## 5. Style examples are not a tool

**Decided:** `style_examples` is never registered in `_TOOL_SCHEMAS`. The
pipeline injects passages into call 2.

**Why:** "write like this" is an instruction, not a fact. A model that can
*choose* to fetch style can also choose to skip it, or call it for the wrong
reason, and its results would arrive in the tool-result block next to verified
figures — where unverified sentences must never sit.

**Cost:** it cannot be inspected through the normal tool trace.

**Would overturn it:** nothing obvious. This one is about safety, not
convenience.

---

## 6. `MIN_SCORE` for style is 0.60 — set high, not at the midpoint

**Decided:** 0.60, which serves roughly six questions in ten.

**Why:** measured, there is **no clean separation**. On-topic floor 0.496,
off-topic ceiling 0.584 — a *negative* gap. `मेरे फोन की बैटरी जल्दी खत्म हो
जाती है` outscores six of ten finance questions, because the corpus is thick
with app walkthroughs that mention phones.

With no threshold that both serves every real question and excludes junk, the
asymmetry decides it: a missing exemplar costs nothing, a mismatched one
re-voices a correct answer toward an unrelated topic.

**Cost:** ~40% of questions get no style at all.

**Would overturn it:** a corpus without app-tutorial content, which would raise
the off-topic ceiling's distance from the floor. Re-run
`scripts/eval_style_examples.py` after any corpus change.

---

## 7. Style passages containing a figure are dropped at build time

**Decided:** `_CLAIM` in `build_style_index.py` removes any passage with a rate,
an amount, or a tax section. 164 of 679 chunks.

**Why:** a retrieved passage is unverified text sitting in the same prompt as
tool output. "SBI gives 7.5% on gold loans" as a *style example* is one
inattentive sentence away from being repeated as fact.

**Cost:** a quarter of the corpus, including good explanatory passages.

**Would overturn it:** nothing. The whole value of this system is that numbers
are traceable.

---

## 8. `effective_from` and `as_of` are separate fields

**Decided:** every fixture entry carries both.

**Why:** they were one field, and FinGuru told users "as of 1 June 2026" about a
rate unchanged since 15 December 2025. The bank's published w.e.f. date is what
a user needs; the date a human last checked the page is what staleness is
measured from. Same shape, different meanings, and conflating them states a date
the bank never published.

**Cost:** two fields to maintain per entry.

**Would overturn it:** nothing.

---

## 9. Account-lookup capabilities were removed, not fixed

**Decided:** `accounts.get_profile` and `accounts.get_borrowings` are gone from
`agent.yaml`, with a comment where they were.

**Why:** asked about a ₹3,00,000 loan, FinGuru answered about the fixture's
₹3,18,500 — it preferred stored data over what the user actually said. For a
demo agent with no real account binding, fake account data is a liability with
no upside.

**Cost:** no "what's my balance" story.

**Would overturn it:** a real, authenticated account binding.

---

## 10. Rates live in a fixture, rules live in the document index

**Decided:** split by *how often the underlying truth changes*, not by topic.

**Why:** small-savings rates are revised quarterly. The document corpus carries
one corpus-wide `retrieved_on` with a 180-day window, so a quarterly rate would
go stale inside it invisibly. The fixture gives per-fact `max_age_days` — 100 for
schemes, so an entry flags itself about a quarter after it was checked.

Structural rules (who can open a BSBD, what an OVD is) change on the scale of
years and belong in text that can be quoted.

**Cost:** two places to look for "what do we know about PPF".

**Would overturn it:** per-document freshness in the index.

---

## 11. The index is written only at the end of a build

**Decided:** `build_*_index.py` accumulates in memory and writes once.

**Why:** the ngrok tunnel dropped three times during the first document build. A
streaming write would have left a half-written index that loads fine and answers
badly.

**Cost:** a long build holds everything in memory.

**Would overturn it:** a corpus too large to hold. Not close.

---

## 12. Style is measured by A/B, not by inspection

**Decided:** `scripts/compare_style.py` answers every question twice in one
process and diffs the results.

**Why:** two integration bugs here were **completely silent** — a language key
that matched nothing, and a message path that read the wrong dict level. Both
built cleanly and passed their tests. Neither raised anything. The only symptom
was that nothing changed, which looks exactly like "style didn't help".

**Cost:** ~6 minutes of model calls per run.

**Would overturn it:** nothing. Anything that can no-op must be tested by
observing an effect.

---

## 13. A written register guide sits beside retrieval, not behind it

**Decided:** `fixtures/register/<lang>.md` is injected whenever the script is
recognised, independently of whether any passage clears the floor.
`capabilities_impl/fixtures/register/ta.md` is the first.

**Why:** retrieval needs a corpus, and a corpus is a slow instrument. Tamil has
none. The Hindi one that *does* exist reached only 5 of 12 real questions,
because it was collected by channel rather than by topic — so even a language
with a corpus goes unserved on whole topics. A guide is a few hundred lines
someone can write in an afternoon and it fires every time.

They compose: when a corpus lands, its passages join the guide in the same
section rather than replacing it. The guide is checked-in text written on
purpose; passages are scraped and unverified, which is why they keep their own
framing.

**Cost:** two places that shape wording for one language, and a guide is
unmeasured where the Hindi table is counted. `ta.md` says so at the bottom.

**Would overturn it:** a Tamil corpus with real topic coverage. Even then the
guide probably stays — it costs nothing when passages are found, and it is the
only thing serving the topics the corpus misses.

---

## 14. The style toggle is a runtime flag, and runtime flags must be declared

**Decided:** `style` rides at the top level of `raw_input` beside `evidence`,
defaults to on, and is listed in `_TEXT_ROUTING_KEYS`.

**Why the placement:** evidence is domain data — it persists on the session,
merges turn to turn, and is shown back to the user. A rendering preference is
none of those things.

**Why the registration, which is the part that bites:** `_build_text_prompt`
renders every raw_input key it does not recognise straight into the user
prompt. Adding `style` put a literal `style: True` line in front of the model.
The tool loop read it and **changed which tools it called** — on the test
question, `style: False` pulled in an extra tool and a ₹90,000 figure, so the
styled answer appeared to have "lost" it. Three runs each way, identical every
time. Style is not permitted to reach tool selection at all, and it did,
through the one path nobody was watching.

**Cost:** a second place to edit when adding a runtime flag.

**Would overturn it:** `_build_text_prompt` taking an explicit allow-list of
content fields instead of excluding known routing keys, which would make the
failure impossible rather than merely tested. Worth doing; not done.

---

## 15. gemma4:12b, chosen for memory and not for quality

**Decided:** the agent runs on `gemma4:12b` (7.6 GB) instead of `qwen3.6:35b`
(23.9 GB). ~16 GB freed for the voice agent sharing the host.

**Why:** the two models do not co-exist on this box. An assistant that cannot
be spoken to is a worse product than one that is a few points weaker on text.

**What it costs**, from the original benchmark on this agent's hard cases:

| | quality | broken | wall time (3 questions) |
|---|---|---|---|
| qwen3.6:35b | 29/33 | 0 | 136s |
| gemma4:12b | 23/33 | 1 | 190s |

**Re-measured at the switch**, on tool chaining, rate lookup, FD maturity, and
Tamil and Hindi script mirroring — all five called tools correctly, including
the `get_fd_rate` → `fd_maturity` chain, all returned full content under
`think: false`, and **the recorded Western-digit-grouping failure did not
reproduce**: `₹2,40,000`, `₹4,19,973`, `₹1,06,398.02`.

One new finding: **Tamil is disproportionately slow** — 97s against 10–21s for
everything else, and 21s for Devanagari Hindi. Suspected tokenizer cost on
Tamil script, compounded by the 3,253-character Tamil register guide. That is
above `timeout_seconds: 90` for a single call and matters most for voice.

**Would overturn it:** the voice agent moving to its own host. Then qwen comes
back — it is the fallback, not gemma4:31b (timed out on every case) or
gpt-oss:20b (empty replies).

---

## 16. Voice mode is prompting, not a second model or a post-processor

**Decided:** `voice: true` appends a brief to the answer prompt. No summariser
runs afterwards.

**Why:** the same argument that killed the style rephraser. Anything that
shortens an answer *after* the grounded call can drop a caveat or round
₹1,06,398.02, and it runs after the only step that knows those matter. The
call that already writes the answer is the one that should write it short.

**The brief goes last, after style, deliberately.** They contradict each other
— style says "say everything you would have said, the same length", voice says
"two to four sentences". Position in the prompt is how voice wins, and there is
a test pinning the order.

**The override is scoped to length and layout, and that scoping is load-bearing.**
An earlier draft said it overrode "length or formatting"; the model read digit
grouping as formatting and returned ₹106,398.02 spoken where it had written
₹1,06,398.02 on screen. An Indian listener hears that as a hundred thousand.

**Measured:** 30–63% of the screen length, 2–3 sentences, markdown clean on 4
of 4, and the only figures dropped were as-of dates and percentages restated as
rupee amounts.

**Cost:** brevity is a request, not a guarantee. Nothing enforces the sentence
count.

**Would overturn it:** a TTS client that needs SSML, which is a format the
model should not be hand-writing.

---

## 17. Sarvam is an accelerant, never a dependency

**Decided:** `capabilities_impl/sarvam.py` provides Indic language ID and
translation. Not registered as a tool. Every path returns `None` without a key,
on a timeout, or on an unrecognised response, and the pipeline proceeds exactly
as it does today.

**Why it exists:** a Tamil question came back answered in Telugu. Our own
detection is a Unicode range (`style_examples.language_of`), which can tell
Devanagari from Tamil and nothing finer — not Hindi from Marathi, not Tamil
from Malayalam, and nothing at all once ASR partly romanises the text. Sarvam
is trained on precisely these languages.

**Trust order is caller first, Sarvam second.** The voice client already sends
`language`, and its ASR knows what it transcribed better than anything
downstream can infer from the output. Sarvam is the fallback, not the default —
it is a network round trip on the path to every answer.

**Timeout is 4 seconds.** This runs before the answer, so a slow third party
would be felt on every turn. It is skipped rather than waited for.

**`translate` is written and deliberately not wired in.** The intended use is
giving `docs.search` a real English query instead of refusing Indic ones
(#4 above). That overturns a deliberate decision and wants measuring first: a
translator subtly wrong on financial vocabulary degrades retrieval silently,
which is the exact failure #4 exists to avoid.

**Unverified.** The header name, two endpoint paths, and the response field
names are written from Sarvam's public API and have not been checked against a
live key. All four are environment-overridable and
`python -m capabilities_impl.sarvam` probes all four in one call, naming
whichever is wrong.

**Would overturn it:** Sarvam becoming load-bearing for grounding rather than
for wording. It must stay on the side of the system where being absent costs
nothing.

---

## Things deliberately NOT done

**A second model to rephrase answers into colloquial Hindi.** Proposed and
rejected. A rephraser downstream of the grounded model can round ₹1,06,398.02,
drop a caveat, or lose the RBI-vs-SBI attribution — and it would run *after* the
only step that knows those things matter. Style went into the prompt of the call
that already writes the answer instead.

**Fine-tuning on the vernacular corpus.** 393 usable Hindi rows at the time. That
is few-shot territory, not training territory.

**A two-index router for Indic document search.** Measured and viable (bge-m3
8/10 vs nomic 0/10 on Indic). Not built: it adds a routing decision that can be
silently wrong, days before a demo, to fix a case that translate-then-retrieve
already handles.

**Merging adjacent transcript chunks into longer exemplars.** Implemented behind
`--merge-adjacent`, measured, **inert** — the per-chunk filter drops 248
scattered chunks, so survivors are almost never adjacent. Kept because it would
work on an unfiltered corpus; do not assume it does anything here.
