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
