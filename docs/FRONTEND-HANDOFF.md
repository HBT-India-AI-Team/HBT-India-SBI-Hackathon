# Frontend handoff — chat context and inline calculators

Everything below is **built and working on the backend**. This is what the
frontend team needs to do to use it. Nothing here needs a backend change.

Two features:

1. **Conversation context** — the backend already remembers a conversation.
   The client's only job is to send an identifier back.
2. **Inline calculators** — the backend says *when* to open an EMI or FIRE
   calculator and *what to prefill it with*. The client renders a form and
   posts the numbers back to be computed.

---

## Who builds what

| | backend (done) | frontend (to do) |
|---|---|---|
| remembering the conversation | ✅ stores messages, evidence, decision per session | send `session_id` back, or `user_id` |
| deciding a calculator should open | ✅ from the tool calls that ran | render what arrives in `tools` |
| the calculator's fields and labels | ✅ returned in the response | build a form from them |
| **doing the arithmetic** | ✅ `POST /api/tools/execute` | **do not compute in the browser** |
| remembering a user's saved inputs | ✅ keyed by `user_id` | call save / read saved |

The one rule worth stating plainly: **do not implement the EMI or FIRE
formula in the client.** The backend computes with the same code the agent
used to write the sentence above the calculator. A second implementation in
JavaScript means the widget and the prose can disagree, and when they do, a
user has no way to tell which number is real.

---

## 1. Conversation context

### The short version

```jsonc
// first turn
POST /agents/finguru/invoke   { "evidence": { "question": "...", "user_id": "u-123" } }

// every later turn — send back the session_id you were given
POST /agents/finguru/invoke   { "evidence": { "question": "...", "session_id": "chat_ab12…" } }
```

Send **either**:

- **`session_id`** — the exact conversation. Returned on every reply; send it
  back verbatim. This is the precise option and the one to prefer.
- **`user_id`** — the person. If you send this and no `session_id`, the
  backend finds that user's existing conversation with this agent and
  continues it. Good for "they closed the app and came back".

Send both and `session_id` wins. If a `session_id` belongs to a different
`user_id`, the backend starts a fresh conversation rather than leaking one
person's history into another's.

### What the backend already does with it

Each turn, the agent is given the **last 6 messages** of the conversation
alongside the new question, so follow-ups like "and what about 15 years?"
resolve without the client re-sending anything. Sessions are files on disk,
so they survive a backend restart.

### What the client still owns

**Redrawing the transcript after a reload.** The backend has the history;
there is currently no endpoint that returns it for display. If you want a
returning user to see their previous messages rather than an empty thread,
say so and we will add one — it is small, but it does not exist today.

---

## 2. Inline calculators

### What arrives

Every chat reply now carries a `tools` array. **It is empty on almost every
turn** — that is the normal case, not a bug.

```jsonc
{
  "output": { "content": "…your monthly EMI would be ₹17,356.46…" },
  "tools": [
    {
      "tool_id": "emi_calculator",
      "reason": "computed",
      "prefill": { "principal": 2000000, "rate": 8.5, "months": 240 },
      "tool": {
        "tool_id": "emi_calculator",
        "name": "EMI Calculator",
        "output_label": "Monthly EMI",
        "output_prefix": "₹",
        "inputs": [
          { "key": "principal", "label": "Loan amount", "type": "number", "prefix": "₹" },
          { "key": "rate", "label": "Interest rate", "type": "number", "suffix": "% p.a.", "step": 0.05 },
          { "key": "months", "label": "Tenure", "type": "number", "suffix": "months", "step": 1 }
        ]
      }
    }
  ]
}
```

**`reason` tells you how to open it:**

| `reason` | means | do |
|---|---|---|
| `computed` | the agent actually calculated this — `prefill` holds the numbers it used | open it filled in, showing the result immediately |
| `mentioned` | the topic came up with no numbers to work with | open it empty, for the user to try figures in |

`prefill` keys always match `tool.inputs[].key`, so filling the form is a
direct lookup. Render from `tool.inputs` rather than hard-coding a form and
adding a calculator later needs no frontend release.

### Computing a result

```jsonc
POST /api/tools/execute
{ "tool_id": "emi_calculator", "inputs": { "principal": 2000000, "rate": 8.5, "months": 240 } }

→ { "value": 17356.46, "output_label": "Monthly EMI", "output_prefix": "₹",
    "result": { "emi": 17356.46, "total_payment": 4165551.52, "total_interest": 2165551.52 } }
```

`value` is the headline number. `result` has the full breakdown if you want
to show more. Debounce this — every keystroke is a round trip otherwise.

A bad input returns **400** with the reason (`"months must be > 0"`). Show it;
it beats showing a stale number.

**Format with `en-IN`.** `toLocaleString('en-IN')` gives ₹17,356.46 and
₹1,06,398.02 — lakh grouping. The default locale gives ₹106,398.02, which an
Indian reader misreads as a hundred thousand.

### Saving a user's inputs

```
POST /api/tools/save    { "user_id": "u-123", "tool_id": "emi_calculator",
                          "input_values": {...}, "result": {...} }
GET  /api/tools/saved?user_id=u-123
```

Same `user_id` as the chat, so "my calculators" and "my conversation" mean the
same person.

### Listing everything available

`GET /api/tools` returns every calculator with its fields — enough to build a
standalone calculators screen with no chat involved.

---

## Endpoint summary

| method | path | key | what |
|---|---|---|---|
| `POST` | `/agents/finguru/invoke` | yes | ask a question (adds `tools` to the reply) |
| `POST` | `/agents/finguru/invoke/stream` | yes | same, streamed sentence by sentence |
| `GET` | `/api/tools` | no | every calculator + its fields |
| `POST` | `/api/tools/execute` | no | compute a result |
| `POST` | `/api/tools/save` | no | remember a user's inputs |
| `GET` | `/api/tools/saved?user_id=` | no | a user's saved calculators |

The `/api/tools/*` routes are **not key-gated**. They compute from numbers the
caller supplies and read nothing private, but they are also not rate-limited —
fine behind the demo tunnel, worth gating before anything real.

---

## Testing without the frontend

All of this works in the Playground today. Ask *"What would my EMI be on a 20
lakh home loan at 8.5% over 20 years?"* and the calculator opens under the
answer, prefilled, with the same number the sentence quotes.
