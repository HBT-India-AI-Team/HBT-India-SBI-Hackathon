# FinGuru backend → app: what changed, and what needs a change on your side

Everything in **§1** is already live on the FinGuru agent backend and needs
**no app change** — it works with the requests you send today.

**§2** is one small bug fix in your code. **§3** and **§4** are opt-in features
that are built and waiting, but will not appear until someone wires them.

Nothing here is urgent except §2.

---

## 1. Live now, no change needed on your side

### `history` is finally being used properly

You have always sent `evidence.history`. Nothing on our side read it, so it
fell through a generic field renderer and reached the model as a **literal
Python dict** pasted into the user's question:

```
evidence: {'question': 'And what about 15 years?', 'history': [{'role': 'user',
'content': 'EMI on 20 lakh...'}], 'name': 'Dhanush'}
```

It worked, in the sense that a model will read anything. Now it is rendered as
a transcript, bounded to the last 6 turns, and explicitly marked as context
rather than a source of figures, so it cannot be mined for a number that
should have come from a tool call.

**Keep sending exactly what you send today.** `{role, content}` is what we
expect. `{direction: 'inbound'|'outbound', text}` — your IndexedDB shape — is
also accepted, in case that store ever gets forwarded directly.

### `name` is now a real identity, not prompt noise

`lib/finguruIdentity.js` says *"the `name` IS the session key"*. Our side keyed
everything on `user_id`, which is why several things did not line up. Both now
resolve to one identity namespace. `name` also no longer leaks into the prompt
as content.

### Language

`language: "en" | "ta" | "hi"` works, at either nesting level. `lang` is
accepted as an alias. `ta-IN` and `ta_IN` both normalise, so a device locale
can be passed straight through.

---

## 2. One bug to fix — `।` is not Tamil punctuation

**File: `src/lib/ttsText.js`, in `TTS_FORMATTING_RULES` (line 21)**

`।` (U+0964) is the **Devanagari danda** — Hindi, Marathi, Sanskrit. **Tamil
conventionally ends sentences with the ASCII full stop.** This rule is live in
the Ollama path's real system prompt, so it is actively asking for
non-standard Tamil today.

**Before**

```js
- Every sentence must end with proper punctuation (., !, or ? — use । for Tamil sentence endings) so it can be correctly identified as a complete spoken unit.
```

**After**

```js
- Every sentence must end with proper punctuation (., ! or ?) so it can be correctly identified as a complete spoken unit.
```

We already applied the equivalent rule on the FinGuru prompt, without the
danda. Our splitter still *accepts* `।` where it appears — a mixed-script reply
may contain one — it just never asks for it.

**Optional, same file:** `SENTENCE_END_RE = /[.!?।]$/` misses `…` and `॥`, so a
chunk ending in an ellipsis collects a stray full stop (`"Well then….”`). Ours
uses `[.!?।॥…]`. Minor, cosmetic in speech.

---

## 3. Calculators — built, but nothing renders them yet

Every chat reply now carries a `tools` array. **It is empty on almost every
turn** — that is the normal case, not a bug. It is populated when the agent
actually computed an EMI or a SIP projection, or when the topic came up with
no numbers to work with.

This matches `finguru-dynamic-tools-frontend-spec.md` — same generic-renderer
contract, `execution` and `output_label` included, and a new tool still means
a DB row with no frontend deploy (§7).

**One deviation from that spec, deliberate:** every tool is
`execution: "server"` and there is **no `formula` field**. Your §4 already
warns never to `eval()` a formula string in the browser; we went further and
do not ship the arithmetic at all. `POST /api/tools/execute` runs the *same
code* that wrote the sentence above the calculator, so the widget and the
prose cannot disagree. Please do not reintroduce a client-side EMI formula —
two implementations will eventually differ, and the user has no way to tell
which number is real.

### 3a. Pass `tools` through

**File: `src/api/finguru.js`, in `askFinGuru`**

**Before**

```js
const result = {
  text: output.content || '',
  language: output.language || null,
  confidence: output.confidence,
  hitl: data?.hitl || { triggered: false, reasons: [] },
  error: data?.error || null,
};
```

**After**

```js
const result = {
  text: output.content || '',
  language: output.language || null,
  confidence: output.confidence,
  hitl: data?.hitl || { triggered: false, reasons: [] },
  error: data?.error || null,
  tools: data?.tools || [],              // NEW: calculators to offer
  sessionId: data?.session_id || null,   // NEW: see §4
};
```

### 3b. Attach it to the message

**File: `src/pages/FinGuruChat.jsx`, in the outbound `setMessages` block**

**Before**

```jsx
{
  id: outboundId,
  direction: 'outbound',
  text: replyText,
  language: res.language,
  variant: res.hitl?.triggered ? 'error' : 'default',
},
```

**After**

```jsx
{
  id: outboundId,
  direction: 'outbound',
  text: replyText,
  language: res.language,
  variant: res.hitl?.triggered ? 'error' : 'default',
  tools: res.tools || [],
},
```

Then render `msg.tools` through the generic renderer your spec already
describes — no `tool_id` special-casing needed.

### What arrives

