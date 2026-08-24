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

// How much audio to bank before letting any of it out of the speakers.
//
// Starting on the very first chunk sounds broken, and measurably so rather
// than as a matter of taste: the server's first chunk carries ~0.33s of audio
// while the next arrives ~0.41s later, so playback runs dry for ~80ms right
// after it -- a silence landing mid-word. From the third chunk on each one
// carries ~0.5s and arrives every ~0.4s, so the cushion grows by ~100ms a
// chunk and the problem never recurs. It is purely a start-up deficit, and a
// small bank of audio absorbs it along with whatever jitter the tunnel adds.
//
// The cost is honest: this delays first sound by roughly the prebuffer. Even
// so the path lands near 1.5s against REST's 6.0s, and smooth beats early.
const PREBUFFER_MS = Number(import.meta.env.VITE_TTS_PREBUFFER_MS) || 900;
// A reply can be shorter than the prebuffer (a one-line answer), in which case
// waiting to fill it would never resolve. Start anyway once this much time has
// passed since the first chunk landed.
const PREBUFFER_MAX_WAIT_MS = 1500;
// Schedule the first buffer a hair into the future rather than exactly at
// currentTime, so the initial start() is not racing the audio clock.
const SCHEDULE_LEAD_SEC = 0.02;

// How long after the last audio chunk to keep a finished turn's socket alive
// before closing it.
//
// This is not tidiness. The server does NOT close the socket when it is sent
// `done: true` -- verified directly, it stays OPEN -- so a turn that only
// calls finish() leaks one connection per message. They accumulate on a box
// whose GPU is shared with Ollama, and the symptom is the second and every
// later message in a conversation stalling or timing out while the first was
// fine. Closing once the audio has drained is what keeps a long conversation
// costing one connection rather than one per turn.
const CLOSE_IDLE_MS = 5000;

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
    this.queue = [];            // decoded AudioBuffers banked, not yet scheduled
    this.bufferedSec = 0;       // audio in `queue`, in seconds
    this.playbackStarted = false;
    this.prebufferTimer = null;
    this.closeWatchdog = null;
    this.underruns = 0;
    this.firstChunkBytes = null;
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
    // The server leaves the socket open after `done`, so closing is on us.
    this._armCloseWatchdog();
  }

  // Close the socket once the turn is genuinely over: finished, no audio still
  // arriving, and nothing still playing. Re-armed by every late chunk and
  // while playback drains, so it can never cut a reply short.
  _armCloseWatchdog() {
    if (!this.finished || this.stopped || this.deliberateClose) return;
    if (this.closeWatchdog) clearTimeout(this.closeWatchdog);
    this.closeWatchdog = setTimeout(() => {
      if (this.stopped || this.deliberateClose) return;
      if (this.speaking || this.queue.length) {
        this._armCloseWatchdog(); // still draining -- check again later
        return;
      }
      logTts('turn complete -- closing tts-ws', {
        sessionId: this.sessionId,
        chunksPlayed: this.chunksPlayed,
        underruns: this.underruns,
      });
      this.close();
    }, CLOSE_IDLE_MS);
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
      this._armCloseWatchdog(); // push the close out; audio is still arriving
    }
  }

  // Decode one int16 PCM frame into an AudioBuffer and bank it. Playback does
  // not necessarily start here -- see _maybeBeginPlayback.
  _play(buf) {
    if (this.stopped) return; // never resurrect playback on a new context after stop()
    if (!this.audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new Ctx();
      this.nextPlayAt = this.audioCtx.currentTime;
    }
    const view = new DataView(buf);
    const n = Math.floor(buf.byteLength / 2);
    if (n === 0) return;
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const s = view.getInt16(i * 2, true);
      f32[i] = s / (s < 0 ? 0x8000 : 0x7fff);
    }
    const audioBuf = this.audioCtx.createBuffer(1, n, this.sampleRate);
    audioBuf.copyToChannel(f32, 0);

    this.queue.push(audioBuf);
    this.bufferedSec += audioBuf.duration;
    if (this.firstChunkBytes === null) this.firstChunkBytes = buf.byteLength;

    if (this.playbackStarted) {
      this._scheduleQueued();
      return;
    }
    this._maybeBeginPlayback();
  }

  // Hold the first chunks back until there is enough banked to survive the
  // start-up deficit, then release everything at once and stay released.
  _maybeBeginPlayback() {
    if (this.bufferedSec * 1000 >= PREBUFFER_MS) {
      this._beginPlayback('prebuffer full');
      return;
    }
    if (!this.prebufferTimer) {
      // Short reply: the buffer may never fill, so start on a deadline.
      this.prebufferTimer = setTimeout(
        () => this._beginPlayback('prebuffer deadline'),
        PREBUFFER_MAX_WAIT_MS
      );
    }
  }

  _beginPlayback(reason) {
    if (this.stopped || this.playbackStarted) return;
    if (this.prebufferTimer) {
      clearTimeout(this.prebufferTimer);
      this.prebufferTimer = null;
    }
    this.playbackStarted = true;
    logTts('playback starting', {
      sessionId: this.sessionId,
      reason,
      bankedMs: Math.round(this.bufferedSec * 1000),
    });
    this.nextPlayAt = this.audioCtx.currentTime + SCHEDULE_LEAD_SEC;
    this._scheduleQueued();
  }

  // Schedule every banked buffer back-to-back on the audio clock. Because each
  // one starts exactly where the previous ended, the result is sample-contiguous
  // regardless of how unevenly the chunks arrived over the network.
  _scheduleQueued() {
    while (this.queue.length) {
      const audioBuf = this.queue.shift();
      this.bufferedSec -= audioBuf.duration;
      const src = this.audioCtx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(this.audioCtx.destination);

      let at = this.nextPlayAt;
      if (at < this.audioCtx.currentTime) {
        // The bank ran dry -- generation fell behind playback. Restart from
        // now; a seam is audible but it is the least-bad option, and worth
        // logging since a recurring one means the prebuffer is too small.
        this.underruns += 1;
        logTts('underrun -- audio bank ran dry', {
          sessionId: this.sessionId,
          underruns: this.underruns,
          behindMs: Math.round((this.audioCtx.currentTime - at) * 1000),
        });
        at = this.audioCtx.currentTime;
      }
      src.start(at);
      this.nextPlayAt = at + audioBuf.duration;

      this.chunksPlayed += 1;
      if (this.chunksPlayed === 1) {
        // Stage 8, streaming flavour: the moment the reply becomes audible.
        logTtsPlaybackStart({
          streaming: true,
          sessionId: this.sessionId,
          sinceStreamOpenMs: Math.round(performance.now() - this.openedAt),
          prebufferMs: PREBUFFER_MS,
          firstChunkBytes: this.firstChunkBytes,
          sampleRate: this.sampleRate,
        });
      }
    }

    // Sentences arrive one at a time and each schedules its own chunk, so a
    // naive "onended per chunk" would flicker speaking on/off between every
    // sentence. Instead: report speaking=true once, then keep pushing the
    // "stop speaking" timer out to cover whatever's currently scheduled -- it
    // only actually fires once nothing new has arrived by the time the last
    // scheduled chunk finishes.
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
    // Anything still banked was never scheduled -- release it rather than
    // silently swallowing the tail of the reply.
    if (!this.stopped && this.queue.length) {
      if (!this.playbackStarted) this._beginPlayback('closing with audio still banked');
      else this._scheduleQueued();
    }
    if (this.prebufferTimer) {
      clearTimeout(this.prebufferTimer);
      this.prebufferTimer = null;
    }
    if (this.closeWatchdog) {
      clearTimeout(this.closeWatchdog);
      this.closeWatchdog = null;
    }
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
    // Drop the bank too: close() deliberately skips flushing once `stopped` is
    // set, so anything still queued must be discarded rather than spoken over
    // whatever the barge-in started.
    this.queue = [];
    this.bufferedSec = 0;
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
