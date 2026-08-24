import { logTtsReceived, logTtsPlaybackStart, debugLog } from '../lib/pipelineLog';

// Streaming TTS over the voice server's WebSocket, shared by both brains.
//
// This was originally written inside api/ollamaStream.js against an endpoint
// that was assumed rather than confirmed. It is confirmed now: the voice
// server really does serve WS /voice/tts/stream, and it behaves exactly as
// the original contract guessed -- a JSON control frame carrying
// {"sampling_rate": 44100}, then int16 PCM chunks. WebSocket routes do not
// appear in FastAPI's openapi.json, which is why it looked absent.
//
// Why it is worth using over REST /synthesize, measured end-to-end through
// the tunnel on the same sentence:
//
//   REST  /voice/synthesize   6.0s before ANY audio (whole clip, then play)
//   WS    /voice/tts/stream   0.8s to the first chunk, ~0.4s cadence after
//
// The local model generates at roughly 0.87x realtime, so once the first
// chunk lands, generation stays ahead of playback and the rest arrives
// faster than it is consumed. That is what makes streaming safe here rather
// than a stutter risk.
//
// Wire protocol (per turn, one socket, many sentences):
//   ->  { session_id, text, language, name? }   one per sentence
//   ->  { session_id, done: true }              end of turn
//   ->  { session_id, cancel: true }            barge-in (best-effort)
//   <-  { sampling_rate }                       once, on connect
//   <-  <binary int16 PCM>                      audio, repeatedly