```jsonc
{
  "tool_id": "emi_calculator",
  "reason": "computed",                  // or "mentioned"
  "prefill": { "principal": 2000000, "rate": 8.5, "months": 240 },
  "tool": {
    "tool_id": "emi_calculator",
    "name": "EMI Calculator",
    "execution": "server",
    "output_label": "Monthly EMI",
    "output_prefix": "₹",
    "inputs": [
      { "key": "principal", "label": "Loan amount",    "type": "number", "prefix": "₹",          "min": 0 },
      { "key": "rate",      "label": "Interest rate",  "type": "number", "suffix": "% p.a.",     "min": 0, "step": 0.05 },
      { "key": "months",    "label": "Tenure",         "type": "number", "suffix": "months",     "min": 1, "step": 1 }
    ]
  }
}
```

| `reason` | means | open it |
|---|---|---|
| `computed` | the agent actually calculated this; `prefill` holds the numbers it used | filled in, showing the result |
| `mentioned` | topic came up, nothing to compute | empty, to try figures in |

`prefill` keys always match `tool.inputs[].key`, so filling the form is a
direct lookup.

### Computing

```jsonc
POST /api/tools/execute
{ "tool_id": "emi_calculator", "inputs": { "principal": 2000000, "rate": 8.5, "months": 240 } }

→ { "result": 17356.46,                      // the headline number (spec §5)
    "value": 17356.46,                       // same, alias
    "output_label": "Monthly EMI",
    "output_prefix": "₹",
    "breakdown": { "emi": 17356.46, "total_payment": 4165551.52,
                   "total_interest": 2165551.52 } }
```

Debounce it — every keystroke is otherwise a round trip. A bad input returns
**400** with the reason (`"months must be > 0"`); showing that beats showing a
stale number.

**Format with `en-IN`.** `toLocaleString('en-IN')` gives ₹1,06,398.02. The
default locale gives ₹106,398.02, which an Indian reader misreads as a hundred
thousand.

### Saving

```
POST /api/tools/save   { "name": "Dhanush", "tool_id": "emi_calculator",
                         "input_values": {...} }
GET  /api/tools/saved?name=Dhanush
```

`name` is accepted, so the `saveTool(name, payload)` already written in
`api/finguruHistory.js` will work as-is. `user_id` also works; they are the
same namespace.

---

## 4. History and streaming

### `GET /api/history?name=<name>` now exists

`fetchNameHistory()` in `api/finguruHistory.js` is currently expected to
soft-fail because that backend did not exist. **It exists now.** Point
`VITE_FINGURU_NAME_API_BASE` at the FinGuru host and it will return real data.

```jsonc
GET /api/history?name=Dhanush

→ { "name": "Dhanush",
    "agent_id": "finguru",
    "session_id": "chat_68493830ac11",
    "messages": [
      { "role": "user",      "text": "What would my EMI be on a 20 lakh home loan…" },
      { "role": "assistant", "text": "…your monthly EMI would be ₹17,356.46." }
    ] }
```

The shape is `{role, text}`, which is the **first** branch of both fallback
chains your renderer already uses (`m.role || m.direction`,
`m.text || m.content || m.message`).

Two things worth knowing:

- **An unknown name returns 200 with `messages: []`,** not 404. First visit is
  a normal state, and your client cannot distinguish a 404 from a network
  failure anyway.
- **Recording only happens when you send `name`** (or `user_id`). Requests
  without one stay completely stateless, exactly as before.

We do **not** feed stored history back into the reply — you already send your
own `history`, and injecting ours on top would double the context. The
endpoint is purely for redrawing a thread.

### Streaming is available, and is a real code change

`POST /agents/finguru/invoke/stream` — same body, same `X-API-Key`, but the
response is **SSE, not JSON**, so `axios.post()` will not work and
`EventSource` cannot be used either (it is GET-only). It needs
`fetch()` + a `ReadableStream` reader.

```
data: {"event":"sentence","index":1,"text":"The current repo rate is 5.25 percent.","elapsed_ms":6719}
data: {"event":"sentence","index":2,"text":"This was last updated on 5 August 2026.","elapsed_ms":6948}
data: {"event":"done","session_id":"chat_…","output":{...},"tools":[...],"hitl":{...}}
data: {"event":"error","message":"…"}
```

Treat `done.output` as the authoritative reply and the sentences purely as an
early preview — `done` carries the same fields `/invoke` returns.

**Honest expectation:** most of a turn is the tool loop, not the writing. On a
12–17s answer this moves time-to-first-word to roughly 6–9s. Real, but it is
not the difference between slow and fast. If voice responsiveness is the goal,
the tool loop is the bigger lever and that one is on us.

Sentences are already stripped of markdown, list markers and brackets when
`voice: true`, so they are safe to hand straight to TTS. With `voice: false`
they keep their formatting, because the same stream can draw an on-screen
bubble.

---

## Summary

| | change needed | where |
|---|---|---|
| history used properly | none | live |
| `name` as identity | none | live |
| language / `lang` / `ta-IN` | none | live |
| **`।` in the TTS rules** | **1 line, delete a clause** | `src/lib/ttsText.js:21` |
| calculators | pass `tools` through, render it | `api/finguru.js`, `pages/FinGuruChat.jsx` |
| history endpoint | point env var at our host | `VITE_FINGURU_NAME_API_BASE` |
| streaming | swap axios for fetch+SSE | `api/finguru.js` |

Questions on any of it, ask — most of this was written by reading your source
rather than a spec, so if something below does not match what you actually
have, we would rather hear it than guess.
