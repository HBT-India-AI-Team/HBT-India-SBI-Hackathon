import axios from 'axios';
import { logSttSent, logSttReceived, logTtsSent, logTtsReceived, debugError, debugWarn } from '../lib/pipelineLog';
import { normalizeForTTS } from '../lib/ttsText';

// Talks to the reference voice server (voice_ai_server_client) via the
// same-origin Vite proxy declared in vite.config.js. The proxy attaches the
// Bearer token and rewrites /voice-api -> <server>/voice, so there is no CORS
// preflight and no API key in the browser bundle.
const VOICE_BASE = '/voice-api';

// Prompt 7 (fallback UI): lightweight probe used to auto-retry the voice
// pipeline in the background while the user is on the text fallback, so they
// can be told voice is back without needing to reload or manually re-check.
export async function checkVoiceHealth() {
  try {
    const { data } = await axios.get(`${VOICE_BASE}/health`, { timeout: 5000 });
    return data?.status === 'ready';
  } catch {
    return false;
  }
}

// FinGuru reports language as a plain name ("English"/"Hindi"/"Tamil"); the
// voice server's /synthesize wants a short code.
const LANG_CODE = {
  english: 'en',
  hindi: 'hi',
  tamil: 'ta',
  telugu: 'te',
  kannada: 'kn',
  malayalam: 'ml',
  marathi: 'mr',
  bengali: 'bn',
  gujarati: 'gu',
  punjabi: 'pa',
  odia: 'or',
  urdu: 'ur',
};

export function languageNameToCode(name, fallback = 'en') {
  if (!name) return fallback;
  return LANG_CODE[String(name).trim().toLowerCase()] || fallback;
}

// Speech reads plainly, so drop the light markdown the agent returns.
export function stripMarkdownForSpeech(text) {
  return (text || '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[*-]\s+/gm, '')
    .replace(/`+/g, '')
    .replace(/\n{2,}/g, '. ')
    .trim();
}

/** POST a recorded WAV to /transcribe. Returns { text, language, latency_ms }. */
export async function transcribeAudio(blob, language) {
  const form = new FormData();
  form.append('file', blob, 'speech.wav');
  if (language) form.append('language', language);

  logSttSent({ language: language || null, audioBytes: blob?.size ?? null });

  const startedAt = performance.now();
  try {
    const { data } = await axios.post(`${VOICE_BASE}/transcribe`, form);
    logSttReceived({
      ms: Math.round(performance.now() - startedAt),
      language: data?.language,
      detectedLanguage: data?.detected_language,
      textLength: (data?.text || '').length,
      textPreview: (data?.text || '').slice(0, 120),
    });
    return data;
  } catch (err) {
    logSttReceived({
      ms: Math.round(performance.now() - startedAt),
      error: err?.response?.status || err?.message,
    });
    debugError('[Voice] ✗ transcribe failed', {
      status: err?.response?.status,
      detail: err?.response?.data || err?.message,
    });
    throw err;
  }
}

/** POST text to /synthesize. Returns a WAV Blob. */
export async function synthesizeText(text, language = 'en') {
  // Defense in depth (same cleanup the streaming path applies per sentence):
  // strip markdown, then run the shared TTS normalizer over the whole reply so
  // the one-shot FinGuru / non-streaming Ollama paths get the same treatment.
  const stripped = stripMarkdownForSpeech(text);
  const cleaned = normalizeForTTS(stripped);
  if (cleaned !== stripped.trim()) {
    debugWarn('[TTS] normalized non-compliant reply before synthesize', { before: stripped, after: cleaned });
  }

  logTtsSent({ language, textLength: cleaned.length, textPreview: cleaned.slice(0, 120) });

  const startedAt = performance.now();
  try {
    const res = await axios.post(
      `${VOICE_BASE}/synthesize`,
      { text: cleaned, language },
      { responseType: 'blob' }
    );
    logTtsReceived({
      ms: Math.round(performance.now() - startedAt),
      audioBytes: res?.data?.size ?? null,
    });
    return res.data;
  } catch (err) {
    logTtsReceived({ ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[Voice] ✗ synthesize failed', { status: err?.response?.status, detail: err?.message });
    throw err;
  }
}

// --- single-slot audio playback for spoken replies ---
let currentAudio = null;

export function playAudioBlob(blob, { onEnd } = {}) {
  stopAudio();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  const done = () => {
    URL.revokeObjectURL(url);
    if (currentAudio === audio) currentAudio = null;
    if (onEnd) onEnd();
  };
  audio.onended = done;
  audio.onerror = done;
  currentAudio = audio;
  audio.play().catch(done);
  return audio;
}

export function stopAudio() {
  if (currentAudio) {
    try {
      currentAudio.pause();
    } catch {
      /* noop */
    }
    currentAudio = null;
  }
}
