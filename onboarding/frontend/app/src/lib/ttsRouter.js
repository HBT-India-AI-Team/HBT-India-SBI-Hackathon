// Client-side port of the backend's TTS provider router (now that the
// frontend calls Sarvam directly instead of going through the voice
// server -- see api/sarvam.js): any digit -- account numbers, PAN, IFSC
// codes, currency amounts, OTPs, dates, whatever -- is read more reliably
// by Sarvam's cloned voice than the faster local Parler-TTS (still served
// by the voice server -- that path never touched Sarvam, so it's untouched
// by this change). Previously this checked a handful of specific patterns
// (PAN/IFSC/account-number/amount/alnum-ID regexes); every one of those
// already implied a digit was present, so the specific patterns added
// nothing a plain digit check doesn't already cover.
const DIGIT_RE = /\d/;

export function needsSarvamTts(text) {
  return DIGIT_RE.test(text || '');
}
