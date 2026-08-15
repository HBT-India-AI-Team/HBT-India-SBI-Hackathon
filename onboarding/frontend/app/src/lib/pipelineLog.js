// The ONLY front-end logging that's on by default. Everything else that used
// to print (per-request/response diagnostics previously scattered across the
// api/ modules and hooks) is now gated behind VITE_DEBUG_LOGS (default off)
// via debugLog/debugWarn/debugError below, so the console stays limited to
// exactly these 7 voice-pipeline stages, each timestamped:
//   1. mic starts recording       5. FinGuru/LLM responds
//   2. sent to STT                6. response sent to TTS
//   3. STT response received      7. TTS response received (multiple if streaming)
//   4. sent to LLM/FinGuru
//
// "Sent"/"received" fire once per turn for non-streaming pipelines (turn-mode
// REST STT/TTS, one-shot FinGuru/Ollama); the streaming-Ollama path forwards
// TTS sentence-by-sentence, so stages 6/7 legitimately fire multiple times
// there -- that's real per-chunk activity, not log spam.

const DEBUG_LOGS = (import.meta.env.VITE_DEBUG_LOGS || 'false').trim().toLowerCase() === 'true';

// Pull the function name out of one stack frame line, covering both the
// V8/Chrome ("    at name (url)") and Firefox/Safari ("name@url") shapes.
function parseFrameName(line) {
  let m = line.match(/^\s*at\s+(?:async\s+)?(.+?)\s+\(/); // V8 named frame
  if (m) return m[1];
  m = line.match(/^\s*([^@\s]+)@/); // Firefox/Safari named frame
  return m ? m[1] : '';
}

// Best-effort name of whoever called one of the log functions below. Walks the
// stack and returns the first frame that isn't inside this file (skips stage(),
// the logXxx() wrapper, and this helper). Robust across browsers' differing
// frame indices; degrades to 'anonymous' if names are stripped (e.g. a minified
// production build) -- this is a dev/diagnostic aid, not load-bearing.
function callerName() {
  const lines = (new Error().stack || '').split('\n');
  for (const line of lines) {
    if (!/\bat\b|@/.test(line)) continue; // skip the "Error" header line
    if (line.includes('pipelineLog')) continue; // skip our own frames
    return parseFrameName(line) || 'anonymous';
  }
  return 'anonymous';
}

function stage(icon, label, detail) {
  // eslint-disable-next-line no-console
  console.log(`${icon} [${new Date().toISOString()}] ${label} · ${callerName()}()`, detail ?? '');
}

export function logMicStart(detail) {
  stage('🎙️', 'Mic recording started', detail);
}
export function logSttSent(detail) {
  stage('📤', 'Sent to STT', detail);
}
export function logSttReceived(detail) {
  stage('📥', 'STT response received', detail);
}
export function logLlmSent(detail) {
  stage('📤', 'Sent to LLM/FinGuru', detail);
}
export function logLlmReceived(detail) {
  stage('📥', 'LLM/FinGuru responded', detail);
}
export function logTtsSent(detail) {
  stage('📤', 'Sent to TTS', detail);
}
export function logTtsReceived(_detail) {
  // Intentionally disabled: TTS-received logging (stage 7) is off for now.
  // Re-enable by uncommenting and restoring the `detail` param name.
  // stage('📥', 'TTS response received', _detail);
}

// Everything else. Off by default -- set VITE_DEBUG_LOGS=true to restore the
// full verbose per-file diagnostic logging used while building this pipeline.
export function debugLog(...args) {
  if (DEBUG_LOGS) console.log(...args); // eslint-disable-line no-console
}
export function debugWarn(...args) {
  if (DEBUG_LOGS) console.warn(...args); // eslint-disable-line no-console
}
export function debugError(...args) {
  if (DEBUG_LOGS) console.error(...args); // eslint-disable-line no-console
}
