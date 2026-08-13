# FinGuru — grounded personal-finance chat

A chat agent for India retail banking and personal-finance coaching. Its one
design goal: **never state a number it didn't get from a tool.**

## Why it's built this way

An LLM asked "what's the 1-year FD rate?" will happily produce a rate. It
will be plausible, formatted correctly, and wrong. The same is true of EMIs,
compound interest and payoff timelines — a 12B model doing amortisation in
its head returns confident nonsense.

So FinGuru holds no numbers itself. Rates come from lookups that carry their
own `as_of` date and source URL, arithmetic runs in Python, and the skill
instructions require the model to pass provenance through into the answer and
to say "I don't have that" when a tool can't supply a figure.

This is grounding, not a correctness guarantee. The model can still
misinterpret a question or frame a right number badly. What it structurally
cannot do is invent the number.

## Where the data comes from

| Source | What | Freshness |
|---|---|---|
| `capabilities_impl/fixtures/india_reference_rates.json` | Repo rate, savings rate, FD brackets, indicative loan bands, tax limits | **Hand-maintained** — see below |
| Frankfurter / ECB (`api.frankfurter.app`) | Exchange rates | Live, cached 1 hour |
| `capabilities_impl/money_math.py` | EMI, FD maturity, SIP, debt payoff, budgeting | Computed per call |

### Why the India rates aren't a live API

There is no free official machine-readable feed for Indian deposit and
lending rates. RBI publishes policy decisions as press-release HTML; each
bank publishes its own rate card as a web page. Scraping those means parsing
markup that changes without notice, on pages whose terms generally prohibit
it — and a misparsed scrape reports a wrong number *as fact*, which is the
exact failure this agent exists to avoid.

A curated file with an explicit `as_of` per entry is the honest version of
the same thing. It is just as current as a scraper on the day you update it,
and when it goes out of date it says so instead of lying.

### Keeping the rates current — the one maintenance task

Every entry has `as_of`, `source_url` and `max_age_days`. When
`age_days > max_age_days`, `india_rates.py` sets `stale: true`, and the skill
instructions require FinGuru to warn the user and tell them to confirm with
the bank. **A forgotten update degrades into a visible caveat, never into a
silent wrong answer.**

To refresh: open the entry's `source_url`, update `value` and `as_of`, leave
the rest alone. The repo rate is the one to watch — RBI's MPC meets
bi-monthly, so `max_age_days` is set to 75.

To check what's currently stale:

```powershell
python -c "from capabilities_impl import india_rates as r; import json; print(json.dumps({n: {'as_of': f()['as_of'], 'stale': f()['stale']} for n, f in [('repo', r.get_policy_rate), ('savings', r.get_savings_rate), ('fd_12m', lambda: r.get_fd_rate(12)), ('tax', r.get_tax_saving_limits)]}, indent=2))"
```

### Swapping in a real feed later

Point the same capability names at a new implementation in
`capabilities_impl/__init__.py`. No stage, agent, or skill file changes —
that's the seam `docs/adding-an-agent.md` §4 describes. Keep the
`as_of`/`source_url`/`stale` keys in the return value; the instructions and
tests both depend on them.

## Adding a tool

Three places, and **missing the third fails silently** — the capability stays
registered but the model never sees it, so FinGuru just quietly loses the
ability to ground that kind of answer:

1. The function, in `capabilities_impl/`
2. `DEFAULT_REGISTRY.register(...)` in `capabilities_impl/__init__.py`
3. **A schema entry in `_TOOL_SCHEMAS`** in `agent_platform/stages/pipeline_stages.py`

`tests/test_finguru.py::test_every_declared_capability_is_registered_and_callable_as_a_tool`
walks the agent's declared capabilities and asserts all three, so that silent
failure is caught at test time.

## Conventions the tools follow

- Rates are annual percentages (`6.6` means 6.6%), never decimals.
- FDs compound quarterly; SIPs are annuity-due (start of month).
- Failures return `{"ok": false, "reason": ...}` or `{"available": false,
  "reason": ...}` rather than raising — a bad tool call from the model must
  reach it as something it can explain, not an exception that kills the run.
- Lookups refuse rather than approximate. `get_fd_rate(360)` returns
  unavailable instead of clamping to the longest bracket, because a clamped
  number is a fabricated one wearing a real one's clothes.
- `get_loan_rate` returns a from/to band, never a single rate. The advertised
  floor is what the best credit profiles get; presenting it as "your rate" is
  the most misleading thing a finance bot can do.

