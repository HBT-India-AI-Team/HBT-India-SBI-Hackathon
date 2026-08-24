// TTS provider router. All speech now goes through the local voice server's
// Parler-TTS, never Sarvam -- Sarvam TTS routing (previously triggered by
// any digit in the reply, for more reliable reading of account numbers/PAN/
// IFSC/amounts/OTPs) is disabled.
export function needsSarvamTts(_text) {
  return false;
}
