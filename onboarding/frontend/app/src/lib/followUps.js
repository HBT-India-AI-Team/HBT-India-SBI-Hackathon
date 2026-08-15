// Recommended follow-up questions (text mode only). Neither backend brain
// has a dedicated structured-output channel for this: FinGuru is a hosted
// agent with a fixed evidence schema (question/history/style/voice/language/
// name -- see api/finguru.js) we don't control the server side of, and
// Ollama's system prompt is shared with the voice pipeline. So this is
// implemented purely via an instruction appended to the outgoing question,
// with the reply parsed back apart client-side -- verified directly against
// a real Ollama response, which followed the format exactly. If a brain
// ignores the instruction, extractFollowUps just finds nothing and the
// reply renders normally with no suggestions -- never a hard failure.
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
