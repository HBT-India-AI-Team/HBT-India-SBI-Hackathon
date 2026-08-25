import axios from 'axios';
import { logSttSent, logSttReceived, logTtsSent, logTtsReceived, debugError } from '../lib/pipelineLog';
import { apiPath } from '../lib/basePath';

// Direct calls to Sarvam's own API (proxied same-origin via /sarvam-api so
// the subscription key stays server-side, see vite.config.js) -- NOT routed
// through the voice server. Its outbound path to api.sarvam.ai fails
// (corporate TLS-inspection proxy on that machine rejects Sarvam's cert
// chain); the browser's own network has no such interception.
const SARVAM_BASE = apiPath('/sarvam-api');

// This app's 2-letter codes (see lib/languages.js) -> Sarvam's required
// BCP-47 codes. Only covers codes LANGUAGE_NAMES actually defines.
const SARVAM_LANG_CODE = {
  en: 'en-IN',
  ta: 'ta-IN',
  hi: 'hi-IN',
  te: 'te-IN',
  kn: 'kn-IN',
  ml: 'ml-IN',
  mr: 'mr-IN',
  bn: 'bn-IN',
  gu: 'gu-IN',
  pa: 'pa-IN',
};

export function toSarvamLangCode(code, fallback = 'en-IN') {
  if (!code) return fallback;
  return SARVAM_LANG_CODE[String(code).trim().toLowerCase()] || fallback;
}

function base64ToBlob(base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/**
 * POST a recorded WAV straight to Sarvam's batch STT (saaras:v3). Always
 * requests auto language detection ("unknown") rather than hinting the
 * currently-selected language, so the detected language (echoed back in
 * `language_code`) genuinely reflects what was heard -- callers use it to
 * update the language picker and drive the reply's TTS language.
 * Returns { text, language, detected_language }.
 */
export async function sarvamTranscribe(blob) {
  const form = new FormData();
  form.append('file', blob, 'speech.wav');
  form.append('model', 'saaras:v3');
  form.append('language_code', 'unknown');

  logSttSent({ provider: 'sarvam', language: 'auto', audioBytes: blob?.size ?? null });
  const startedAt = performance.now();
  try {
    const { data } = await axios.post(`${SARVAM_BASE}/speech-to-text`, form);
    logSttReceived({
      ms: Math.round(performance.now() - startedAt),
      language: data?.language_code,
      detectedLanguage: data?.language_code,
      textLength: (data?.transcript || '').length,
      textPreview: (data?.transcript || '').slice(0, 120),
    });
    return { text: data?.transcript || '', language: data?.language_code, detected_language: data?.language_code };
  } catch (err) {
    logSttReceived({ ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[Sarvam] ✗ transcribe failed', { status: err?.response?.status, detail: err?.response?.data || err?.message });
    throw err;
  }
}

/** POST text straight to Sarvam's TTS (bulbul:v2). Returns a WAV Blob. */
export async function sarvamSynthesize(text, language) {
  logTtsSent({ provider: 'sarvam', language, textLength: text.length, textPreview: text.slice(0, 120) });
  const startedAt = performance.now();
  try {
    const { data } = await axios.post(`${SARVAM_BASE}/text-to-speech`, {
      text,
      language_code: toSarvamLangCode(language),
      model: 'bulbul:v2',
      speaker: 'anushka',
    });
    const b64 = data?.audios?.[0];
    if (!b64) throw new Error('Sarvam TTS returned no audio');
    const blob = base64ToBlob(b64, 'audio/wav');
    logTtsReceived({ ms: Math.round(performance.now() - startedAt), audioBytes: blob.size });
    return blob;
  } catch (err) {
    logTtsReceived({ ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[Sarvam] ✗ synthesize failed', { status: err?.response?.status, detail: err?.response?.data || err?.message });
    throw err;
  }
}
