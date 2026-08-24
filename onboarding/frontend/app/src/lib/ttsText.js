// Text preparation for the voice pipeline. Two concerns, both pure/isolated so
// they can be unit-tested directly (see ttsText.test.js):
//   1. buildFinGuruSystemPrompt() -- the LLM system prompt the frontend sends
//      on the Ollama paths (direct + streaming). Optionally appends the
//      TTS-friendly formatting rules when the response is bound for speech.
//   2. normalizeForTTS() -- a defensive last-mile cleanup applied to every
//      sentence chunk immediately before it's sent to TTS, regardless of which
//      LLM produced it (Ollama or FinGuru), in case the LLM ignored the rules.

// The formatting rules block, appended to the system prompt for speech-bound
// replies. Kept verbatim from the product spec so wording can be tuned in one
// place. FinGuru owns its own system prompt server-side (the frontend only
// sends `evidence`, not a system prompt), so these rules are NOT applied to
// FinGuru from here -- that prompt must be updated on the FinGuru backend
// separately. normalizeForTTS() below is the frontend's safety net for both.
export const TTS_FORMATTING_RULES = `

When generating responses that will be converted to speech (TTS), follow these formatting rules strictly:
- Never use bullet points, numbered lists, or list markers of any kind (no -, •, 1., 2., etc.). Convert any list into flowing spoken sentences instead, e.g. instead of "1. Check your balance 2. Review the alert" say "First, check your balance. Then, review the alert."
- Never use brackets or parentheses. If a clarifying detail is needed, weave it into the sentence directly instead of setting it apart.
- Every sentence must end with proper punctuation (., !, or ? — use । for Tamil sentence endings) so it can be correctly identified as a complete spoken unit.
- English words are fine when there's no natural Tamil equivalent (e.g. bank names, product names, technical terms like "loan", "EMI", "KYC") — keep these as standalone English words within the Tamil sentence structure, but do not switch entire clauses or sentences into English.
- Avoid special characters or formatting marks entirely (no #, *, _, |, etc.) — write as if dictating naturally to a person, not writing a document.`;

/**
 * Builds the FinGuru persona system prompt used on the Ollama call paths.
 * @param {{ langName?: string, colloquial?: boolean, forTTS?: boolean }} opts
 *   forTTS=true appends TTS_FORMATTING_RULES (use for speech-bound replies;
 *   leave false for typed chat so lists/formatting stay allowed there).
 */
export function buildFinGuruSystemPrompt({ langName = 'English', colloquial = false, forTTS = false } = {}) {
  const base =
    `You are FinGuru, a helpful India-context personal finance assistant. Give accurate, ` +
    `concise, practical answers for an Indian user (amounts in rupees, Indian products and ` +
    `government schemes). Reply in ${langName}.` +
    (colloquial ? ' Use everyday, colloquial, conversational language.' : '') +
    ' Keep answers short enough to be comfortably read aloud.';
  return forTTS ? base + TTS_FORMATTING_RULES : base;
}

// A leading list marker at the very start of a chunk: "-", "•", "*", "1.",
// "2)", etc. Requires trailing whitespace so a decimal like "3.5 lakh" (no
// space after the dot) is never mistaken for a numbered-list marker.
const LEADING_LIST_MARKER_RE = /^\s*(?:[-•*]|\d+[.)])\s+/;

// Bracket/parenthesis characters to drop while KEEPING their inner text, so
// "(EMI)" becomes "EMI" rather than being removed entirely.
const BRACKET_CHARS_RE = /[()[\]{}]/g;

// Other non-speech formatting symbols to strip outright. Deliberately excludes
// speakable punctuation (. , ! ? ; : ' " % ₹) and the intra-word hyphen/slash
// (e-KYC, and/or) so real words survive; only markup-ish characters go.
const NON_SPEECH_SYMBOLS_RE = /[#*_|~`^<>\\]/g;

// Valid sentence-final punctuation, including the Devanagari/Tamil danda.
const SENTENCE_END_RE = /[.!?।]$/;

// --- numbers -> words, for the local TTS model ---------------------------
//
// The local Parler-TTS voice reads raw digits badly. Measured against the live
// server on the same sentence: "3,71,392.25 rupees over 38 months" is 56
// characters and produced 7.22s of audio (129 ms/char), while the same amount
// written as words is 86 characters -- half again as long -- and produced only
// 5.71s (66 ms/char). Twice the time per character means the model is labouring
// over each digit rather than speaking a number, which is what makes amounts
// sound wrong and drags the whole sentence out of its normal cadence.
//
// The original design avoided this by routing anything with a digit to Sarvam,
// whose voice reads Indian amounts natively. With TTS forced local that route
// is closed, so the expansion has to happen here instead.
//
// ENGLISH ONLY, deliberately. Expanding a number into English words inside a
// Hindi or Tamil sentence would splice two languages together mid-clause --
// exactly the defect this is meant to remove. For every other language the
// digits are left untouched, which is no worse than before.

const _ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
  'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen',
  'eighteen', 'nineteen'];
