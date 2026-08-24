import { FINGURU_STREAM_URL, finguruHeaders } from './finguru';
import { synthesizeText, playAudioBlob, stripMarkdownForSpeech } from './voice';
import { TtsSentenceStream } from './ttsStream';
import { normalizeForTTS } from '../lib/ttsText';
import { logLlmSent, logLlmReceived, logTtsSent, debugLog, debugError } from '../lib/pipelineLog';

// Streaming FinGuru -> sentence-level TTS. The counterpart to
// api/ollamaStream.js's streamOllamaToTTS, for the path that actually goes
// through the FinGuru backend.
//
// Why this exists: on the one-shot path a voice turn is
//   [wait for the whole answer: 12-95s] -> [synthesize the whole reply] -> play
// so nothing is audible until the model has finished writing. Measured on the
// live backend, the first sentence lands in ~2-4s. Speaking it while the rest
// is still being written is the difference between a usable voice assistant
// and a long silence.
//
// ONE IMPORTANT DIFFERENCE from the Ollama path: sentence boundaries are NOT
// computed here. The backend emits them, deliberately -- its answers are money,
// and "Rs 1,06,398.02" split on the full stop becomes "Rs 1,06,398." and "02",
// two utterances, the first a wrong number spoken to someone who cannot see the
// screen. So this consumes `sentence` events rather than re-splitting text.
// (The Ollama path has to split client-side because it streams raw tokens.)
//
// HOW THE AUDIO GETS OUT: streaming WS first, REST only as a fallback.
//
// Each sentence goes to the voice server's WS /tts/stream (api/ttsStream.js),
// which returns PCM chunks as it generates them instead of one WAV at the end.
// Measured on the same sentence through the tunnel:
//
//   REST /synthesize   6.0s before any audio
//   WS   /tts/stream   0.8s to the first chunk
//
// The socket is opened at the START of the turn, before the first sentence
// exists, so its ~1.2s connect happens while the LLM is still writing rather
// than on the critical path.
//
// If the socket never opens (streaming disabled server-side, endpoint
// unreachable), TtsSentenceStream calls back and we fall back to the REST
// SentenceSpeaker below, replaying every sentence received so far. The
// fallback only ever fires when NOTHING has been played, so it cannot
// double-speak a reply that was already partly audible.
//
// A previous version cut the opening sentence at a comma to shorten the first
// REST synthesis. Streaming makes that pointless -- the first chunk arrives
// long before a whole sentence is synthesized either way -- and it cost an
// extra round trip, so it is gone.
//
// NOTE: the WS path is local Parler-TTS only. lib/ttsRouter.js's Sarvam
// routing does not apply to it, which currently changes nothing, since that
// router is switched to always-local.

/**
 * Plays synthesized sentences strictly in order, while allowing the next one
 * to be synthesized during playback of the current one.
 *
 * The ordering matters more than it looks: synthesizeText resolves at wildly
 * different speeds depending on which provider a sentence routed to, so
 * "play whichever finishes first" would reorder the answer. Blobs are stored
 * by sentence index and only ever played from the front of the queue.
 */
class SentenceSpeaker {
  constructor({ onFirstProvider, onSpeakingChange } = {}) {
    this.onFirstProvider = onFirstProvider;   // (provider) -> set the TTS badge
    this.onSpeakingChange = onSpeakingChange; // (bool)
    this.blobs = new Map();   // index -> blob, awaiting its turn
    this.nextToPlay = 0;
    this.received = 0;
    this.playing = false;
    this.stopped = false;
    this.streamEnded = false;
    this.reportedProvider = false;
    // Sentences arrive in a burst (often <1s apart) but must be SENT to TTS
    // one at a time, not fired concurrently: measured directly against the
    // local voice server, 3 concurrent /synthesize calls each degraded from
    // a solo ~10s to ~30s and then failed outright ("server is currently
    // offline") -- it has no real request queue, just one worker. Chaining
    // each push() off this promise means sentence N+1's request doesn't go
    // out until sentence N's has resolved (or failed), regardless of how
    // fast the sentences themselves arrive.
    this._synthChain = Promise.resolve();
  }

  /** Queue one sentence for synthesis; playback order is preserved
   *  regardless of which provider answers first, and requests are sent to
   *  TTS strictly one at a time (see _synthChain above). */
  push(text, language) {
    if (this.stopped || !text) return;
    const index = this.received++;
    this._synthChain = this._synthChain.then(() => this._synthesizeOne(index, text, language));
  }

