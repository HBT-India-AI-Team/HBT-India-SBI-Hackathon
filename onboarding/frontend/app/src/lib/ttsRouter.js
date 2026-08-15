// Client-side port of the backend's TTS provider router (now that the
// frontend calls Sarvam directly instead of going through the voice
// server -- see api/sarvam.js): text carrying account numbers, PAN, IFSC
// codes, currency amounts, or other alphanumeric IDs is read more reliably
// by Sarvam's cloned voice; everything else uses the faster local
// Parler-TTS (still served by the voice server -- that path never touched
// Sarvam, so it's untouched by this change).

const PAN_RE = /\b[A-Z]{5}\d{4}[A-Z]\b/;
const IFSC_RE = /\b[A-Z]{4}0[A-Z0-9]{6}\b/;
const ACCOUNT_NO_RE = /\b\d{9,18}\b/;
const AMOUNT_RE = /(?:₹|Rs\.?|INR)\s?\d[\d,]*(?:\.\d+)?/i;
// A general alphanumeric ID: 6+ chars mixing letters and digits (customer
// IDs, application refs, OTPs-with-context, etc.) -- deliberately broader
// than PAN/IFSC so novel ID formats still route to Sarvam.
const ALNUM_ID_RE = /\b(?=[A-Za-z0-9]{6,}\b)(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b/;

export function needsSarvamTts(text) {
  const t = (text || '').toUpperCase();
  return PAN_RE.test(t) || IFSC_RE.test(t) || ACCOUNT_NO_RE.test(t) || AMOUNT_RE.test(t) || ALNUM_ID_RE.test(t);
}
