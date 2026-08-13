# INTEGRATIONS — who calls what

The onboarding app is the front end for three separate backends. Only one of
them is this repo. Written down because the FinGuru work kept running into
questions that were really "which service is that?" — and because a request
shape we do not control is a thing that changes without telling us.

**Last confirmed:** 13 August 2026, from the client team's own inventory.

---

## The three backends

```
  onboarding web app
        │
        ├──► onboarding API      VITE_API_BASE          not this repo
        ├──► FinGuru             VITE_FINGURU_URL       ← THIS REPO
        └──► voice server        VOICE_SERVER_TARGET    not this repo
                                 (via the Vite dev proxy)
```

The FinGuru feature in that app uses **one** call to us plus three to the
voice server. Everything under the onboarding API predates this work.

---

## 1. What they call on US

One endpoint. That is the whole surface.

```
POST {VITE_FINGURU_URL}/agents/finguru/invoke
Header: X-API-Key: <the agent's key>

{
  "evidence": {
    "question": "<ASR transcript or typed text>",
    "history":  [{"role": "user"|"assistant", "content": "..."}, ...],
    "style":    true,
    "voice":    true,
    "language": "ta"
  }
}
```

Defined client-side in `finguru.js`.

### Things about this shape that have already caused bugs

**Flags are nested inside `evidence`, not at the top level.** Our own chat
route puts them beside it. Both are read now (`_request_flag` in
`pipeline_stages.py`), but the first version of the voice flag read only the
top level, so their `voice: true` was set, sent, and silently ignored.

**The message field is `question`, not `message`.** Our chat route calls it
`message`. Both names resolve now (`_MESSAGE_KEYS`). Before that,
`_user_message` returned empty and the vernacular style layer quietly did
nothing on every Tamil turn — no error, no log line, correct-looking answers.

**They manage `history` themselves rather than using a session.** It arrives
as a list and gets rendered into the model's prompt as a serialized dict that
grows every turn. `/agents/finguru/chat` would handle this server-side via
`session_id`, but they are on `/invoke` and that is fine — just know the
prompt carries the whole transcript.

**`language` is sent and we do not act on it.** It reaches the prompt as
ordinary context and nothing reads it as a directive. Wiring it would pin the
reply language instead of inferring it from ASR text — worth doing after a
Tamil question came back answered in Telugu — but it is not built.

### What they get back

`/invoke` returns the reply at **`output.content`**, not `reply`:

```json
{"run_id": "...", "outcome": null, "decision": null,
 "output": {"language": "Tamil", "content_type": "text",
            "content": "…", "confidence": 0.9},
 "hitl": null, "error": null}
```

`/agents/finguru/chat` (which they do not use) returns `reply` directly plus a
`style` diagnostic saying whether the vernacular layer actually fired.

---

## 2. Voice server — NOT us

Same-origin paths the Vite dev proxy forwards to `VOICE_SERVER_TARGET/voice`,
attaching a Bearer token.

| path | forwards to | what |
|---|---|---|
| `POST /voice-api/transcribe` | `…/voice/transcribe` | multipart WAV → text (turn mode STT) |
| `POST /voice-api/synthesize` | `…/voice/synthesize` | `{text, language}` → WAV (TTS) |
| `WS /voice-ws` | `…/voice/call?token=…` | live mode |

Client side: `voice.js`, `useVoiceCall.js`.

**We do not build any of this.** Speech in and speech out belong to that
service. Our job ends at returning text worth speaking — which is what the
`voice` flag is for.

---

## 3. Onboarding API — NOT us

Pre-existing, unrelated to FinGuru. Listed so nobody goes looking for these in
this repo. Defined in `client.js`, based at `VITE_API_BASE`.

**Applications**

```
POST /applications/start
GET  /applications/{id}
GET  /applications/by-user/{mobile}
GET  /applications/{id}/status
POST /applications/{id}/consent
POST /applications/{id}/documents                  (multipart)
POST /applications/{id}/edit/{requirementId}       (multipart)
GET  /applications/{id}/notifications
POST /applications/{id}/handoff/{channel}
POST /applications/{id}/support/escalate
POST /applications/{id}/guardian/link
```

**Sessions**

```
POST /sessions/{sessionId}/message
GET  /sessions/{sessionId}/state
POST /sessions/{sessionId}/call/initiate
POST /sessions/{sessionId}/call/end
WS   /sessions/{sessionId}/call/live               (liveCall.js / SupportCall)
```

---

## Open, not built

- **`language` is unwired.** They send it; we ignore it. One Tamil question
  was answered in Telugu because the model inferred language from garbled ASR
  rather than being told.
- **No health check from the app.** It calls no `/health` at runtime, so a
  voice server that is down looks like a hung UI.
- **Fragment inputs.** Turns have arrived as `"ச"` — one character. The
  always-listening button ends capture early. Client-side, but it lands here.
