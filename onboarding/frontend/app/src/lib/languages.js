// Canonical language code -> display name map (matches the backend's
// LANGUAGE_NAMES). The FinGuru agent takes the code as its `language` argument
// (e.g. "language": "ta").
export const LANGUAGE_NAMES = {
  ta: 'தமிழ் (Tamil)',
  hi: 'हिन्दी (Hindi)',
  te: 'తెలుగు (Telugu)',
  kn: 'ಕನ್ನಡ (Kannada)',
  ml: 'മലയാളം (Malayalam)',
  mr: 'मराठी (Marathi)',
  bn: 'বাংলা (Bengali)',
  gu: 'ગુજરાતી (Gujarati)',
  pa: 'ਪੰਜਾਬੀ (Punjabi)',
  en: 'English',
};

// Codes offered in the picker for now (add more from LANGUAGE_NAMES to expand).
export const ENABLED_LANGUAGES = ['en', 'ta', 'hi'];

export const DEFAULT_LANGUAGE = 'en';

// STT's detected_language can arrive as a code ("ta"), a locale ("ta-IN"), or a
// full English name ("Tamil"/"tamil"). Map any of those to a canonical code, or
// null if it's not one we recognize.
const LANGUAGE_NAME_TO_CODE = {
  tamil: 'ta',
  hindi: 'hi',
  telugu: 'te',
  kannada: 'kn',
  malayalam: 'ml',
  marathi: 'mr',
  bengali: 'bn',
  gujarati: 'gu',
  punjabi: 'pa',
  english: 'en',
};

export function resolveLanguageCode(value) {
  if (!value) return null;
  const v = String(value).trim().toLowerCase();
  if (!v) return null;
  const base = v.split(/[-_]/)[0]; // "ta-in" -> "ta"
  if (LANGUAGE_NAMES[base]) return base;
  return LANGUAGE_NAME_TO_CODE[v] || LANGUAGE_NAME_TO_CODE[base] || null;
}

// Best-effort human-readable label for whatever STT (or script detection,
// see detectScriptLanguage below) reported -- resolves to our own display
// name when we recognize it, otherwise falls back to the raw value itself
// (capitalized) so an unmapped language still shows as SOMETHING rather than
// silently vanishing from the "isn't supported yet" message.
export function languageDisplayName(raw) {
  if (!raw) return null;
  const code = resolveLanguageCode(raw);
  if (code && LANGUAGE_NAMES[code]) return LANGUAGE_NAMES[code];
  const s = String(raw).trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : null;
}

// The one message shown everywhere (voice STT and typed text alike) when the
// user's language isn't one of ENABLED_LANGUAGES yet. Includes the detected
// language when known, per the "show which language got detected" spec --
// falls back to the generic wording when nothing could be identified.
export function unsupportedLanguageMessage(detectedLabel) {
  return detectedLabel
    ? `Detected ${detectedLabel} — this language isn't supported yet. Support will be added soon. Try English, Tamil, or Hindi.`
    : "This language isn't supported yet — support will be added soon. Try English, Tamil, or Hindi.";
}

// Unicode script ranges for the Indic languages we recognize but don't (yet)
// serve, keyed by the same codes as LANGUAGE_NAMES. Used to catch unsupported
// languages in TYPED text, which never goes through STT's own language
// detection -- Devanagari (Hindi) and Tamil script are deliberately absent
// here since both are already-enabled languages, and Latin script (English,
// or romanized Hindi/Tamil/Hinglish, all explicitly supported per the
// agent's own prompt) is treated as supported by omission.
const SCRIPT_RANGES = [
  [0x0c00, 0x0c7f, 'te'], // Telugu
  [0x0c80, 0x0cff, 'kn'], // Kannada
  [0x0d00, 0x0d7f, 'ml'], // Malayalam
  [0x0980, 0x09ff, 'bn'], // Bengali
  [0x0a80, 0x0aff, 'gu'], // Gujarati
  [0x0a00, 0x0a7f, 'pa'], // Punjabi (Gurmukhi)
];

// Returns a language code if `text` is clearly written in a script we
// recognize but don't yet support, or null if it looks supported/ambiguous
// (Latin script, Devanagari, Tamil script, or nothing script-like at all).
export function detectScriptLanguage(text) {
  if (!text) return null;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    const hit = SCRIPT_RANGES.find(([start, end]) => cp >= start && cp <= end);
    if (hit) return hit[2];
  }
  return null;
}

// Devanagari and Tamil block ranges for the two Indic scripts this app DOES
// serve -- the counterpart to SCRIPT_RANGES above, which deliberately omits
// them. Used as a fallback when STT's own detected-language field is missing
// or unreliable (observed with Sarvam's live-call transcript.final event,
// which doesn't always carry `language`): the transcript text itself is
// still a reliable signal, so a Tamil-script utterance need not silently
// default to English just because STT stayed quiet about the language.
const SUPPORTED_SCRIPT_RANGES = [
  [0x0900, 0x097f, 'hi'], // Devanagari
  [0x0b80, 0x0bff, 'ta'], // Tamil
];

// Best-effort language code from script alone, covering only the two Indic
// scripts this app supports (Tamil, Devanagari/Hindi). Returns null for
// Latin/English or anything else -- callers should treat null as "STT's word
// is all there is", not as "this is English".
export function detectSupportedScriptLanguage(text) {
  if (!text) return null;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    const hit = SUPPORTED_SCRIPT_RANGES.find(([start, end]) => cp >= start && cp <= end);
    if (hit) return hit[2];
  }
  return null;
}
