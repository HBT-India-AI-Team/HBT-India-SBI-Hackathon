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
