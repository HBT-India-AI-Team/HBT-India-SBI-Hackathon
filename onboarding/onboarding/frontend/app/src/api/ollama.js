import axios from 'axios';
import { LANGUAGE_NAMES } from '../lib/languages';
import { logLlmSent, logLlmReceived, debugWarn, debugError } from '../lib/pipelineLog';
import { buildFinGuruSystemPrompt } from '../lib/ttsText';

// Direct connection to an Ollama server (no proxy). Used as a fallback "brain"
// for the voice pipeline (STT -> Ollama -> TTS) when the FinGuru server is down.
//
// NOTE: because this is a direct browser -> Ollama call, the Ollama server must
// allow this page's origin via its OLLAMA_ORIGINS setting (e.g. OLLAMA_ORIGINS=*).
export const OLLAMA_ENABLED = (import.meta.env.VITE_OLLAMA_ENABLED || 'false').trim().toLowerCase() === 'true';
export const OLLAMA_URL = (import.meta.env.VITE_OLLAMA_URL || 'http://localhost:11434').replace(/\/+$/, '');
export const OLLAMA_MODEL = (import.meta.env.VITE_OLLAMA_MODEL || 'llama3.2').trim();

// When true (and OLLAMA_ENABLED), voice questions are answered by Ollama
// instead of FinGuru -- flip this on when the FinGuru server is unavailable.
export const USE_OLLAMA_IN_VOICE =
  (import.meta.env.VITE_USE_OLLAMA_IN_VOICE || 'false').trim().toLowerCase() === 'true';

// Sentence-level LLM -> TTS streaming gate (see ollamaStream.js). Scoped
// entirely to the Ollama direct-call fallback path (SKIP_FINGURU_IN_VOICE &&
// USE_OLLAMA_IN_VOICE) -- that's the only path where the frontend calls Ollama
// itself, so it's the only path where client-side sentence-chunking applies.
// Independent of USE_OLLAMA_IN_VOICE otherwise: this only decides STREAMING vs
// ONE-SHOT for however Ollama's reply gets spoken -- default false leaves the
// existing one-shot "full response -> one /synthesize call" flow untouched.
export const LLM_STREAMING_ENABLED =
  (import.meta.env.VITE_LLM_STREAMING_ENABLED || 'false').trim().toLowerCase() === 'true';

// Sentence-boundary character class used to split LLM output for streaming
// TTS forwarding, e.g. "[.!?।]" (Tamil/Devanagari danda included). Falls back
// to the default on an invalid pattern rather than throwing at load time.
const DEFAULT_SENTENCE_SPLIT = '[.!?।]';
export const LLM_SENTENCE_SPLIT_REGEX = (() => {
  const src = (import.meta.env.VITE_LLM_SENTENCE_SPLIT_REGEX || DEFAULT_SENTENCE_SPLIT).trim();
  try {
    // eslint-disable-next-line no-new
    new RegExp(src); // validate before handing it back
    return src;
  } catch {
    debugWarn('[Ollama] invalid LLM_SENTENCE_SPLIT_REGEX, falling back to default', src);
    return DEFAULT_SENTENCE_SPLIT;
  }
})();

/**
 * Ask the local/remote Ollama model. Mirrors askFinGuru's return shape so it's
 * a drop-in for the chat pipeline: { text, language, confidence, hitl, error }.
 * `history` is an array of { role: 'user'|'assistant', content } turns.
 * `options.name` is the resolved session identity, included on the request
 * body when present (see lib/finguruIdentity.js) -- Ollama itself ignores it.
 */
export async function askOllama(question, history = [], options = {}) {
  const { language, colloquial = false, name, voice = false } = options;
  const langName = LANGUAGE_NAMES[language] || 'English';
  // forTTS only when the answer is bound for speech (voice), so typed chat
  // keeps normal formatting (lists, parentheses) while spoken replies get the
  // TTS-friendly rules appended.
  const system = buildFinGuruSystemPrompt({ langName, colloquial, forTTS: !!voice });

  const messages = [{ role: 'system', content: system }, ...history, { role: 'user', content: question }];

  logLlmSent({ engine: 'Ollama', model: OLLAMA_MODEL, question, language: langName, colloquial });

  const startedAt = performance.now();
  try {
    const { data } = await axios.post(`${OLLAMA_URL}/api/chat`, {
      model: OLLAMA_MODEL,
      messages,
      stream: false,
      ...(name ? { name } : {}),
    });
    const text = data?.message?.content || '';
    logLlmReceived({
      engine: 'Ollama',
      ms: Math.round(performance.now() - startedAt),
      model: data?.model,
      textLength: text.length,
      textPreview: text.slice(0, 120),
    });
    return {
      text,
      language: langName,
      confidence: null,
      hitl: { triggered: false, reasons: [] },
      error: null,
    };
  } catch (err) {
    logLlmReceived({ engine: 'Ollama', ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[Ollama] ✗ request failed', {
      status: err?.response?.status,
      detail: err?.response?.data || err?.message,
    });
    throw err;
  }
}
