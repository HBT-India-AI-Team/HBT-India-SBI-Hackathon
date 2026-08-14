import axios from 'axios';
import { debugLog } from '../lib/pipelineLog';

// Name-based backend history (per the name-identity spec). CONFIRMED with the
// user: this backend does not exist yet -- these are stubs. Paths are
// relative (no hardcoded host) via VITE_FINGURU_NAME_API_BASE so they're easy
// to point at a real backend later without another frontend change; until
// then calls here are EXPECTED to fail (no route at this origin) and callers
// must treat that as a soft no-op, not a user-facing error.
const BASE = (import.meta.env.VITE_FINGURU_NAME_API_BASE || '').replace(/\/+$/, '');

/**
 * GET /api/history?name=<name> -- the backend's history for this name,
 * treated as authoritative (callers should overwrite local state with it,
 * never merge). Returns null if unavailable (backend not deployed, network
 * error, 404, etc.) rather than throwing, since this is a best-effort
 * enhancement layer over the existing local (IndexedDB) history, not a
 * replacement for it.
 */
export async function fetchNameHistory(name) {
  if (!name) return null;
  try {
    const { data } = await axios.get(`${BASE}/api/history`, { params: { name }, timeout: 5000 });
    return data ?? null;
  } catch (err) {
    debugLog('[FinGuru] name-based history unavailable (expected until that backend exists):', err?.message);
    return null;
  }
}

/**
 * POST /api/tools/save -- exposed per the spec's "/api/tools/save, etc."
 * mention, but NOT wired to any UI: the spec doesn't define what triggers a
 * save or what payload it expects beyond `name`, so inventing a call site
 * here would be guessing at backend behavior. Call this once a real trigger
 * is specified.
 */
export async function saveTool(name, payload = {}) {
  const { data } = await axios.post(`${BASE}/api/tools/save`, { name, ...payload });
  return data;
}
