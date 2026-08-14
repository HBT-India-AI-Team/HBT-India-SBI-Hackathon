import { LANGUAGE_NAMES } from '../lib/languages';
import { OLLAMA_URL, OLLAMA_MODEL, LLM_SENTENCE_SPLIT_REGEX } from './ollama';
import { logLlmSent, logLlmReceived, logTtsSent, logTtsReceived, debugLog, debugWarn } from '../lib/pipelineLog';
import { buildFinGuruSystemPrompt, normalizeForTTS } from '../lib/ttsText';

// Streaming Ollama -> sentence-level TTS forwarding. Active ONLY in the
// Ollama-fallback voice path (see FinGuruChat, gated on VITE_VOICE_SKIP_FINGURU
// && VITE_USE_OLLAMA_IN_VOICE). The FinGuru routing path never touches this.
//
// TTS endpoint contract (Prompt 3 -- not yet deployed at time of writing):
//   Same-origin WS /tts-ws  (Vite proxies it to <voice-server>/voice/tts/stream
//   with the token attached; path configurable via VOICE_TTS_STREAM_PATH).
//   Per sentence we send JSON: { session_id, text, language }
//   At the end of a turn we send:  { session_id, done: true }
//   The server is expected to stream back int16 PCM audio chunks (binary),
//   optionally preceded by a JSON control frame carrying { sampling_rate }.
// Adjust the small `send`/message shape below if Prompt 3 finalizes differently.

