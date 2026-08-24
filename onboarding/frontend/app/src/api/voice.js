import axios from 'axios';
import { logTtsSent, logTtsReceived, logTtsPlaybackStart, debugError, debugWarn } from '../lib/pipelineLog';
import { normalizeForTTS } from '../lib/ttsText';
import { sarvamTranscribe, sarvamSynthesize } from './sarvam';
import { needsSarvamTts } from '../lib/ttsRouter';

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

// STT is exclusively Sarvam now (saaras:v3-realtime for live calls, saaras:v3
// for turn-mode), called DIRECTLY (see api/sarvam.js) rather than through the
// voice server's /stt/sarvam wrapper -- that wrapper's outbound path to
// api.sarvam.ai fails (corporate TLS-inspection proxy on that machine
// rejects Sarvam's cert chain); the browser's own network has no such
// interception. The voice server's local /transcribe route stays available
// on the backend for internal batch/corpus tooling, just never called here.
/** Transcribe a recorded WAV via Sarvam directly (auto language detection). Returns { text, language, detected_language }. */
export async function transcribeAudio(blob) {
  return sarvamTranscribe(blob);
}

/**
 * TTS provider selection: a client-side port of the backend's regex router
 * (see lib/ttsRouter.js) -- text with account numbers/PAN/IFSC/amounts/IDs
 * goes to Sarvam directly (same reasoning as STT: never through the voice
 * server); everything else uses the voice server's local Parler-TTS via
 * /synthesize, unaffected by the Sarvam SSL issue since it never leaves that
 * machine. Returns { blob, provider }.
 */
export async function synthesizeText(text, language = 'en') {
  // Defense in depth (same cleanup the streaming path applies per sentence):
  // strip markdown, then run the shared TTS normalizer over the whole reply so
  // the one-shot FinGuru / non-streaming Ollama paths get the same treatment.
  const stripped = stripMarkdownForSpeech(text);
  const cleaned = normalizeForTTS(stripped, language);
  if (cleaned !== stripped.trim()) {
    debugWarn('[TTS] normalized non-compliant reply before synthesize', { before: stripped, after: cleaned });
  }

  if (needsSarvamTts(cleaned)) {
    const blob = await sarvamSynthesize(cleaned, language);
    return { blob, provider: 'sarvam' };
  }

  logTtsSent({ provider: 'local', language, textLength: cleaned.length, textPreview: cleaned.slice(0, 120) });
  const startedAt = performance.now();
  try {
    const res = await axios.post(`${VOICE_BASE}/synthesize`, { text: cleaned, language }, { responseType: 'blob' });
    logTtsReceived({ ms: Math.round(performance.now() - startedAt), audioBytes: res?.data?.size ?? null });
    return { blob: res.data, provider: 'local' };
  } catch (err) {
    logTtsReceived({ ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[Voice] ✗ synthesize failed', { status: err?.response?.status, detail: err?.message });
    throw err;
  }
}

// --- single-slot audio playback for spoken replies ---
let currentAudio = null;

export function playAudioBlob(blob, { onEnd, label } = {}) {
  stopAudio();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  const queuedAt = performance.now();
  const done = () => {
    URL.revokeObjectURL(url);
    if (currentAudio === audio) currentAudio = null;
    if (onEnd) onEnd();
  };
  audio.onended = done;
  audio.onerror = done;
  // 'playing' (not 'play') -- fires when audio is genuinely audible, after any
  // buffering, rather than when playback was merely requested.
  audio.onplaying = () => {
    logTtsPlaybackStart({
      ...(label ? { label } : {}),
      startDelayMs: Math.round(performance.now() - queuedAt),
      durationSec: Number.isFinite(audio.duration) ? Math.round(audio.duration * 10) / 10 : null,
      audioBytes: blob?.size ?? null,
    });
  };
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