## Published guidance (`docs.search`)

The rate tools answer "how much". `docs.search` answers "what am I entitled
to" — deposit insurance cover, liability for a fraudulent card transaction,
home loan foreclosure charges, how to escalate to the Ombudsman. Same
grounding contract, applied to rules instead of numbers: don't recall a
regulation, retrieve it and cite it.

The corpus is **15 public RBI customer FAQs, 360 chunks** (6.1 MB), built by
`scripts/build_doc_index.py` into `capabilities_impl/fixtures/doc_index.json`:
deposit insurance, card transactions, housing loans, the Ombudsman scheme,
KYC, UDGAM, the DEA Fund, education loans, ATMs, NEFT, RTGS, prepaid payment
instruments, cheque clearing, tokenisation and Sovereign Gold Bonds.

A "chunk" is one question-and-answer from an FAQ — split on the documents'
own numbered questions rather than a fixed character window, capped at 1800
characters. Median 542 chars. Whole documents are useless as embeddings (one
vector for 24,000 characters averages into "vaguely about home loans"), and
single sentences lose the context that makes a number mean anything.

**Senior Citizens Savings Scheme is deliberately absent.** RBI's page for it
(`FAQView.aspx?Id=62`) is a stub — a title and a date, no content. The build
now aborts if any source yields zero chunks, because the failure is otherwise
silent: the index still builds and `docs.list_sources` still advertises the
topic, so the agent reports no coverage for questions it was supposed to
answer.

**Build-time, not request-time.** Nothing in the chat path fetches a web page
or re-embeds a document. A live fetch inside a turn would put a third-party
site's uptime and latency in the user's way, and the evidence behind an
answer should be reproducible rather than whatever the site returned that
second. Rebuild with `python scripts/build_doc_index.py`.

**No router.** Every query searches all sources at once. Asking a model to
first pick a source adds a decision that can be silently wrong — a bad pick
is indistinguishable from the corpus genuinely not covering the question —
and real questions ("can my bank hand my data to a credit bureau?") span
sources. Ranking across everything *is* the routing, for free.

### Three things that were measured, not assumed

**The relevance floor (`MIN_SCORE = 0.58`).** Vector search always returns
its nearest neighbours even when the nearest thing is irrelevant, and an
off-topic chunk still looks like a citation. So the floor is measured, not
guessed — and it must be **re-measured whenever the corpus or embedding model
changes**, because both bounds move:

| Corpus | Off-topic ceiling | On-topic floor | Usable gap |
|---|---|---|---|
| 90 chunks | 0.520 | 0.687 | 0.167 |
| 360 chunks | 0.547 | 0.633 | **0.086** |

0.58 sits inside both. Note the direction: more chunks means more chances
something is coincidentally close, so the ceiling rises; broader coverage
brings harder questions, so the floor falls. **The gap halved when the corpus
quadrupled.** Past a few thousand chunks a single global threshold stops
separating the two at all, and the answer then is a reranker or per-source
thresholds — not a smaller number here. That is the real ceiling on how far
this corpus can grow, and it will bind well before search speed does.

**Vernacular queries are refused, not embedded.** `nomic-embed-text` is
English-first: a raw Tamil question scores ~0.54 against the passage its
English translation matches at 0.72 — near enough to the floor to sometimes
work and sometimes not. So a non-Latin query returns `available: false` with
an instruction to translate and retry. The agent already forms English tool
arguments, so in practice it translates the question itself and answers in
the user's language. Romanized Hinglish is allowed through — it's Latin
script and embeds fine.

**Similarity ranks topic, not answerhood — so retrieve wide.** For the query
"what is the deposit insurance cover if a bank fails?", the chunk headed
"What is the maximum deposit amount insured by the DICGC?" — the one holding
the ₹5,00,000 answer — ranks about **ninth**. Everything above it is
genuinely about deposit insurance and simply doesn't answer the question. A
lexical/hybrid blend makes it *worse* here (the top wrong chunk contains
every query term; the right one is missing two). Hence `DEFAULT_TOP_K = 8`:
the model is the precision filter.

The bigger fix was not ranking at all but **query phrasing**, which is why
the instructions and the tool description both say to ask for the fact rather
than the situation, and to search again if the passages don't contain the
answer:

