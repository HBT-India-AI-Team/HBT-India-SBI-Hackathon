// Recommended follow-up questions -- the OLLAMA half of the story only.
//
// FinGuru no longer needs any of this: `follow_ups` is a first-class field of
// its output schema (see api/finguru.js and api/finguruStream.js), so the
// model is constrained to fill it, it arrives already in the user's language,
// and it lives outside `content` -- meaning a spoken reply physically cannot
// read the suggestion list aloud. Prefer that field whenever it is populated.
//
// Ollama has no structured-output channel (its system prompt is shared with
// the voice pipeline), so for that brain the suggestions are still requested
// as an instruction appended to the outgoing question and parsed back apart
// client-side -- verified against a real Ollama response, which followed the
// format exactly. If a brain ignores the instruction, extractFollowUps finds
// nothing and the reply renders normally with no suggestions: never a hard
// failure.
//
// The marker must never go out on a turn whose reply will be SPOKEN -- it
// puts the list inside the reply text, which is the same text that gets
// synthesized. Callers gate on that (see FinGuruChat's `wantMarker`).
// extractFollowUps still runs whenever the instruction was sent, because its
// other job is stripping a marker block out of displayed text if a brain
// emits one anyway.
const FOLLOWUP_MARKER = '###FOLLOWUPS###';
const MAX_FOLLOWUPS = 3;

export function withFollowUpInstruction(question) {
  return `${question}\n\n(After answering, on a new line write exactly "${FOLLOWUP_MARKER}" followed by up to ${MAX_FOLLOWUPS} short, natural follow-up questions the user might ask next, one per line, no numbering or bullets. If none are relevant, omit the marker and the list entirely.)`;
}

/** Splits a raw reply into { answer, followUps } -- strips the marker block out of the displayed text. */
export function extractFollowUps(text) {
  const raw = text || '';
  const idx = raw.indexOf(FOLLOWUP_MARKER);
  if (idx === -1) return { answer: raw.trim(), followUps: [] };
  const answer = raw.slice(0, idx).trim();
  const followUps = raw
    .slice(idx + FOLLOWUP_MARKER.length)
    .split('\n')
    .map((line) => line.replace(/^[-*\d.\s]+/, '').trim())
    .filter(Boolean)
    .slice(0, MAX_FOLLOWUPS);
  return { answer: answer || raw.trim(), followUps };
}
