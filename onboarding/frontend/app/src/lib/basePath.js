// Where this app is mounted, and how to build same-origin URLs against it.
//
// The app is not always served from the domain root. One deployment serves it
// at `/onboarding-v2/`, and every same-origin path in this codebase used to be
// written root-relative -- `new URL('/tts-ws', location.href)`, `'/voice-api'`,
// `VITE_FINGURU_URL=/agents/finguru/invoke`. Those resolve against the ORIGIN,
// not the app, so under a sub-path they all pointed at the wrong place:
// `/tts-ws` instead of `/onboarding-v2/tts-ws`. The server answered with the
// SPA's index.html rather than a 404, so the failure looked like a hang
// ("Connecting…" forever) instead of a missing route.
//
// BASE_URL is set by Vite from `base` in vite.config.js and always ends in a
// slash ('/' when mounted at the root). Everything below is derived from it, so
// a single `APP_BASE` env var moves the whole app -- dev proxy included, since
// vite.config.js prefixes its proxy routes with the same value.

/** App base, without the trailing slash. '' when mounted at the domain root. */
export const BASE = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');

/**
 * A same-origin path, mounted under the app's base.
 * apiPath('/voice-api') -> '/voice-api' at the root, '/onboarding-v2/voice-api' under a sub-path.
 */
export function apiPath(route) {
  const path = route.startsWith('/') ? route : `/${route}`;
  return `${BASE}${path}`;
}

/**
 * The same, as an absolute ws:// or wss:// URL -- matching the page's own
 * protocol so an https page never opens an insecure socket (browsers block it).
 */
export function wsUrl(route, params) {
  const u = new URL(apiPath(route), window.location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  if (params) for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  return u.toString();
}

/**
 * Mount an operator-configured URL under the base when it is same-origin.
 * An absolute URL (http://, https://) is returned untouched -- pointing at a
 * different host is a deliberate choice and must not be rewritten.
 */
export function resolveConfiguredUrl(value, fallback = '') {
  const raw = (value ?? '').trim() || fallback;
  if (!raw) return raw;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) return raw;   // absolute: leave alone
  return apiPath(raw);
}
