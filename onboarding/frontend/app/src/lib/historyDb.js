// Persistent chat-history storage for FinGuru (Prompt 3), using the native
// IndexedDB API directly -- NOT the `idb` package the prompt suggests, since
// it isn't in package.json and adding a dependency needs sign-off first. Raw
// IndexedDB is more verbose but fully sufficient here, and it natively
// structured-clones Blobs, so audio can live inside the same record as the
// rest of the conversation with no extra encoding step.
//
// Schema (deliberately simple / denormalized for a hackathon-scale app): one
// record per conversation, containing its full message list inline.
//   conversations: { id, profileId, title, startedAt, updatedAt, messages[] }
//   message shape mirrors FinGuruChat's in-memory message objects, e.g.:
//   { id, direction, type, text, audioBlob, duration, status, variant, language }
//   (the ephemeral `audioUrl` object-URL is stripped before storage and
//   regenerated from `audioBlob` on load -- object URLs don't survive reload).

const DB_NAME = 'finguru-history';
const DB_VERSION = 1;
const STORE = 'conversations';

// Retention cap: keep at most this many conversations per profile locally.
export const MAX_CONVERSATIONS = 30;

export const historySupported = typeof window !== 'undefined' && 'indexedDB' in window;

let dbPromise = null;

function openDb() {
  if (!historySupported) return Promise.reject(new Error('indexedDB unsupported'));
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: 'id' });
          store.createIndex('profileId', 'profileId');
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

function reqToPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Insert or fully overwrite a conversation record. */
export async function upsertConversation(record) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function getConversation(id) {
  const db = await openDb();
  const tx = db.transaction(STORE, 'readonly');
  const result = await reqToPromise(tx.objectStore(STORE).get(id));
  return result || null;
}

/** All conversations for a profile, most-recently-updated first. */
export async function listConversations(profileId) {
  const db = await openDb();
  const tx = db.transaction(STORE, 'readonly');
  const idx = tx.objectStore(STORE).index('profileId');
  const all = await reqToPromise(idx.getAll(profileId));
  return all.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

export async function deleteConversation(id) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/** Delete every conversation for a profile (per-user "clear all history"). */
export async function clearAllHistory(profileId) {
  const all = await listConversations(profileId);
  await Promise.all(all.map((c) => deleteConversation(c.id)));
}

/** Keep only the `max` most-recently-updated conversations for a profile. */
export async function trimRetention(profileId, max = MAX_CONVERSATIONS) {
  const all = await listConversations(profileId); // already sorted newest-first
  const toDelete = all.slice(max);
  await Promise.all(toDelete.map((c) => deleteConversation(c.id)));
}
