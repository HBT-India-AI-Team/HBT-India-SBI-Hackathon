# FinGuru Frontend — Voice UX Implementation Prompts

Seven prompts covering mic control, voice messaging, chat history, and four
additional gaps in the current voice pipeline. Hand each section to the
frontend developer independently — they're written to be self-contained.

---

## Prompt 1 — Mute mic during active call

```
Add a mute toggle for the microphone during an active voice call/session 
in the React frontend.

Requirements:
- Add a mute button/icon in the active call UI, visible only while a 
  call/session is active.
- When muted, stop sending audio frames to the STT WebSocket entirely 
  (do not just lower volume — actually stop transmission to avoid 
  wasting STT/bandwidth and avoid accidental transcription).
- Keep the WebSocket connection itself alive while muted — only pause 
  the audio stream, don't disconnect/reconnect on mute/unmute (avoids 
  reconnection latency).
- Reflect mute state clearly in the UI (icon change, color state, or 
  label) so the user always knows if they're muted.
- If TTS playback is in progress when the user hits mute, playback 
  should continue uninterrupted — mute only affects the outgoing mic 
  stream, not incoming audio playback.
- Persist mute state across the session but reset to unmuted at the 
  start of each new call.
- Add a keyboard shortcut (e.g. spacebar or M) for quick mute toggle, 
  common in call UIs, if consistent with the rest of the app's 
  interaction patterns.
```

---

## Prompt 2 — Record and send voice messages, play responses

```
Add voice message recording, sending, and playback to the React frontend 
chat interface (as opposed to a live streaming call — this is a 
record-then-send flow, like a voice note).

Requirements:
- Add a press-and-hold or tap-to-record mic button in the chat input area.
- Use the MediaRecorder API (or existing audio capture setup already in 
  the app) to record audio locally in the browser.
- Show real-time recording feedback: a waveform or simple animated 
  level indicator, plus elapsed recording time.
- On release/stop, allow the user to preview/playback the recording 
  before sending, and to discard and re-record if needed.
- On send, upload the recorded audio to the existing STT endpoint (or 
  the new streaming STT endpoint if enabled — respect 
  YONO_SERVER equivalents / VITE_* flags already in the frontend config) 
  and show a "processing" state in the chat while transcription + 
  LLM + TTS complete.
- Display the voice message in the chat thread as a distinct message 
  type (e.g. a compact audio player bubble with duration), not just 
  plain text, even after transcription is available — show the 
  transcript as a caption/subtext under the audio player.
- Play the assistant's spoken response automatically when it arrives, 
  with a visible audio player control (play/pause/seek) attached to 
  that chat message, not just autoplay-and-forget.
- Handle overlapping sends gracefully: if the user sends a new voice 
  message while a previous response is still being processed, queue 
  or clearly indicate processing order rather than racing requests.
- Handle mic permission denial gracefully with a clear inline message, 
  not a silent failure.
```

---

## Prompt 3 — Chat history storage

```
Add persistent chat history storage to the React frontend so 
conversations (both text and voice messages) survive page reloads and 
can be revisited.

Requirements:
- Store each message with: message id, role (user/assistant), type 
  (text/voice), text content, audio URL/blob reference (if voice), 
  timestamp, and session/conversation id.
- Decide and implement a storage layer — recommend starting with 
  IndexedDB (via a small wrapper like idb) for audio blobs and message 
  history together, since localStorage isn't suitable for binary audio 
  data and has strict size limits.
- Group messages into conversations/sessions (e.g. one entry per call 
  or per day), with a conversation list/history view the user can 
  browse and reopen.
- On reopening a past conversation, restore both the text transcript 
  and playable audio for voice messages (re-fetch from backend if 
  audio blobs aren't retained locally, or replay from IndexedDB if 
  they are — decide based on expected storage size/retention needs).
- Add a way to clear/delete conversation history (per-conversation and 
  all-at-once), since this may be needed for privacy/DPDP compliance 
  given FinGuru's regulatory context.
- Cap local storage growth with a reasonable retention policy (e.g. 
  keep last N conversations or last N days locally), configurable via 
  a constant.
- If the app has multiple users/sessions on the same device, scope 
  history storage by user/session id so histories don't bleed across 
  logins.
```

---

## Prompt 4 — Network/connection resilience

