import { useCallback, useEffect, useState } from "react";
import { useAuth } from "./useAuth";
import type { Profile } from "../lib/types";

export function useUserSearch(query: string) {
  const { supabase, user } = useAuth();
  const [rows, setRows] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(false);

  const run = useCallback(async () => {
    if (!user) return;
    const q = (query || "").trim();
    if (!q) {
      setRows([]);
      return;
    }
    setLoading(true);
    const { data } = await supabase
      .from("profiles")
      .select("id,display_name,role,e2ee_public_key_b64")
      .ilike("display_name", `%${q}%`)
      .limit(20);
    const list = ((data as Profile[]) ?? []).filter((p) => p.id !== user.id);
    setRows(list);
    setLoading(false);
  }, [query, supabase, user]);

  useEffect(() => {
    const t = setTimeout(run, 200);
    return () => clearTimeout(t);
  }, [run]);

  return { rows, loading };
}

