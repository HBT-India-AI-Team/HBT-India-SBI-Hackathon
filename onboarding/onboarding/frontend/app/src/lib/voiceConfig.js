// Voice assistant mode, set via VITE_VOICE_MODE in .env.
//   'turn' = record -> /transcribe -> askFinGuru -> /synthesize -> play
//   'live' = full-duplex WebSocket call to the voice server's /call endpoint
export const VOICE_MODE =
  (import.meta.env.VITE_VOICE_MODE || 'turn').trim().toLowerCase() === 'live' ? 'live' : 'turn';

export const isLiveVoice = VOICE_MODE === 'live';

// When true (and in live mode), skip routing the transcript through FinGuru --
// play the voice server's own reply instead (the built-in echo hook). Useful
// for testing the raw voice pipeline. Set VITE_VOICE_SKIP_FINGURU=true.
export const SKIP_FINGURU_IN_VOICE =
  (import.meta.env.VITE_VOICE_SKIP_FINGURU || 'false').trim().toLowerCase() === 'true';

// Prompt 6 (barge-in): allow the user to interrupt assistant speech by
// talking again during Speaking. Set VITE_BARGE_IN_ENABLED=true to enable;
// when false (default), a transcript arriving while the assistant is
// speaking is ignored -- the existing full-turn-based behavior.
export const BARGE_IN_ENABLED = (import.meta.env.VITE_BARGE_IN_ENABLED || 'false').trim().toLowerCase() === 'true';

// Sensitivity: minimum transcript length (characters) required to count as a
// deliberate interruption rather than a cough/false VAD trigger. NOTE: the
// voice server's `transcript` message carries no duration/confidence field,
// so text length is the only signal available client-side to approximate
// "minimum speech duration" -- a real duration/confidence value from the
// server would make this far more reliable. Configurable via
// VITE_BARGE_IN_MIN_CHARS.
export const BARGE_IN_MIN_CHARS = Number(import.meta.env.VITE_BARGE_IN_MIN_CHARS) || 4;
