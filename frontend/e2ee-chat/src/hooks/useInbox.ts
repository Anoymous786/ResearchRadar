import { useCallback, useEffect, useState } from "react";
import { useAuth } from "./useAuth";
import type { InboxRow } from "../lib/types";

export function useInbox() {
  const { supabase, user } = useAuth();
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);

  const load = useCallback(
    async (mode: "reset" | "more" = "reset") => {
      if (!user) return;
      setError(null);
      if (mode === "reset") {
        setLoading(true);
        setCursor(null);
        setHasMore(true);
      }
      const before = mode === "more" ? cursor : null;
      const { data, error } = await supabase.rpc("chat_inbox", { p_limit: 50, p_before: before });
      if (error) {
        setError(error.message);
        setLoading(false);
        return;
      }
      const page = (data as InboxRow[]) ?? [];
      if (mode === "reset") setRows(page);
      else setRows((prev) => [...prev, ...page]);
      const nextCursor = page.length ? page[page.length - 1]?.last_message_at ?? null : null;
      setCursor(nextCursor);
      setHasMore(Boolean(nextCursor) && page.length >= 50);
      setLoading(false);
    },
    [cursor, supabase, user]
  );

  useEffect(() => {
    load("reset");
  }, [load]);

  return { rows, loading, error, refresh: () => load("reset"), loadMore: () => load("more"), hasMore };
}