// Same-origin WS; Vite proxies /tts-ws to <voice-server>/voice/tts/stream and
// attaches the token (path configurable via VOICE_TTS_STREAM_PATH).
function ttsWsUrl() {
  const u = new URL('/tts-ws', window.location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.toString();
}

// Short/bounded: this connection is scoped to a single conversational turn.
const TTS_RECONNECT_DELAYS_MS = [500, 1500]; // 2 attempts

function logTts(msg, extra) {
  debugLog(`[TtsStream][${new Date().toISOString()}] ${msg}`, extra ?? '');
}

/**
 * A single TTS-stream WebSocket for one conversation turn. Sentences are sent
 * as they complete (queued until the socket opens); returned PCM plays back
 * gaplessly via Web Audio scheduling.
 *
 * Construct it EARLY -- at the start of a turn, before the first sentence
 * exists. Connecting costs ~1.2s through the tunnel, and doing it while the
 * LLM is still thinking takes that off the critical path entirely.
 */
export class TtsSentenceStream {
  constructor(sessionId, language, onSpeaking, onFallbackNeeded, name) {
    this.sessionId = sessionId;
    this.language = language;
    this.name = name; // resolved session identity (see lib/finguruIdentity.js)
    this.onSpeaking = onSpeaking; // (bool) -- coalesced across back-to-back sentence chunks
    // Fires ONCE, only if the socket never successfully opened even after
    // retries (streaming disabled server-side, or the endpoint unreachable) --
    // lets the caller fall back to non-streaming REST /synthesize instead of
    // silently losing this turn's audio.
    this.onFallbackNeeded = onFallbackNeeded;
    this.everConnected = false;
    this.ready = false;
    this.pending = []; // sentence payloads not yet sent to any socket
    this.sampleRate = 44100; // default until the server tells us otherwise
    this.audioCtx = null;
    this.nextPlayAt = 0;
    this.speaking = false;
    this.speakingEndTimer = null;
    this.finished = false; // finish() was called -- a drop after this is not resumable
    this.deliberateClose = false; // close() was called by us -- don't reconnect
    this.stopped = false; // stop() was called -- ignore any late frames, never play again
    this.reconnectAttempt = 0;
    this.chunksPlayed = 0;
    this.openedAt = performance.now(); // for time-to-first-audio in the log
    this._connect();
  }

  _connect() {
    try {
      this.ws = new WebSocket(ttsWsUrl());
      this.ws.binaryType = 'arraybuffer';
      this.ws.onopen = () => {
        logTts(this.reconnectAttempt > 0 ? 'tts-ws reconnected' : 'tts-ws connected', {
          sessionId: this.sessionId,
        });
        this.ready = true;
        this.everConnected = true;
        this.reconnectAttempt = 0;
        for (const p of this.pending) this._raw(p);
        this.pending = [];
      };
      this.ws.onmessage = (e) => this._onMessage(e);
      this.ws.onerror = () => {};
      this.ws.onclose = (event) => {
        this.ready = false;
        if (this.deliberateClose || this.finished) return; // normal end-of-turn close
        // There is no per-sentence ack in the protocol, so we can only resend
        // what never made it onto a socket (this.pending) -- sentences already
        // sent to the now-dead connection may or may not have been synthesized
        // server-side, and we have no way to know.
        if (this.reconnectAttempt >= TTS_RECONNECT_DELAYS_MS.length) {
          logTts("tts-ws reconnect attempts exhausted -- giving up on this turn's audio", {
            sessionId: this.sessionId,
            code: event.code,
            everConnected: this.everConnected,
          });
          // Only fall back if the connection NEVER worked this turn -- if it
          // worked and then dropped mid-turn, some audio may already be
          // playing, so restarting via REST now would risk double-speaking.
          if (!this.everConnected) {
            this.deliberateClose = true; // stop any further reconnect attempts
            this.onFallbackNeeded?.();
          }
          return;
        }
        const delay = TTS_RECONNECT_DELAYS_MS[this.reconnectAttempt];
        this.reconnectAttempt += 1;
        logTts(`tts-ws closed unexpectedly, reconnecting (attempt ${this.reconnectAttempt}) in ${delay}ms`, {
          sessionId: this.sessionId,
          code: event.code,
          reason: event.reason,
        });
        setTimeout(() => {
          if (!this.deliberateClose) this._connect();
        }, delay);
      };
    } catch (err) {
      logTts('failed to open /tts-ws', { error: String(err) });
      this.ws = null;
      if (!this.everConnected) this.onFallbackNeeded?.();
    }
  }

  _raw(payload) {
    try {
      this.ws?.send(payload);
    } catch {
      /* socket gone */
    }
  }

  _enqueue(obj) {
    if (this.stopped) return; // don't send anything after a hard stop
    const payload = JSON.stringify(obj);
    if (this.ready) this._raw(payload);
    else this.pending.push(payload);
  }

  sendSentence(text) {
    this._enqueue({
      session_id: this.sessionId,
      text,
      language: this.language,
      ...(this.name ? { name: this.name } : {}),
    });
  }

  finish() {
    this._enqueue({ session_id: this.sessionId, done: true });
    this.finished = true;
  }

  _onMessage(e) {
    if (this.stopped) return; // hard-stopped: drop any frames still arriving mid-close
    if (typeof e.data === 'string') {
      try {
        const m = JSON.parse(e.data);
        if (m?.sampling_rate) this.sampleRate = m.sampling_rate;
      } catch {
        /* ignore non-JSON control frames */
      }
      return;
    }
    if (e.data instanceof ArrayBuffer) {
      logTtsReceived({ streaming: true, sessionId: this.sessionId, audioBytes: e.data.byteLength });
      this._play(e.data);
    }
  }

  _play(buf) {
    if (this.stopped) return; // never resurrect playback on a new context after stop()
    if (!this.audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new Ctx();
      this.nextPlayAt = this.audioCtx.currentTime;
    }
    const view = new DataView(buf);
    const n = Math.floor(buf.byteLength / 2);
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const s = view.getInt16(i * 2, true);
      f32[i] = s / (s < 0 ? 0x8000 : 0x7fff);
    }
    const audioBuf = this.audioCtx.createBuffer(1, n, this.sampleRate);
    audioBuf.copyToChannel(f32, 0);
    const src = this.audioCtx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(this.audioCtx.destination);
    const at = Math.max(this.audioCtx.currentTime, this.nextPlayAt);
    src.start(at);
    this.nextPlayAt = at + audioBuf.duration;

    this.chunksPlayed += 1;
    if (this.chunksPlayed === 1) {
      // Stage 8, streaming flavour: the moment the reply becomes audible.
      logTtsPlaybackStart({
        streaming: true,
        sessionId: this.sessionId,
        sinceStreamOpenMs: Math.round(performance.now() - this.openedAt),
        chunkBytes: buf.byteLength,
        sampleRate: this.sampleRate,
      });
    }

    // Sentences arrive one at a time and each schedules its own chunk, so a
    // naive "onended per chunk" would flicker speaking on/off between every
    // sentence. Instead: report speaking=true on the first chunk, then keep
    // pushing the "stop speaking" timer out to cover whatever's currently
    // scheduled -- it only actually fires once nothing new has arrived by
    // the time the last scheduled chunk finishes.
    if (!this.speaking) {
      this.speaking = true;
      this.onSpeaking?.(true);
    }
    if (this.speakingEndTimer) clearTimeout(this.speakingEndTimer);
    const untilDoneMs = Math.max(0, (this.nextPlayAt - this.audioCtx.currentTime) * 1000) + 60;
    this.speakingEndTimer = setTimeout(() => {
      this.speaking = false;
      this.onSpeaking?.(false);
    }, untilDoneMs);
  }

  /** True once any audio has actually been played this turn. Lets a caller
   *  decide whether falling back to REST would double-speak. */
  get hasPlayed() {
    return this.chunksPlayed > 0;
  }

  close() {
    this.deliberateClose = true;
    if (this.speakingEndTimer) {
      clearTimeout(this.speakingEndTimer);
      this.speakingEndTimer = null;
    }
    if (this.speaking) {
      this.speaking = false;
      this.onSpeaking?.(false);
    }
    try {
      this.ws?.close(1000);
    } catch {
      /* noop */
    }
  }

  // Barge-in: hard interrupt -- unlike close(), kills audio immediately
  // instead of letting already-scheduled chunks finish, and tells the server
  // to stop generating more for this turn.
  stop() {
    logTts('hard stop (barge-in)', { sessionId: this.sessionId });
    // Tell the server to stop first, BEFORE flipping `stopped`, since
    // _enqueue drops sends once stopped. Best-effort: the protocol has no
    // confirmed cancel message, so this may simply be ignored server-side.
    this._enqueue({ session_id: this.sessionId, cancel: true });
    // From here on ignore everything: late frames already in the receive buffer
    // must NOT recreate an AudioContext and keep playing after the close.
    this.stopped = true;
    if (this.ws) this.ws.onmessage = null; // stop handling frames immediately
    this.close();
    try {
      if (this.audioCtx && this.audioCtx.state !== 'closed') this.audioCtx.close();
    } catch {
      /* noop */
    }
    this.audioCtx = null;
    this.nextPlayAt = 0;
  }
}

export default TtsSentenceStream;
