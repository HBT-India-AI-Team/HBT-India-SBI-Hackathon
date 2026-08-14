# CHANGELOG

**Generated from git history — do not edit by hand.**
Run `python scripts/update_changelog.py` after committing.

A hand-maintained changelog drifts out of sync and then misleads. The
commit message is the source of truth; this is a readable view of it.

---

## 2026-08-14 · Stream sentences to the client over SSE; stop calling TTS ourselves

`94b6945` — 5 files, +237/−97

Voice is entirely the client's: they do synthesis, playback and the audio
channel. This side produces text and hands it over early.

The previous version had this backend POST each sentence to the speech
service, which was wrong for the architecture. The browser owns the speaker,
so a WAV generated here had nowhere to go -- setting VOICE_TTS_URL would have
synthesised audio and discarded it, adding load and latency for nothing.
Removed, along with its three env vars, so nobody can configure that failure.

Replaced by POST /agents/{agent_id}/invoke/stream: same body, same X-API-Key,
text/event-stream response. `sentence` events as each one is finished, then a
`done` event carrying exactly the object /invoke returns, so a client can
treat the sentences as an early preview and `done` as authoritative.

Verified end to end against the running backend on a real turn:

    +12060ms  SENTENCE 1: A good way to manage your ₹85,000 ... 50-30-20 rule.
    +12421ms  SENTENCE 2: ... ₹42,500 for needs ... ₹17,000 toward savings.
    +12670ms  SENTENCE 3: ... do you already have an emergency fund set up?
    +12680ms  DONE  lang=English  347 chars

620ms of head start, every rupee figure intact. Small, for the reason in
DECISIONS #18: the tool loop is 81% of a turn and streaming the answer cannot
touch it.

The sink is now a ContextVar rather than an argument. It is chosen at the
HTTP edge and consumed six frames down inside a stage shared with every other
agent; threading a callable through all of that would put a speech concern
into signatures that have nothing to do with speech. The worker thread is
started with copy_context().run so the value follows it -- a ContextVar does
not cross a thread boundary on its own, and getting that wrong looks exactly
like "streaming produced no sentences".

A test asserts by AST that this module imports no HTTP client at all, so the
TTS call cannot come back by accident.

## 2026-08-14 · Read the speech endpoint per call, not at import

`a36dde8` — 2 files, +61/−9

VOICE_TTS_URL was captured at module import, so it froze at whatever the
environment held the first time speech_stream was touched. Adding it to .env
would have appeared to do nothing until someone worked out a restart was
needed -- and a test could not point it anywhere at all.

That is the same silent no-op this subsystem has now shipped three times: a
language key that matched nothing, an evidence nesting read at the wrong
level, a flag nested a level below where it was looked for. All configured
correctly, all doing nothing, none raising.

Sentences also now carry "VOICE_TTS_URL not set" as their error rather than
being silently skipped, so the trace distinguishes "nowhere to send" from
"send failed" -- which is the state every machine without a GPU peer is in,
including this one.

## 2026-08-14 · Regenerate changelog

`3ea2707` — 1 file, +57/−11

## 2026-08-14 · Stream the spoken answer, forwarding each sentence as it completes

`62f3cfb` — 7 files, +889/−7

In voice mode the answer call now streams and every finished sentence goes to
the speech service immediately, instead of the listener waiting for the whole
reply and then hearing a monologue.

MEASURED FIRST, because the result changes what anyone should do next. One
real voice turn, 17.6s end to end:

    tool loop (2 calls)   14,309ms   81%
    answer generation      3,243ms   18%

First sentence reached the endpoint 2,631ms into the answer phase, so this
bought ~600ms of a 17.6-second turn. Under 4%. The implementation is right;
the latency is not where the design assumed. Anyone trying to make voice feel
faster should be working on the tool loop.

Two hazards that are most of the work here, both of which produce bad audio
rather than an exception:

