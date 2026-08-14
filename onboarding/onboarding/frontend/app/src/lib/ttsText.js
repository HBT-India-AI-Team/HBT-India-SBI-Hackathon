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

/**
 * Last-mile cleanup for a single sentence chunk before it goes to TTS. Pure and
 * idempotent -- safe to call on already-clean text. Returns the cleaned string.
 *
 * @param {string} text
 * @returns {string}
 */
export function normalizeForTTS(text) {
  if (!text) return '';
  let out = String(text);
  out = out.replace(LEADING_LIST_MARKER_RE, '');   // drop a leading "1." / "- " / "• "
  out = out.replace(BRACKET_CHARS_RE, '');          // "(EMI)" -> "EMI"
  out = out.replace(NON_SPEECH_SYMBOLS_RE, '');     // strip #, *, _, |, ~, etc.
  out = out.replace(/\s+/g, ' ').trim();            // collapse whitespace/newlines
  if (!out) return '';
  // If stripping removed the trailing punctuation (or the LLM never added it),
  // re-append a period so the chunk is a complete spoken unit.
  if (!SENTENCE_END_RE.test(out)) out += '.';
  return out;
}
