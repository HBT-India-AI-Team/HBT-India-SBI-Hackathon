import { LANGUAGE_NAMES } from '../lib/languages';
import { OLLAMA_URL, OLLAMA_MODEL, LLM_SENTENCE_SPLIT_REGEX } from './ollama';
import { logLlmSent, logLlmReceived, logTtsSent, debugLog, debugWarn } from '../lib/pipelineLog';
import { buildFinGuruSystemPrompt, normalizeForTTS } from '../lib/ttsText';
import { TtsSentenceStream } from './ttsStream';

// Streaming Ollama -> sentence-level TTS forwarding. Active ONLY in the
// Ollama-fallback voice path (see FinGuruChat, gated on VITE_VOICE_SKIP_FINGURU
// && VITE_USE_OLLAMA_IN_VOICE).
//
// The TTS half now lives in api/ttsStream.js, because the FinGuru path uses it
// too -- it is no longer Ollama-only. Its endpoint, once assumed, is confirmed
// deployed; see that file for the measured latency and the wire protocol.

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