function ttsWsUrl() {
  const u = new URL('/tts-ws', window.location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.toString();
}

// Prompt 4 (network resilience) for the per-turn TTS WS. Short/bounded since
// this connection is scoped to a single conversational turn, not long-lived.
const TTS_RECONNECT_DELAYS_MS = [500, 1500]; // 2 attempts

function logTts(msg, extra) {
  debugLog(`[OllamaTTS][${new Date().toISOString()}] ${msg}`, extra ?? '');
}

// Splits off the first complete sentence from `buf` using the configurable
// LLM_SENTENCE_SPLIT_REGEX boundary class (default "[.!?।]" -- Tamil/Devanagari
// danda included), returning { sentence, rest } or null if none yet.
const SENTENCE_BOUNDARY_RE = new RegExp(`^[\\s\\S]*?${LLM_SENTENCE_SPLIT_REGEX}`);
function takeSentence(buf) {
  const m = buf.match(SENTENCE_BOUNDARY_RE);
  if (!m) return null;
  return { sentence: m[0].trim(), rest: buf.slice(m[0].length) };
}

// A single TTS-stream WebSocket for one conversation turn. Sentences are sent
// as they complete (queued until the socket opens); returned PCM plays back.
class TtsSentenceStream {
  constructor(sessionId, language, onSpeaking, onFallbackNeeded, name) {
    this.sessionId = sessionId;
    this.language = language;
    this.name = name; // resolved session identity, included on every sentence sent (see lib/finguruIdentity.js)
    this.onSpeaking = onSpeaking; // (bool) -- coalesced across back-to-back sentence chunks
    // Prompt 3/streaming mismatch handling: fires ONCE, only if the socket
    // never successfully opened even after retries (e.g. TTS_STREAMING_ENABLED
    // is off on the GPU PC, or /tts/stream is unreachable/refused) -- lets the
    // caller fall back to the existing non-streaming REST /synthesize flow
    // instead of silently losing this turn's audio.
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
        // TODO(backend): there is no per-sentence ack in the assumed /tts/stream
        // contract, so we can only resend what never made it onto a socket
        // (this.pending) -- sentences already sent to the now-dead connection
        // may or may not have been synthesized server-side, and we have no way
        // to know. A real ack/resume protocol would remove this gap.
        if (this.reconnectAttempt >= TTS_RECONNECT_DELAYS_MS.length) {
          logTts('tts-ws reconnect attempts exhausted -- giving up on this turn\'s audio', {
            sessionId: this.sessionId,
            code: event.code,
            everConnected: this.everConnected,
          });
          // Only fall back if the connection NEVER worked this turn (a config
          // mismatch, e.g. GPU-side TTS_STREAMING_ENABLED=false, or the
          // endpoint being unreachable) -- if it worked and then dropped
          // mid-turn, some audio may already be playing, so restarting via
          // REST now would risk double-speaking part of the reply.
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
    this._enqueue({ session_id: this.sessionId, text, language: this.language, ...(this.name ? { name: this.name } : {}) });
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

  // Prompt 6 (barge-in): hard interrupt -- unlike close(), kills audio
  // immediately instead of letting already-scheduled chunks finish, and
  // tells the server to stop generating more for this turn.
  stop() {
    logTts('hard stop (barge-in)', { sessionId: this.sessionId });
    // Tell the server to stop first (best-effort -- see contract note below),
    // BEFORE flipping `stopped`, since _enqueue drops sends once stopped.
    // TODO(backend): the assumed /tts/stream contract (see file header) has
    // no confirmed cancel message -- this is a best-effort guess at its shape.
    this._enqueue({ session_id: this.sessionId, cancel: true });
    // From here on ignore everything: late frames already in the receive buffer
    // must NOT recreate an AudioContext and keep playing after the close.
    this.stopped = true;
    if (this.ws) this.ws.onmessage = null; // stop handling frames immediately, don't wait for close
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

/**
 * Stream a reply from Ollama and forward complete sentences to the TTS WS the
 * instant each one is ready (not waiting for the full response). Returns the
 * full text when the stream ends.
 *
 * @param {string} question
 * @param {{ language?: string, colloquial?: boolean, onToken?: (t:string)=>void, onSpeaking?: (b:boolean)=>void, signal?: AbortSignal, name?: string }} opts
 */
export async function streamOllamaToTTS(question, opts = {}) {
  const { language, colloquial = false, onToken, onSpeaking, signal, name } = opts;
  const sessionId =
    (typeof crypto !== 'undefined' && crypto.randomUUID && crypto.randomUUID()) ||
    `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const langName = LANGUAGE_NAMES[language] || 'English';

  // This path always feeds TTS, so the formatting rules are always appended.
  const system = buildFinGuruSystemPrompt({ langName, colloquial, forTTS: true });

  // Mismatch handling: if the TTS-ws never connects at all (e.g. Prompt 3's
  // TTS_STREAMING_ENABLED is off on the GPU PC, or /tts/stream is otherwise
  // unreachable/refused), stop treating this as a streaming turn -- keep
  // accumulating tokens as normal and let the caller speak the full response
  // through the existing non-streaming REST endpoint once the stream ends.
  let ttsWsFailed = false;
  const tts = new TtsSentenceStream(
    sessionId,
    language || 'en',
    onSpeaking,
    () => {
      ttsWsFailed = true;
      logTts('tts-ws never connected -- falling back to non-streaming REST synthesis for this turn', { sessionId });
    },
    name
  );
  // Prompt 6 (barge-in): aborting the signal hard-stops audio immediately and
  // (via `signal` passed to fetch below) cancels the in-flight Ollama request.
  if (signal) {
    if (signal.aborted) {
      tts.stop();
      return { text: '', sessionId, interrupted: true };
    }
    signal.addEventListener('abort', () => tts.stop(), { once: true });
  }
  const streamStart = performance.now();
  logLlmSent({ engine: 'Ollama(stream)', model: OLLAMA_MODEL, question, language: langName });
  logTts('stream start', { sessionId, model: OLLAMA_MODEL, language: langName });

  let buffer = '';
  let full = '';
  let sentenceCount = 0;

  const forward = (sentence) => {
    if (!sentence || ttsWsFailed) return; // nothing to send to once we've abandoned the ws
    // Defense in depth: clean each sentence right before it goes to TTS, in
    // case the LLM ignored the formatting rules in the system prompt.
    const cleaned = normalizeForTTS(sentence);
    if (!cleaned) return;
    if (cleaned !== sentence.trim()) {
      // The LLM didn't fully follow the TTS formatting prompt -- surface the
      // before/after (gated) so prompt compliance can be tracked/tuned.
      debugWarn('[TTS] normalized non-compliant chunk', { before: sentence, after: cleaned });
    }
    sentenceCount += 1;
    const latency = Math.round(performance.now() - streamStart);
    logTtsSent({ streaming: true, seq: sentenceCount, atMs: latency, text: cleaned });
    tts.sendSentence(cleaned);
  };

  // Retry the CONNECTION (not the generation) with backoff, but only while no
  // token has arrived yet -- once generation has started, retrying would
  // re-run the whole prompt and could produce duplicate spoken sentences with
  // no way to know what the (possibly still-alive) TTS side already got, so a
  // mid-stream drop is surfaced as an error instead (see `midStream` on the
  // thrown error; Prompt 7 decides what the user sees for that).
  const OLLAMA_CONNECT_RETRY_DELAYS_MS = [500, 1500, 3000];
  let connectAttempt = 0;
  let reader = null;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (signal?.aborted) return { text: full, sessionId, interrupted: true };
    try {
      const res = await fetch(`${OLLAMA_URL}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: OLLAMA_MODEL,
          messages: [{ role: 'system', content: system }, { role: 'user', content: question }],
          stream: true,
          ...(name ? { name } : {}),
        }),
        signal,
      });
      if (!res.ok || !res.body) throw new Error(`ollama stream HTTP ${res.status}`);
      reader = res.body.getReader();
      break; // connected
    } catch (err) {
      if (err?.name === 'AbortError') return { text: full, sessionId, interrupted: true };
      if (connectAttempt >= OLLAMA_CONNECT_RETRY_DELAYS_MS.length) {
        tts.close();
        logTts('could not connect to Ollama after retries', { sessionId, detail: err?.message });
        throw err;
      }
      const delay = OLLAMA_CONNECT_RETRY_DELAYS_MS[connectAttempt];
      connectAttempt += 1;
      logTts(`connect failed, retrying (${connectAttempt}/${OLLAMA_CONNECT_RETRY_DELAYS_MS.length}) in ${delay}ms`, err?.message);
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  try {
    const decoder = new TextDecoder();
    let ndjson = '';
    // Read NDJSON tokens; Ollama emits one JSON object per line.
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (signal?.aborted) return { text: full, sessionId, interrupted: true };
      const { done, value } = await reader.read();
      if (done) break;
      ndjson += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = ndjson.indexOf('\n')) >= 0) {
        const line = ndjson.slice(0, nl).trim();
        ndjson = ndjson.slice(nl + 1);
        if (!line) continue;
        let obj;
        try {
          obj = JSON.parse(line);
        } catch {
          continue;
        }
        const tok = obj?.message?.content || '';
        if (tok) {
          buffer += tok;
          full += tok;
          onToken?.(tok);
          // Flush every complete sentence sitting in the buffer.
          let taken;
          while ((taken = takeSentence(buffer)) !== null) {
            buffer = taken.rest;
            forward(taken.sentence);
          }
        }
      }
    }
    // Flush any trailing partial sentence at the end of the stream.
    forward(buffer.trim());
    logLlmReceived({
      engine: 'Ollama(stream)',
      ms: Math.round(performance.now() - streamStart),
      sentences: sentenceCount,
      textLength: full.length,
      textPreview: full.slice(0, 120),
    });
    logTts('stream done', {
      sessionId,
      totalMs: Math.round(performance.now() - streamStart),
      sentences: sentenceCount,
      chars: full.length,
      ttsFallbackNeeded: ttsWsFailed,
    });
    if (ttsWsFailed) {
      // The ws never worked this turn -- nothing was ever actually sent to
      // it, so there's nothing to finish(); the caller speaks `full` via the
      // existing non-streaming REST endpoint instead.
      return { text: full, sessionId, ttsFallbackNeeded: true };
    }
    tts.finish();
    return { text: full, sessionId };
  } catch (err) {
    tts.close();
    // A user-initiated Stop (AbortController.abort()) aborts the in-flight
    // reader.read() with an AbortError -- that's a clean interruption, not a
    // failure, so return `interrupted` instead of throwing into the error
    // fallback UI. (The loop-top signal check only catches aborts BETWEEN
    // reads, not one landing during a pending read.)
    if (err?.name === 'AbortError' || signal?.aborted) {
      return { text: full, sessionId, interrupted: true };
    }
    logTts('stream failed', { sessionId, detail: err?.message, tokensReceived: full.length > 0 });
    err.midStream = full.length > 0; // true = generation had already started when it broke
    err.partialText = full;
    throw err;
  }
}