* The stream is JSON, not prose. `format` is still applied, so what arrives
  is {"language":"Tamil","content_type":"text","content":"... — splitting
  those tokens directly would send `{"language":"Tamil"` to a speech engine.
  JsonStringField walks the partial document and yields only that field,
  handling escapes that straddle chunk boundaries.

* Not every full stop ends a sentence. This agent's output is money:
  ₹1,06,398.02 split naively becomes "₹1,06,398." and "02", two utterances
  and a wrong number spoken to someone who cannot see the screen. A
  terminator only ends a sentence when a boundary follows, so a decimal never
  does. Rs./Mr./etc are handled too. Terminators are . ! ? plus the
  Devanagari danda । and ॥ -- note those are Hindi, not Tamil, which
  conventionally uses the ASCII stop; both are accepted.

Async as asked, over asyncio.to_thread since no async HTTP client is
installed and DECISIONS #3 keeps this service dependency-free. Each sentence
is dispatched with create_task, so a slow speech box cannot stall token
reading -- there is a test that fails if it ever does.

No retry on a stream, unlike _post_chat: by the time one fails part of it has
been spoken, and replaying repeats audio the listener heard. It falls back to
the non-streaming path, which retries. A dead speech service degrades to
text-only rather than losing the answer.

Only voice streams. On screen the whole answer appears at once, so streaming
would buy nothing and give up that retry.

Tests: +23, mostly on the two pure pieces, including every chunk size from 1
byte upward -- token boundaries are arbitrary and can land mid-number or
mid-escape.

## 2026-08-13 · Regenerate changelog

`2da931a` — 1 file, +53/−0

## 2026-08-13 · Add Sarvam AI for Indic language identification, and pin the reply language

`0e8e67e` — 6 files, +550/−10

A Tamil question came back answered in Telugu. Our own detection is a Unicode
range -- it separates Devanagari from Tamil and nothing finer, not Hindi from
Marathi, not Tamil from Malayalam, and nothing at all once ASR partly
romanises the text. Sarvam is trained on exactly these languages.

capabilities_impl/sarvam.py, not a registered tool: the model never chooses
it, the pipeline calls it. Same constraints as fx_rates -- allowlisted host,
cached, 4 second timeout -- with one difference that matters. fx fails
closed, because a wrong exchange rate is worse than none. This fails soft: no
key, a timeout, or an unrecognised response all return None and the answer
goes out exactly as it does today. It runs before every answer, so it is not
allowed to be load-bearing.

The reply language is now pinned in the prompt, caller first and Sarvam
second. The voice client already sends `language` and its ASR knows what it
transcribed better than anything downstream can infer from the output, so
that wins; Sarvam is the fallback for callers who send nothing. Codes are
resolved to names first -- "the user is writing in ta-IN" asks the model to
know a BCP-47 table, "Tamil" does not.

`language` therefore joins _TEXT_ROUTING_KEYS. It was inert context before
and left in the rendered evidence deliberately; now that it is a directive of
its own, leaving it in both places says the same thing twice, in two voices,
one of which reads as the user's.

Measured on the exact turn that produced the Telugu answer: the reply is
Tamil either way now (that bug was on qwen; we are on gemma), but with the
language pinned a garbled transcript gets "I didn't follow, please say that
again" instead of an invented interpretation -- which is the right move for a
voice channel where the input is frequently mangled.

`translate` is written and deliberately NOT wired in. The intended use is
giving docs.search a real English query instead of refusing Indic ones, and
that overturns DECISIONS #4 -- a translator subtly wrong on financial
vocabulary would degrade retrieval silently, which is the failure that
decision exists to avoid. It wants measuring first.

The header name, both endpoint paths and the response field names are written
from Sarvam's public API and are UNVERIFIED against a live key. All are
environment-overridable and `python -m capabilities_impl.sarvam` probes all
four in one call, naming whichever is wrong.

Tests: +8, all of which assert the absent/broken/slow paths degrade rather
than raise.

## 2026-08-13 · Regenerate changelog

`ec78ca0` — 1 file, +39/−0

## 2026-08-13 · Record the API surface, and show what is actually being called

`9db73ae` — 9 files, +544/−2

A client team was calling us on a URL shape we do not serve and with field
names we do not read. Both were invisible: from our side a 404 in a log
nobody reads, from theirs a request that failed for no stated reason. Two
things now make that visible.

**docs/API.md**, generated from the app by scripts/dump_api_surface.py. Never
hand-edited, for the same reason as the changelog -- a hand-maintained
endpoint list drifts and then reads as authoritative while being wrong.

It reads app.openapi(), not app.routes. They disagree: walking app.routes
finds 11 endpoints and none of the 24 under /admin, because routes pulled in
by include_router do not surface the same way. openapi() is the contract
FastAPI actually publishes, and it reports all 36. Docstring text is cut to
one sentence and has its pipes escaped, since several contain JSON examples
with | in them that silently turn a markdown table into gibberish.

**GET /admin/api-surface**, surfaced in the UI under Integrate. Two halves,
and both are needed: the declared list says what is real, the traffic list
says what is being asked for, and a row in the second that is not in the
first is the bug. Traffic is parsed from uvicorn's own access lines, with id
segments collapsed so one endpoint is one row. Declared paths are templates,
so matching compiles each to a pattern rather than comparing strings.

Verified by calling a path that does not exist: it shows up flagged, in red,
with its 404.

run_backend.ps1 now tees to uvicorn_out.log and appends rather than
truncates. That log is the only record of what a client called, and with it
going to a console that gets closed, a client on the wrong URL stays
invisible to everyone.

## 2026-08-13 · Regenerate changelog

`0dc1079` — 1 file, +42/−0

## 2026-08-13 · Read request flags at whichever level the caller nests them

`90389c6` — 3 files, +239/−11

The voice client sends its flags inside `evidence`, beside the question:

    {"evidence": {"question": ..., "style": true, "voice": true, "language": "ta"}}

Our own chat route puts them at the top level, and _voice_enabled read only
there. So their `voice: true` was set, sent, and ignored -- the flag existed
at a level nothing looked at, with no error and no log line. Same for the
message itself: they call it `question`, _user_message looked only for
`message`, and the vernacular style layer silently did nothing on every Tamil
turn they sent.

Both shapes are legitimate and neither is ours to rename, so both resolve now
via _request_flag and _MESSAGE_KEYS. This is the third instance of the same
failure in this subsystem -- a language key, an evidence nesting, and now a
flag nesting -- all of which built cleanly, passed their tests, and did
nothing.

Also scrubs routing keys from nested evidence, not just the top level.
Filtering one level put a literal "'voice': True, 'style': True" inside the
rendered evidence dict, where the model reads it as something the user said.
That is the exact leak that changed tool selection when the style flag was
added.

`language` is deliberately left unwired. It reaches the prompt as ordinary
context; nothing treats it as a directive. Worth doing after a Tamil question
came back answered in Telugu, but it is new behaviour, not plumbing.

Adds docs/INTEGRATIONS.md recording all three backends the onboarding app
talks to and which one is ours -- one endpoint, /agents/finguru/invoke. The
voice server (transcribe/synthesize/live WS) and the onboarding API are other
services and we build neither.

Tests: +3, covering all three live payload shapes.

## 2026-08-13 · Regenerate changelog

`92f8e99` — 1 file, +81/−0

## 2026-08-13 · Switch to gemma4:12b and add a spoken-answer mode

`d5e5c61` — 11 files, +380/−77

**Model.** qwen3.6:35b is 23.9 GB resident, gemma4:12b is 7.6 GB. The ~16 GB
freed goes to the voice agent sharing this host; the two do not co-exist, and
an assistant that cannot be spoken to is a worse product than one that is a
few points weaker on text. This is a memory decision, not a quality one, and
agent.yaml now says so.

Re-measured rather than assumed, because DECISIONS #1 says a model swap must
be: tool chaining, rate lookup, FD maturity, Tamil and Hindi script
mirroring. All five called tools correctly including the get_fd_rate ->
fd_maturity chain, all returned full content under think:false, and the
recorded Western-digit-grouping failure did not reproduce -- ₹2,40,000,
₹4,19,973, ₹1,06,398.02.

One new finding worth knowing: Tamil is disproportionately slow, 97s against
10-21s for everything else and 21s for Devanagari Hindi. That is above
timeout_seconds: 90 for a single call and it matters most for voice.

**Voice mode.** "voice": true on either chat route appends a brief to the
answer prompt: two to four sentences, plain prose, no markdown, no image
payload, say the source rather than spell the URL. Prompting, not a
post-processor -- the same argument that killed the style rephraser. Anything
that shortens an answer after the grounded call can drop a caveat or round a
figure, and it runs after the only step that knows those matter.

The brief goes last, after style, because they contradict each other
directly: style says "the same length", voice says "two to four sentences".
Position is how voice wins, and a test pins the order.

Its override is scoped to length and layout, and that scoping is
load-bearing. An earlier draft said "length or formatting"; the model read
digit grouping as formatting and returned ₹106,398.02 spoken where it had
written ₹1,06,398.02 on screen -- which an Indian listener hears as a hundred
thousand. Narrowed, and re-verified on two figures.

Measured: 30-63% of screen length, 2-3 sentences, markdown clean on 4 of 4,
and the only figures dropped were as-of dates and percentages restated as
rupee amounts.

Defaults off, so every existing caller is unaffected. Declared in
_TEXT_ROUTING_KEYS, which is the trap style already fell into. Playground
gets a Voice pill beside Colloquial so the output can be checked without
wiring a voice client.

Tests: +5. Suite is back to its 60 pre-existing failures.

## 2026-08-13 · Make the public chat route safe for clients we do not own

`21df6fe` — 2 files, +80/−3

The style flag on /agents/{id}/chat was read with `is not False`, which is
correct only if the client sends a real JSON boolean. A mobile or web client
sending {"style": "false"} -- a stringified bool, which is what form
encoding and several mobile HTTP libraries produce -- got style ON while
asking for it OFF. Silently, and disagreeing with the admin route, which has
Pydantic and coerces the same payload the other way.

That route takes a raw dict, so nothing coerces on its behalf. _bool_field
now reads bool, int and the usual string spellings, and falls back to the
default on anything unreadable rather than erroring: a malformed optional
flag must not cost someone their answer. Omitting the key still means on, so
a client written before the flag existed is unaffected.

Also adds "style" to the response. The layer has several ways to produce
nothing -- switched off, a script with no corpus, nothing above the
retrieval floor -- and they are indistinguishable in the reply itself. A
client that cannot see which one happened reports "the flag does nothing"
for the case where the flag worked perfectly and the corpus had no match.
Additive, so an existing client ignores it.

Verified against the running backend: {"style": "false"} now reports
{'applied': False, 'reason': 'turned off by the caller'}, and a request with
no style key at all still answers.

## 2026-08-13 · Regenerate changelog

`389b991` — 1 file, +50/−0

## 2026-08-13 · Add a colloquial-style toggle and a Tamil register guide

`63bedf2` — 13 files, +600/−97

Two things, both aimed at the same gap: the vernacular layer only reaches a
question when the corpus happens to cover it, and there is no way to see
whether it did.

**Toggle.** The Playground composer gets a "Colloquial" switch, default on,
sent per turn so the same question can be asked twice in one thread and the
answers compared. /invoke, the embed page and the public API send nothing
and keep exactly the behaviour they have.

Each reply now carries a badge saying whether style reached it, and if not
which of the several silent nothings happened -- switched off, a script with
no corpus, or nothing above the 0.60 floor. Toggling the switch and seeing
no change is the expected outcome for most English questions; without the
badge that reads as a broken feature, which is the first conclusion a demo
audience reaches for.

**Tamil register guide.** fixtures/register/ta.md, injected whenever the
user writes in Tamil, independent of retrieval. Tamil has no corpus and is
more diglossic than Hindi -- written and spoken Tamil differ enough that
formal written Tamil is hard going for a fluent speaker, and bank Tamil
defaults to the formal end. The guide covers the vocabulary seam (products
keep English names, concepts stay Tamil), address and verb endings, which
matter more in Tamil than vocabulary does, and lakh/crore number forms.
Hand-written judgement, not counted evidence, and it says so at the bottom.

language_of() now routes Tamil as well as Devanagari. The two halves
compose: when a Tamil corpus lands its passages join the guide in the same
section rather than replacing it.

**One bug found by testing rather than by reading.** Adding `style` to
raw_input put a literal "style: True" line in the user prompt, because
_build_text_prompt renders every key it does not recognise as routing. The
tool loop read it and changed which tools it called -- on the test question
`style: False` pulled in an extra tool and a Rs 90,000 figure, so the styled
answer appeared to have lost it. Three runs each way, identical every time.
Style is not permitted to reach tool selection at all, and it did, through
the one path nobody was watching. Fixed by declaring the key; tool selection
is now identical with the toggle on and off.

Tests: +5, including one pinning the flag out of the prompt. Suite is back
to its 60 pre-existing failures.

## 2026-08-13 · Regenerate CHANGELOG

`c44dc84` — 1 file, +15/−0

## 2026-08-13 · Add the style A/B sheet, with the rows style never reached greyed out

`591bcaf` — 1 file

The generated columns mislead on their own. Style reached 5 of the 12
questions; the other 7 are byte-identical because they scored below the 0.60
retrieval floor, so a reviewer comparing them is comparing nothing. Those
rows are greyed, and a reviewed column reads each flagged row rather than
counting its figures -- of the flags, one is a real loss (the Rs 45,000
emergency-fund anchor), the rest are reformatting or trimmed asides.

## 2026-08-13 · Regenerate CHANGELOG

`ebe546c` — 1 file, +37/−0

## 2026-08-13 · Stop style rewriting recommendations or adding a sign-off

`29ab6fe` — 2 files, +26/−0

Two failures the earlier guards did not cover, both found by re-running the
A/B rather than by reading the prompt.

A recommendation survived but changed: "at least 3 months of expenses
(about Rs 45,000)" came back as "at least 6 months", losing the rupee anchor
along the way. The instruction said not to DROP a rule; it said nothing
about adjusting one. It now names the case.

More seriously, an answer closed with "put your money in the right place or
you'll end up a servant of the bank" -- on an agent that speaks for a bank.
No passage in the corpus says anything of the sort; only 9 of 380 matched a
rhetoric scan and those were false positives on नौकरी. The model invented
it, because the register it is copying ends on a punchy line and that habit
transfers even when the sentiment does not. Style moves rhetorical habits,
not just vocabulary, which neither guard anticipated.

Verified after: no sign-off in any of the twelve answers, tools 12/12.

Worth recording what the same run showed about reach. Style changes only 5
of the 12 questions; the other 7 are byte-identical because nothing clears
the 0.60 floor. The misses are not marginal for the corpus -- a daughter's
scheme scores 0.496, senior-citizen income 0.538, pensions 0.542 -- because
61 videos weighted toward gold loans, app walkthroughs and account opening
simply do not discuss them. The floor cannot come down to meet them either:
an off-topic phone-battery question already scores 0.584.

So the lever from here is corpus breadth, not prompt wording. Every further
edit to this block only reaches the five questions that already work.

## 2026-08-13 · Regenerate CHANGELOG for the previous commit

`c9d369c` — 1 file, +31/−0

## 2026-08-13 · Drop app screen-narration from the style corpus, and document the system

`cddf9d4` — 7 files, +860/−1

A fifth of the style index was someone walking through a phone app -- "tap
here, enter the OTP, upload a selfie". Fluent, colloquial, on-topic-adjacent,
and the wrong thing entirely: it teaches an assistant that cannot see a
screen to narrate one. Two markers are required rather than one, because a
single "click here" inside a real explanation of opening an account online is
legitimate while a passage built from them is a tutorial. 64 dropped, 380
remain, and the A/B moved from 9/12 to 10/12 figures preserved with the
average length gap closing from -10% to -3%.

Two other things were tried and did not work, both left measured rather than
guessed at. Fewer exemplars (k=1) was worse, not better: on the one question
where k mattered it lost 30% of the answer where k=3 held. Merging adjacent
transcript chunks into fuller passages is inert here, because the per-chunk
filter removes 248 scattered chunks and survivors are almost never adjacent
-- kept behind a flag, with a comment saying so.

FLOW.md, DECISIONS.md and CHANGELOG.md answer "what is happening" without
reading the whole codebase. FLOW maps a request through both model calls and
both retrieval paths, and ends in a symptom table -- a 3-second confident
reply means tools were skipped, an empty reply means thinking got switched on
for the wrong call. DECISIONS covers only the choices that look wrong until
you know why, each with the evidence that would overturn it.

CHANGELOG is generated from git rather than written, because a hand-kept one
drifts and then reads as authoritative while being wrong. Never edit it; run
scripts/update_changelog.py.

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