```
Add reconnection handling for the voice pipeline's WebSocket connections 
(STT, TTS, and any direct LLM streaming connection) in the React frontend, 
since the app talks to multiple machines (GPU PC, FinGuru backend) across 
a network that can drop mid-call.

Requirements:
- Detect WebSocket close/error events for each connection (STT, TTS, 
  LLM) independently, since one can drop while others stay alive.
- Implement automatic reconnection with exponential backoff (e.g. 
  starting at 500ms, capping at ~10s, with a max retry count before 
  giving up and surfacing an error to the user).
- On reconnect, resume the current turn gracefully where possible: 
  e.g. if TTS drops mid-playback of a streamed response, reconnect and 
  continue rather than restarting the entire conversation turn.
- Show a non-intrusive connection status indicator (e.g. a small banner 
  or icon) when a connection is degraded/reconnecting, so the user 
  understands why things feel stuck rather than assuming the app is 
  broken.
- If reconnection fails after max retries, fall back to the text-input 
  fallback flow (see Prompt 7) rather than leaving the user stuck in a 
  dead voice call state.
- Log connection drop/reconnect events with timestamps for debugging 
  latency and reliability issues across the three-machine setup.
```

---

## Prompt 5 — Pipeline stage visual feedback

```
Add visible pipeline-stage indicators to the voice call UI in the React 
frontend, so the user sees "listening → thinking → speaking" states 
rather than the app appearing frozen during STT → LLM → TTS processing.

Requirements:
- Define and track discrete states for the active turn: 
  Listening (mic capturing/VAD active) -> Transcribing (STT processing) 
  -> Thinking (LLM generating) -> Speaking (TTS audio playing) -> Idle.
- Update the UI state immediately as each stage's WebSocket/API 
  signals a transition (e.g. VAD end-of-speech -> Transcribing, first 
  LLM token -> Thinking, first TTS audio chunk -> Speaking).
- Use lightweight, low-distraction visual treatment (e.g. animated 
  icon or subtle status text near the mic/call UI) — this should 
  reduce perceived latency, not add visual noise.
- Since TTS and LLM are streaming sentence-by-sentence, allow rapid 
  Thinking/Speaking alternation to render smoothly (e.g. debounce 
  state flicker if sentences arrive in very quick succession) rather 
  than janking between states.
- Ensure state resets cleanly to Idle at the end of a turn or if the 
  call ends/errors out.
```

---

## Prompt 6 — Interrupt / barge-in handling

```
Add barge-in support to the React frontend voice call flow: allow the 
user to start speaking again while the assistant's TTS response is 
still playing, and have that interrupt playback and start a new turn.

Requirements:
- While TTS audio is playing, keep the mic's VAD active in the 
  background (do not fully suspend audio capture during Speaking state).
- If VAD detects the user has started speaking during TTS playback 
  (above a confidence/duration threshold to avoid false triggers from 
  background noise), immediately:
    1. Stop/pause the current TTS audio playback.
    2. Send a cancel/stop signal to the TTS WebSocket if it's still 
       streaming remaining sentences, so it stops generating further 
       audio for the interrupted response.
    3. Transition the UI state to Listening and begin capturing the 
       new utterance as a fresh turn.
- Make the barge-in sensitivity configurable (e.g. minimum speech 
  duration before triggering interrupt, to avoid a cough or short 
  noise cutting off the assistant).
- Add a config flag (e.g. VITE_BARGE_IN_ENABLED=true) to allow 
  disabling this behavior entirely if it proves unreliable in testing, 
  falling back to full-turn-based (no interrupt) behavior.
- Ensure interrupted responses are still logged/stored correctly in 
  chat history (Prompt 3) — mark them as "interrupted" rather than 
  silently dropping them.
```

---

## Prompt 7 — Fallback UI when voice pipeline fails

```
Add a graceful text-input fallback in the React frontend for when the 
voice pipeline (STT, LLM, or TTS) fails mid-call, so the user is never 
left stuck with no way to continue the conversation.

Requirements:
- Detect failure conditions: STT WebSocket unreachable/errors, LLM 
  call fails or times out, TTS WebSocket unreachable/errors, or 
  reconnection attempts (Prompt 4) exhausted.
- On any of these, automatically surface a text input box in the chat 
  UI with a clear, non-alarming message (e.g. "Voice isn't available 
  right now — you can type instead") rather than a raw error or a 
  silently frozen call state.
- Allow the user to continue the conversation via typed text through 
  the same LLM endpoint used for voice (skipping STT), with responses 
  shown as text (and optionally still attempting TTS playback if only 
  STT failed, or falling back to text-only response if TTS also failed).
- Add a manual "switch to text" control as well, available at all 
  times during a call, not just triggered by failure — some users may 
  prefer typing in noisy environments regardless of whether voice is 
  working.
- Once back on text fallback, periodically retry the voice pipeline in 
  the background (or offer a "try voice again" button) so the user can 
  return to voice once the underlying issue resolves, without needing 
  to reload the page.
- Ensure fallback conversations are stored in chat history (Prompt 3) 
  consistently with voice-based ones, tagged by message type (text vs 
  voice) as already planned.
```
