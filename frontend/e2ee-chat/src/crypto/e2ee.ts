import nacl from "tweetnacl";
import { b64ToBytes, bytesToB64 } from "../lib/base64";

export type E2eeKeypair = {
  publicKeyB64: string;
  secretKeyB64: string;
};

export type EncryptedPayload = {
  ciphertextB64: string;
  nonceB64: string;
};

export function generateIdentityKeypair(): E2eeKeypair {
  const kp = nacl.box.keyPair();
  return {
    publicKeyB64: bytesToB64(kp.publicKey),
    secretKeyB64: bytesToB64(kp.secretKey),
  };
}

export function publicKeyFromSecretKeyB64(secretKeyB64: string): string {
  const sk = b64ToBytes(secretKeyB64);
  const kp = nacl.box.keyPair.fromSecretKey(sk);
  return bytesToB64(kp.publicKey);
}

/**
 * Shared key for 1:1 conversation derived via X25519 (Curve25519) ECDH:
 * nacl.box.before(peerPublicKey, mySecretKey) => 32-byte shared key.
 *
 * This is "true E2EE" (server can't decrypt) but does NOT provide strong forward secrecy
 * like Signal's Double Ratchet. It's a practical baseline that can be upgraded later.
 */
export function deriveSharedKey(params: {
  mySecretKeyB64: string;
  peerPublicKeyB64: string;
}): Uint8Array {
  const mySk = b64ToBytes(params.mySecretKeyB64);
  const peerPk = b64ToBytes(params.peerPublicKeyB64);
  return nacl.box.before(peerPk, mySk);
}

export function encryptWithSharedKey(sharedKey: Uint8Array, plaintext: string): EncryptedPayload {
  const nonce = nacl.randomBytes(nacl.secretbox.nonceLength);
  const msg = new TextEncoder().encode(plaintext);
  const box = nacl.secretbox(msg, nonce, sharedKey);
  return { ciphertextB64: bytesToB64(box), nonceB64: bytesToB64(nonce) };
}

export function decryptWithSharedKey(sharedKey: Uint8Array, payload: EncryptedPayload): string | null {
  try {
    const nonce = b64ToBytes(payload.nonceB64);
    const box = b64ToBytes(payload.ciphertextB64);
    const opened = nacl.secretbox.open(box, nonce, sharedKey);
    if (!opened) return null;
    return new TextDecoder().decode(opened);
  } catch {
    return null;
  }
}