| Query | Rank of the ₹5,00,000 chunk |
|---|---|
| "deposit insurance cover if a bank fails" | 9th |
| "how much of my deposit is insured" | 1st |
| "what is the maximum amount insured per depositor" | 1st |

Two known cases still need that retry, and both are structural rather than
fixable by tuning:

- **Cross-references severed by chunking.** Asked "what documents count as
  valid KYC proof?", the top three chunks all say *"documents as mentioned in
  the reply to Q 5 above"* — they point at the chunk holding the actual list
  instead of containing it. Rephrasing to "what documents are required for
  opening a bank account" retrieves it at rank 1.
- **Generic phrasing colliding across documents.** "What happens to money in
  an account nobody has touched for ten years?" matches "what happens if
  funds are not credited" (NEFT) and "what happens to an unused PPI" ahead of
  the DEA Fund rule.

`scripts/eval_doc_search.py` reports these as `RETRY` rather than failures —
the agent is instructed to rephrase, so they are working as designed. A
**rising** retry count is the signal to investigate.

### Per-source cap

`MAX_PER_SOURCE = 3` stops one document filling every slot; leftover slots
are refilled by score, so results never shrink. With 68 PPI chunks in the
corpus, a wallet-adjacent question would otherwise return eight passages from
one FAQ, and a cross-cutting question ("can my bank give my data to a credit
bureau?") needs KYC *and* the Ombudsman scheme. This costs something on
questions squarely about one document — it displaced five KYC chunks in the
example above — which is the intended trade.

### The regression check

`python scripts/eval_doc_search.py` — 11 question → must-appear-fact pairs
plus 5 off-topic queries that must return nothing. Run it after **any**
change to the corpus, chunker, embedding model or `MIN_SCORE`. `--full` also
runs real chat turns.

It checks retrieved chunks rather than the model's reply: ~100× faster, no
LLM needed, and it isolates the thing that actually regresses. Expected
strings are copied **verbatim from the source text** — RBI writes "₹
2,00,000/-" and "tenor … is 8 years", so "2 lakh" and "eight years" both
failed while retrieval was perfectly fine. A wrong expectation is
indistinguishable from a real miss in the output, so check the document
before trusting a red line.

### Refreshing, and what it costs

RBI FAQs change when circulars are amended, so every result carries
`retrieved_on`/`age_days`/`stale` (over `MAX_AGE_DAYS = 180`) — the same
staleness treatment as the rates, because a confidently-cited superseded rule
is worse than no citation.

Retrieval adds a document lookup plus a larger prompt. Measured end-to-end:
16–22s in English, ~52s in Tamil, and up to ~120s on a bad run over the ngrok
tunnel. Budget for that before demoing it live.

## Vernacular languages

FinGuru replies in whatever language and script the user writes in — English,
Hindi, Tamil, Telugu, Bengali, Marathi, Kannada and the rest, including
romanized Hinglish/Tanglish. This is entirely a prompt-and-contract property;
no translation layer, no per-language model, no routing. `gemma4:12b` is
natively multilingual, so the work was in constraining it, not enabling it.

Three deliberate design points:

**`language` is the first field in the output contract.** Ollama generates
structured output in schema property order, so declaring the language first
makes the model commit to one before writing `content`, rather than choosing
retroactively. Moving it weakens adherence without failing any test — which is
why `test_language_is_declared_before_the_content_it_governs` exists.

**Script is mirrored, digits are not.** Hinglish in gets Hinglish out, not
Devanagari — someone typing on a phone keyboard often can't comfortably read a
formal Devanagari paragraph. But every *figure* stays in Western digits even
when the user typed native numerals, because Indian passbooks and UPI apps
print Western digits regardless of page language, and because `₹2,13,530.31`
can be checked against the tool result at a glance while `₹২,১৩,৫৩০.৩১`
cannot. Both halves of that rule were empirically necessary: Bengali and
Marathi initially mirrored the user's numerals, and Marathi kept localising
percentages (`६.६%`) after it had stopped localising rupee amounts.

**Grounding is language-independent, and this is the actual risk.** Fluency
survives translation better than tool-calling does, so the temptation to
answer from memory is strongest in exactly the languages where the user is
least able to check. This is not hypothetical — asked "how big an emergency
fund?" without tools, the bare model answered 3–6 months in Hindi, 6–12 in
Tamil and 6–9 in Marathi. With the tools wired, all twelve languages tested
call `india.get_fd_rate` + `money.fd_maturity` and quote 6.6% / ₹2,13,530.31
identically, with the as-of date and tax caveat intact.

Verified end-to-end: English, Hindi (both scripts), Tamil, Telugu, Bengali,
Marathi, Kannada, Malayalam, Gujarati, Punjabi, Odia, Urdu (right-to-left).

Caveats must survive translation too — a dropped stale-rate warning turns a
hedged answer into a confident promise, and dropping it because it's awkward
to phrase in Tamil is the worst available failure.

Expect **25–65s** per vernacular turn versus ~15s for English, since non-Latin
scripts cost more tokens per character. Outliers happen — one Odia turn took
117s and Hindi has hit 110s — so for a timed demo, prefer Hinglish, Telugu or
Gujarati (consistently under 40s) and warm the model with a throwaway turn
first.

## Testing

`tests/test_finguru.py` covers wiring (agent loads, every capability
registered *and* schema'd), provenance (every rate carries `as_of`/`source`,
staleness flags correctly), and the math (each formula against its closed
form, `sip_required_for_goal` inverting `sip_projection`, the
never-pays-off debt branch, and bad inputs returning reasons not exceptions).

No live Ollama needed — the LLM call isn't exercised, per `docs/testing.md`.

### Checking the vernacular behaviour by hand

Language adherence is the model's, so it can't be unit-tested — the tests
above pin the contract that drives it. To check the behaviour itself, paste
these into the Playground and confirm three things each time: the reply is in
the same script you asked in, the AI Observation panel shows real tool calls,
and every figure in the prose matches the tool result exactly.

| Language | Prompt |
|---|---|
| Hindi | `अभी 1 साल की FD की ब्याज दर क्या है? अगर मैं 2 लाख रुपये लगाऊं तो कितना मिलेगा?` |
| Tamil | `இப்போது 1 வருட FD வட்டி விகிதம் என்ன? நான் 2 லட்சம் போட்டால் எவ்வளவு கிடைக்கும்?` |
| Hinglish | `bhai abhi 1 saal ki FD ka rate kya hai? 2 lakh daalu to kitna milega?` |
| Telugu | `ఇప్పుడు 1 సంవత్సరం FD వడ్డీ రేటు ఎంత? నేను 2 లక్షలు పెట్టితే ఎంత వస్తుంది?` |
| Bengali | `এখন ১ বছরের FD-র সুদের হার কত? আমি ২ লক্ষ টাকা রাখলে কত পাব?` |
| Marathi | `सध्या १ वर्षाच्या FD चा व्याजदर किती आहे? मी २ लाख ठेवले तर किती मिळतील?` |
| Kannada | `ಈಗ 1 ವರ್ಷದ FD ಬಡ್ಡಿ ದರ ಎಷ್ಟು? ನಾನು 2 ಲಕ್ಷ ಹಾಕಿದರೆ ಎಷ್ಟು ಸಿಗುತ್ತದೆ?` |
| Malayalam | `ഇപ്പോൾ 1 വർഷത്തെ FD പലിശ നിരക്ക് എത്രയാണ്? ഞാൻ 2 ലക്ഷം ഇട്ടാൽ എത്ര കിട്ടും?` |
| Gujarati | `અત્યારે 1 વર્ષની FD નો વ્યાજ દર કેટલો છે? હું 2 લાખ મૂકું તો કેટલા મળશે?` |
| Punjabi | `ਹੁਣ 1 ਸਾਲ ਦੀ FD ਦੀ ਵਿਆਜ ਦਰ ਕੀ ਹੈ? ਜੇ ਮੈਂ 2 ਲੱਖ ਪਾਵਾਂ ਤਾਂ ਕਿੰਨਾ ਮਿਲੇਗਾ?` |
| Odia | `ବର୍ତ୍ତମାନ 1 ବର୍ଷର FD ସୁଧ ହାର କେତେ? ମୁଁ 2 ଲକ୍ଷ ରଖିଲେ କେତେ ପାଇବି?` |
| Urdu | `ابھی 1 سال کی FD کی شرح سود کیا ہے؟ اگر میں 2 لاکھ لگاؤں تو کتنا ملے گا؟` |

All of these ask the same thing, so the answers should agree numerically — 6.6% and
₹2,13,530.31 — regardless of language. Any divergence means a language lost
its tool call and answered from memory.

Also worth testing: switching language mid-conversation (it should follow the
latest message), and asking in Hindi something the tools can't answer, to
confirm it says so in Hindi rather than inventing a number.
