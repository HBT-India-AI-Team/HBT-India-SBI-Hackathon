import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

// Name-based session identity for FinGuru (per finguru-memory-frontend-spec.md,
// summarized in the implementation prompt -- the spec file itself wasn't
// found on disk, so this follows the prompt's own detailed requirements).
//
// The `name` IS the session key -- no separate random session id is generated.
// Priority: URL ?name= (Moneyverse game hand-off) -> localStorage -> prompt.
export const FINGURU_NAME_KEY = 'finguru_user_name';

function readStoredName() {
  try {
    const v = localStorage.getItem(FINGURU_NAME_KEY);
    return v && v.trim() ? v.trim() : null;
  } catch {
    return null; // localStorage unavailable (private mode etc.)
  }
}

function storeName(name) {
  try {
    localStorage.setItem(FINGURU_NAME_KEY, name);
  } catch {
    /* noop */
  }
}

// This app uses HashRouter, so react-router's useSearchParams() only sees a
// query string written AFTER the hash (".../#/finguru?name=X"). A hand-off
// link built the more common way (".../?name=X#/finguru") puts it on the
// actual page URL instead -- check that too so either convention works.
function readPreHashName() {
  try {
    return (new URLSearchParams(window.location.search).get('name') || '').trim();
  } catch {
    return '';
  }
}

/**
 * Resolves the current user's name (URL param wins and is persisted
 * immediately; otherwise whatever's in localStorage) and exposes a setter
 * for the name-prompt flow. `name` is null until resolved -- callers should
 * render the "what should I call you?" prompt while it's null.
 */
export function useFinGuruName() {
  const [searchParams] = useSearchParams();
  const urlName = (searchParams.get('name') || '').trim() || readPreHashName();

  const [name, setNameState] = useState(() => {
    if (urlName) {
      storeName(urlName); // hand-off from the game persists on first sight
      return urlName;
    }
    return readStoredName();
  });

  const setName = useCallback((n) => {
    const trimmed = (n || '').trim();
    if (!trimmed) return;
    storeName(trimmed);
    setNameState(trimmed);
  }, []);

  return { name, setName };
}
