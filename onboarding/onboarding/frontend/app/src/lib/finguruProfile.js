// FinGuru has no login/auth of its own (it works pre-onboarding). To scope
// chat history "by user/session id so histories don't bleed across logins"
// as Prompt 3 asks, we use a persisted anonymous per-browser profile id --
// the closest available boundary without a real FinGuru account system.
// Same localStorage pattern as AppContext.jsx.
const KEY = 'finguru.profileId';

export function getProfileId() {
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = (typeof crypto !== 'undefined' && crypto.randomUUID && crypto.randomUUID()) || `p-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return 'default'; // localStorage unavailable (private mode etc.)
  }
}
