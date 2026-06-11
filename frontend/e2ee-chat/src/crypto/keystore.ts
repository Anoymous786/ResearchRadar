/**
 * Minimal IndexedDB key store.
 *
 * Stores ONLY the user's E2EE private key locally in the browser.
 * If the user clears site data, the key is lost and old messages become undecryptable.
 *
 * Production upgrade path: wrap secret key with a user passphrase using WebCrypto
 * (PBKDF2/Argon2 + AES-GCM) and/or support multi-device key export/import.
 */

const DB_NAME = "saransha_chat";
const DB_VERSION = 1;
const STORE = "keys";
const LEGACY_KEY_ID = "identity_secret_key_b64";

function keyIdForUser(userId: string): string {
  return `identity_secret_key_b64:${userId}`;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onerror = () => reject(req.error);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
  });
}

async function withStore<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    const req = fn(store);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
    tx.oncomplete = () => db.close();
    tx.onerror = () => reject(tx.error);
  });
}

export async function getIdentitySecretKeyB64(userId: string): Promise<string | null> {
  try {
    const perUser = await withStore("readonly", (s) => s.get(keyIdForUser(userId)));
    if (typeof perUser === "string" && perUser) return perUser;

    // Backward-compat for old single-key storage.
    const legacy = await withStore("readonly", (s) => s.get(LEGACY_KEY_ID));
    if (typeof legacy === "string" && legacy) return legacy;
    return null;
  } catch {
    return null;
  }
}

export async function setIdentitySecretKeyB64(userId: string, secretKeyB64: string): Promise<void> {
  await withStore("readwrite", (s) => s.put(secretKeyB64, keyIdForUser(userId)));
}