  async _synthesizeOne(index, text, language) {
    if (this.stopped) return;
    try {
      const { blob, provider } = await synthesizeText(text, language);
      if (this.stopped) return;
      if (!this.reportedProvider) {
        // The badge reports the provider that actually spoke first. A later
        // sentence may route differently (an amount mid-answer flips to
        // Sarvam); showing the first is honest and stable, and matches what
        // the non-streaming path shows.
        this.reportedProvider = true;
        this.onFirstProvider?.(provider);
      }
      logTtsSent({ streaming: true, seq: index + 1, provider, textLength: text.length, textPreview: text.slice(0, 80) });
      this.blobs.set(index, blob);
      this._drain();
    } catch (err) {
      // A failed sentence must not stall every sentence behind it: record an
      // empty slot so the queue can step over it.
      debugError('[FinGuruStream] synthesize failed for one sentence', { index, detail: err?.message });
      this.blobs.set(index, null);
      this._drain();
    }
  }

  _drain() {
    if (this.stopped || this.playing) return;
    const blob = this.blobs.get(this.nextToPlay);
    if (blob === undefined) return;          // not synthesized yet -- wait
    this.blobs.delete(this.nextToPlay);
    this.nextToPlay += 1;
    if (blob === null) { this._drain(); return; }   // failed sentence, skip

    this.playing = true;
    if (this.nextToPlay === 1) this.onSpeakingChange?.(true);
    playAudioBlob(blob, {
      label: `sentence ${this.nextToPlay}`,
      onEnd: () => {
        this.playing = false;
        if (this.stopped) return;
        if (this.blobs.has(this.nextToPlay)) { this._drain(); return; }
        // Nothing queued. Only report "done speaking" once the stream itself
        // has ended -- otherwise a gap while the next sentence synthesizes
        // would flicker the UI out of its speaking state and back.
        if (this.streamEnded && this.nextToPlay >= this.received) this.onSpeakingChange?.(false);
      },
    });
  }

  /** The stream produced no more sentences. */
  end() {
    this.streamEnded = true;
    if (!this.playing && this.nextToPlay >= this.received) this.onSpeakingChange?.(false);
  }

  /** Barge-in / cancel: drop everything, including audio already synthesized. */
  stop() {
    this.stopped = true;
    this.blobs.clear();
    this.playing = false;
    this.onSpeakingChange?.(false);
  }
}

/**
 * Ask FinGuru and speak each sentence as the backend finishes writing it.
 *
 * Returns the same shape as askFinGuru ({ text, language, confidence, hitl,
 * error }) -- taken from the `done` event, which the backend documents as the
 * authoritative reply, with the sentences being an early preview of it. So the
 * text that gets stored and displayed is exactly what /invoke would have
 * returned; only the audio starts earlier.
 *
 * @param {string} question
 * @param {Array} history
 * @param {{ colloquial?: boolean, language?: string, name?: string,
 *           onFirstProvider?: (p:string)=>void, onSpeakingChange?: (b:boolean)=>void,
 *           onSentence?: (t:string)=>void, signal?: AbortSignal }} options
 */