const _TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];

function _under100(n) {
  if (n < 20) return _ONES[n];
  const t = _TENS[Math.floor(n / 10)];
  return n % 10 ? `${t} ${_ONES[n % 10]}` : t;
}

/** Indian numbering: crore / lakh / thousand / hundred, not millions. */
function indianNumberToWords(n) {
  if (!Number.isFinite(n)) return '';
  if (n === 0) return 'zero';
  const parts = [];
  const crore = Math.floor(n / 10000000);
  if (crore) {
    // Recurse so "125 crore" reads correctly rather than overflowing the table.
    parts.push(`${indianNumberToWords(crore)} crore`);
    n %= 10000000;
  }
  const lakh = Math.floor(n / 100000);
  if (lakh) { parts.push(`${_under100(lakh)} lakh`); n %= 100000; }
  const thousand = Math.floor(n / 1000);
  if (thousand) { parts.push(`${_under100(thousand)} thousand`); n %= 1000; }
  const hundred = Math.floor(n / 100);
  if (hundred) { parts.push(`${_ONES[hundred]} hundred`); n %= 100; }
  if (n) parts.push(_under100(n));
  return parts.join(' ');
}

// A number as it actually appears in these replies: an optional currency mark,
// Indian-grouped digits, an optional decimal, an optional percent sign.
// The whitespace before `%` lives INSIDE that optional group on purpose. With
// it outside, a plain number swallowed the space that followed it and welded
// itself to the next word -- "10,000 on" spoke as "ten thousand rupeeson".
const NUMBER_RE = /(₹|Rs\.?|INR)?\s?(\d[\d,]*)(?:\.(\d+))?(\s*%)?/g;
// Long unbroken digit runs are identifiers -- account numbers, mobile numbers,
// reference numbers. Nobody wants those as one enormous word, so they stay
// digit-by-digit, which is also how a person reads them aloud.
const ID_DIGITS_MIN = 9;

function spellNumberMatch(currency, intRaw, decRaw, percent) {
  const digits = intRaw.replace(/,/g, '');
  if (!/^\d+$/.test(digits)) return null;

  // Identifier, not a quantity: read it out digit by digit.
  if (!currency && !decRaw && !percent && !intRaw.includes(',') && digits.length >= ID_DIGITS_MIN) {
    return digits.split('').map((d) => _ONES[Number(d)]).join(' ');
  }
  if (digits.length > 15) return null;          // beyond crore-of-crores: leave it alone

  let out = indianNumberToWords(Number(digits));
  const isMoney = Boolean(currency);
  if (decRaw) {
    if (isMoney && decRaw.length <= 2) {
      // Money decimals are paise, and "and twenty five paise" is how the
      // amount is actually said -- "point two five rupees" is not.
      const paise = Number(decRaw.padEnd(2, '0'));
      if (paise) out += ` rupees and ${_under100(paise)} paise`;
      else out += ' rupees';
      return percent ? `${out} percent` : out;
    }
    out += ` point ${decRaw.split('').map((d) => _ONES[Number(d)]).join(' ')}`;
  }
  if (isMoney) out += ' rupees';
  if (percent) out += ' percent';
  return out;
}

/**
 * Rewrites digits as spoken English words. Applied only when the reply is in
 * English -- see the note above. Pure; safe to call on text with no numbers.
 */
export function spellNumbersForSpeech(text) {
  if (!text) return '';
  return String(text).replace(NUMBER_RE, (match, currency, intRaw, decRaw, percent) => {
    const spoken = spellNumberMatch(currency, intRaw, decRaw, percent);
    if (spoken === null) return match;
    // Preserve whatever separated this from the previous word.
    const lead = /^\s/.test(match) ? ' ' : '';
    return lead + spoken;
  });
}

/**
 * Last-mile cleanup for a single sentence chunk before it goes to TTS. Pure and
 * idempotent -- safe to call on already-clean text. Returns the cleaned string.
 *
 * @param {string} text
 * @returns {string}
 */
export function normalizeForTTS(text, language) {
  if (!text) return '';
  let out = String(text);
  out = out.replace(LEADING_LIST_MARKER_RE, '');   // drop a leading "1." / "- " / "• "
  out = out.replace(BRACKET_CHARS_RE, '');          // "(EMI)" -> "EMI"
  out = out.replace(NON_SPEECH_SYMBOLS_RE, '');     // strip #, *, _, |, ~, etc.
  // English only -- spelling a number into English words inside a Hindi or
  // Tamil sentence would be worse than the digits it replaces. Callers that
  // pass no language get the old behaviour, digits untouched.
  if (language && String(language).trim().toLowerCase().startsWith('en')) {
    out = spellNumbersForSpeech(out);
  }
  out = out.replace(/\s+/g, ' ').trim();            // collapse whitespace/newlines
  if (!out) return '';
  // If stripping removed the trailing punctuation (or the LLM never added it),
  // re-append a period so the chunk is a complete spoken unit.
  if (!SENTENCE_END_RE.test(out)) out += '.';
  return out;
}
