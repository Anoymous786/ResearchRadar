import { useEffect, useState } from "react";
import { useAuth } from "./useAuth";
import type { Profile } from "../lib/types";

export function useMyProfile() {
  const { supabase, user } = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!user) {
        setProfile(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      const { data, error } = await supabase
        .from("profiles")
        .select("id,display_name,role,e2ee_public_key_b64")
        .eq("id", user.id)
        .maybeSingle();
      if (cancelled) return;
      if (error) {
        setError(error.message);
        setProfile(null);
      } else {
        setProfile((data as Profile | null) ?? null);
      }
      setLoading(false);
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [supabase, user]);

  return { profile, setProfile, loading, error };
}

