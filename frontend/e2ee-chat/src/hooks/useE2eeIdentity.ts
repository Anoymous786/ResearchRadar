import { useEffect, useMemo, useState } from "react";
import { generateIdentityKeypair, publicKeyFromSecretKeyB64 } from "../crypto/e2ee";
import { getIdentitySecretKeyB64, setIdentitySecretKeyB64 } from "../crypto/keystore";
import { useAuth } from "./useAuth";
import { useMyProfile } from "./useMyProfile";

export function useE2eeIdentity() {
  const { supabase, user } = useAuth();
  const { profile } = useMyProfile();
  const [secretKeyB64, setSecretKey] = useState<string | null>(null);
  const [publicKeyB64, setPublicKey] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const identity = useMemo(() => {
    if (!secretKeyB64 || !publicKeyB64) return null;
    return { secretKeyB64, publicKeyB64 };
  }, [secretKeyB64, publicKeyB64]);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!user) {
        setSecretKey(null);
        setPublicKey(null);
        setReady(false);
        return;
      }

      const existing = await getIdentitySecretKeyB64(user.id);
      if (cancelled) return;

      if (existing) {
        setSecretKey(existing);
        setPublicKey(publicKeyFromSecretKeyB64(existing));
        setReady(true);
        return;
      }

      // First run on this browser: create identity keypair, store secret locally, publish public key.
      const kp = generateIdentityKeypair();
      await setIdentitySecretKeyB64(user.id, kp.secretKeyB64);
      setSecretKey(kp.secretKeyB64);
      setPublicKey(kp.publicKeyB64);
      setReady(true);
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [supabase, user]);

  // Best-effort publish/repair public key on the server.
  // This keeps E2EE working when a user signs in on a new browser/device.
  useEffect(() => {
    let cancelled = false;
    async function publish() {
      if (!user || !profile || !publicKeyB64) return;
      if (profile.e2ee_public_key_b64 === publicKeyB64) return;
      const { error } = await supabase.from("profiles").update({ e2ee_public_key_b64: publicKeyB64 }).eq("id", user.id);
      if (cancelled) return;
      if (error) {
        // Ignore; UI can still send but peer won't be able to decrypt until fixed.
        return;
      }
    }
    publish();
    return () => {
      cancelled = true;
    };
  }, [profile, publicKeyB64, supabase, user]);

  return { identity, ready };
}