export async function askFinGuruStreaming(question, history = [], options = {}) {
  const {
    colloquial = false, language, name,
    onFirstProvider, onSpeakingChange, onSentence, signal,
  } = options;

  const evidence = {
    question,
    history,
    style: colloquial,
    voice: true,                     // this path only exists for spoken turns
    ...(language ? { language } : {}),
    ...(name ? { name } : {}),
  };

  // --- audio out: streaming WS, with the REST speaker held in reserve ---
  //
  // Opened NOW, before the request is even sent, so the socket is connected by
  // the time the first sentence arrives ~5-7s later. That turns its ~1.2s
  // connect from latency the user waits through into time that was going to be
  // spent waiting for the LLM anyway.
  const ttsLang = language || 'en';
  const sessionId = `fg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const spokenSoFar = [];        // cleaned sentences, for replay if the WS never opens
  let speaker = null;            // REST fallback, built only if it is actually needed
  let usingRestFallback = false;

  const startRestFallback = () => {
    if (usingRestFallback) return;
    usingRestFallback = true;
    debugLog('[FinGuruStream] tts-ws unavailable -- falling back to REST synthesis');
    speaker = new SentenceSpeaker({ onFirstProvider, onSpeakingChange });
    for (const s of spokenSoFar) speaker.push(s, ttsLang);   // nothing was audible yet, so this cannot double-speak
  };

  const ttsStream = new TtsSentenceStream(sessionId, ttsLang, onSpeakingChange, startRestFallback, name);
  // The WS is local Parler-TTS only, so the badge is known up front rather
  // than discovered per sentence the way the REST router required.
  onFirstProvider?.('local');

  // One handle for "stop making noise", whichever path is live.
  const stopAllAudio = () => {
    ttsStream.stop();
    speaker?.stop();
  };
  // Text must be cleaned the same way synthesizeText() cleans it -- the WS
  // path bypasses that function, so markdown would otherwise be spoken.
  const speakSentence = (text) => {
    const cleaned = normalizeForTTS(stripMarkdownForSpeech(text));
    if (!cleaned) return;
    spokenSoFar.push(cleaned);
    if (usingRestFallback) speaker.push(cleaned, ttsLang);
    else ttsStream.sendSentence(cleaned);
  };

  if (signal) {
    if (signal.aborted) { stopAllAudio(); return { text: '', language: null, interrupted: true }; }
    signal.addEventListener('abort', stopAllAudio, { once: true });
  }

  logLlmSent({ engine: 'FinGuru(stream)', question, style: colloquial, voice: true, language: language || null });
  const startedAt = performance.now();
  let firstSentenceAtMs = null;
  let sentenceCount = 0;
  let final = null;

  try {
    const res = await fetch(FINGURU_STREAM_URL, {
      method: 'POST',
      headers: finguruHeaders(),
      body: JSON.stringify({ evidence }),
      signal,
    });
    if (!res.ok || !res.body) throw new Error(`finguru stream HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // Server-sent events: records are separated by a blank line, and each
    // carries one "data: <json>" line.
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const record = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const line = record.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let msg;
        try {
          msg = JSON.parse(line.slice(5).trim());
        } catch {
          continue;                    // a partial or non-JSON frame
        }

        if (msg.event === 'sentence' && msg.text) {
          sentenceCount += 1;
          if (firstSentenceAtMs === null) {
            firstSentenceAtMs = Math.round(performance.now() - startedAt);
            debugLog(`[FinGuruStream] first sentence at ${firstSentenceAtMs}ms`);
          }
          onSentence?.(msg.text);
          logTtsSent({ streaming: true, seq: sentenceCount, provider: usingRestFallback ? 'local(rest)' : 'local(ws)', textLength: msg.text.length, textPreview: msg.text.slice(0, 80) });
          // Fire-and-forget: the WS send returns immediately, so reading the
          // SSE stream is never blocked behind synthesis.
          speakSentence(msg.text);
        } else if (msg.event === 'done') {
          final = msg;
        } else if (msg.event === 'error') {
          throw new Error(msg.message || 'FinGuru stream error');
        }
      }
    }

    // End of turn. finish() lets the server know no more text is coming; the
    // audio already queued keeps playing and draining after this returns.
    if (usingRestFallback) speaker.end();
    else ttsStream.finish();
    const output = final?.output || {};
    logLlmReceived({
      engine: 'FinGuru(stream)',
      ms: Math.round(performance.now() - startedAt),
      firstSentenceMs: firstSentenceAtMs,
      sentences: sentenceCount,
      language: output.language || null,
      textLength: (output.content || '').length,
      textPreview: (output.content || '').slice(0, 120),
    });

    return {
      text: output.content || '',
      language: output.language || null,
      confidence: output.confidence,
      hitl: final?.hitl || { triggered: false, reasons: [] },
      error: null,
      // The `done` event carries the same tool suggestions /invoke returns.
      tools: Array.isArray(final?.tools) ? final.tools : [],
      followUps: Array.isArray(output.follow_ups) ? output.follow_ups : [],
      // True when audio already played -- the caller must NOT then speak the
      // reply again through the one-shot path.
      spoken: sentenceCount > 0,
      sessionId: final?.session_id || null,
    };
  } catch (err) {
    stopAllAudio();
    if (err?.name === 'AbortError' || signal?.aborted) {
      return { text: '', language: null, interrupted: true, spoken: false };
    }
    logLlmReceived({ engine: 'FinGuru(stream)', ms: Math.round(performance.now() - startedAt), error: err?.message });
    debugError('[FinGuruStream] ✗ stream failed', { detail: err?.message });
    throw err;
  }
}
