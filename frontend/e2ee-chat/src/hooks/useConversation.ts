import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./useAuth";
import { useE2eeIdentity } from "./useE2eeIdentity";
import { decryptWithSharedKey, deriveSharedKey, encryptWithSharedKey } from "../crypto/e2ee";
import type { MessageRow, Profile } from "../lib/types";

export type UiMessage = {
  id: string;
  mine: boolean;
  created_at: string;
  text: string;
  pending?: boolean;
};

function isoNow() {
  return new Date().toISOString();
}

function canonicalPair(a: string, b: string): { user1: string; user2: string } {
  return a < b ? { user1: a, user2: b } : { user1: b, user2: a };
}

export function useConversation(otherUserId: string | null) {
  const { supabase, user } = useAuth();
  const { identity } = useE2eeIdentity();

  const [other, setOther] = useState<Profile | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);

  const sharedKey = useMemo(() => {
    if (!identity?.secretKeyB64 || !other?.e2ee_public_key_b64) return null;
    return deriveSharedKey({ mySecretKeyB64: identity.secretKeyB64, peerPublicKeyB64: other.e2ee_public_key_b64 });
  }, [identity?.secretKeyB64, other?.e2ee_public_key_b64]);

  const channelRef = useRef<ReturnType<typeof supabase.channel> | null>(null);

  const teardown = useCallback(async () => {
    if (channelRef.current) {
      try {
        await supabase.removeChannel(channelRef.current);
      } catch {
        // ignore
      }
      channelRef.current = null;
    }
  }, [supabase]);

  const open = useCallback(async () => {
    if (!user || !otherUserId) return;
    setLoading(true);
    setMessages([]);
    setCursor(null);
    setHasMore(true);

    const { data: otherProfile, error: otherErr } = await supabase
      .from("profiles")
      .select("id,display_name,role,e2ee_public_key_b64")
      .eq("id", otherUserId)
      .maybeSingle();
    if (otherErr) {
      setLoading(false);
      return;
    }
    const otherP = (otherProfile as Profile | null) ?? null;
    setOther(otherP);
    const keyForInitial =
      identity?.secretKeyB64 && otherP?.e2ee_public_key_b64
        ? deriveSharedKey({ mySecretKeyB64: identity.secretKeyB64, peerPublicKeyB64: otherP.e2ee_public_key_b64 })
        : null;

    const pair = canonicalPair(user.id, otherUserId);
    const existing = await supabase.from("conversations").select("id").eq("user1", pair.user1).eq("user2", pair.user2).maybeSingle();
    if (existing.error) {
      setLoading(false);
      return;
    }
    let cid = existing.data?.id as string | undefined;
    if (!cid) {
      const created = await supabase.from("conversations").insert(pair).select("id").single();
      if (created.error) {
        // Two users can try to create the same pair concurrently.
        // If unique constraint wins elsewhere, fetch the existing row and proceed.
        if (created.error.code === "23505") {
          const retry = await supabase
            .from("conversations")
            .select("id")
            .eq("user1", pair.user1)
            .eq("user2", pair.user2)
            .single();
          if (retry.error) {
            setLoading(false);
            return;
          }
          cid = retry.data.id as string;
        } else {
          setLoading(false);
          return;
        }
      }
      if (!cid && created.data?.id) cid = created.data.id as string;
    }
    setConversationId(cid);

    // Load newest page
    const { data: page, error: pageErr } = await supabase
      .from("messages")
      .select("id,conversation_id,sender_id,created_at,client_created_at,type,ciphertext_b64,nonce_b64")
      .eq("conversation_id", cid)
      .order("created_at", { ascending: false })
      .limit(30);
    if (!pageErr) {
      const rows = ((page as MessageRow[]) ?? []).slice().reverse();
      setCursor(rows.length ? rows[0]!.created_at : null);
      setHasMore(rows.length >= 30);
      setMessages(rowsToUi(rows, keyForInitial));
    }

    await teardown();
    // Realtime only for active conversation
    channelRef.current = supabase
      .channel(`conversation:${cid}`)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "messages", filter: `conversation_id=eq.${cid}` },
        (payload) => {
          const row = payload.new as unknown as MessageRow;
          setMessages((prev) => {
            // De-dupe by id
            if (prev.some((m) => m.id === row.id)) return prev;
            const next = [...prev, ...rowsToUi([row], keyForInitial)];
            next.sort((a, b) => `${a.created_at}::${a.id}`.localeCompare(`${b.created_at}::${b.id}`));
            return next;
          });
          // For recipient: mark as delivered quickly (receipt row is created by DB trigger)
          if (user.id !== row.sender_id) {
            supabase.rpc("chat_mark_delivered", { p_conversation_id: cid, p_delivered_at: isoNow() }).catch(() => {});
          }
        }
      )
      .subscribe();

    // Mark read (fast unread reset)
    await supabase.rpc("chat_mark_read", { p_conversation_id: cid, p_read_at: isoNow() });
    // Mark seen (best-effort; sets seen_at for recipient receipts in this conversation)
    await supabase.rpc("chat_mark_seen", { p_conversation_id: cid, p_seen_at: isoNow() });

    setLoading(false);
  }, [identity?.secretKeyB64, otherUserId, supabase, teardown, user]);

  function rowsToUi(rows: MessageRow[], keyOverride?: Uint8Array | null): UiMessage[] {
    const key = keyOverride ?? sharedKey;
    return rows.map((r) => {
      const mine = user?.id === r.sender_id;
      const text =
        key && r.ciphertext_b64 && r.nonce_b64
          ? decryptWithSharedKey(key, { ciphertextB64: r.ciphertext_b64, nonceB64: r.nonce_b64 }) ?? "🔒 Unable to decrypt"
          : "🔒 Encrypted";
      return { id: r.id, mine, created_at: r.created_at, text };
    });
  }

  useEffect(() => {
    open();
    return () => {
      teardown();
    };
  }, [open, teardown]);

  const loadOlder = useCallback(async () => {
    if (!conversationId || !hasMore || !cursor) return;
    const { data, error } = await supabase
      .from("messages")
      .select("id,conversation_id,sender_id,created_at,client_created_at,type,ciphertext_b64,nonce_b64")
      .eq("conversation_id", conversationId)
      .lt("created_at", cursor)
      .order("created_at", { ascending: false })
      .limit(30);
    if (error) return;
    const rows = ((data as MessageRow[]) ?? []).slice().reverse();
    if (!rows.length) {
      setHasMore(false);
      return;
    }
    setCursor(rows[0]!.created_at);
    setHasMore(rows.length >= 30);
    setMessages((prev) => [...rowsToUi(rows), ...prev]);
  }, [conversationId, cursor, hasMore, supabase]);

  const sendText = useCallback(
    async (text: string) => {
      if (!conversationId || !user || !sharedKey) return;
      const trimmed = (text || "").trim();
      if (!trimmed) return;
      const id = crypto.randomUUID();
      const now = isoNow();
      setMessages((prev) => [...prev, { id, mine: true, created_at: now, text: trimmed, pending: true }]);

      const enc = encryptWithSharedKey(sharedKey, trimmed);
      const { error } = await supabase.from("messages").insert({
        id,
        conversation_id: conversationId,
        sender_id: user.id,
        type: "text",
        ciphertext_b64: enc.ciphertextB64,
        nonce_b64: enc.nonceB64,
        client_created_at: now,
      });
      if (error) {
        // Keep message locally; app can show failed state later.
        return;
      }
      // Mark read for me (keeps unread count sane if you sent last)
      await supabase.rpc("chat_mark_read", { p_conversation_id: conversationId, p_read_at: isoNow() });
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, pending: false } : m)));
    },
    [conversationId, sharedKey, supabase, user]
  );

  return { other, conversationId, messages, loading, sendText, loadOlder, hasMore };
}

