import { useMemo } from "react";
import type { InboxRow } from "../lib/types";
import { useE2eeIdentity } from "../hooks/useE2eeIdentity";
import { decryptWithSharedKey, deriveSharedKey } from "../crypto/e2ee";

function formatTime(ts: string | null) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function Inbox(props: {
  rows: InboxRow[];
  activeOtherId: string | null;
  onOpen: (otherUserId: string) => void;
}) {
  const { identity } = useE2eeIdentity();

  const items = useMemo(() => {
    return props.rows.map((r) => {
      let preview = "";
      if (identity?.secretKeyB64 && r.other_e2ee_public_key_b64 && r.last_message_ciphertext_b64 && r.last_message_nonce_b64) {
        const k = deriveSharedKey({ mySecretKeyB64: identity.secretKeyB64, peerPublicKeyB64: r.other_e2ee_public_key_b64 });
        preview =
          decryptWithSharedKey(k, { ciphertextB64: r.last_message_ciphertext_b64, nonceB64: r.last_message_nonce_b64 }) ??
          "🔒 Unable to decrypt";
      } else if (r.last_message_id) {
        preview = "🔒 Encrypted message";
      } else {
        preview = "No messages yet";
      }
      return { r, preview };
    });
  }, [identity?.secretKeyB64, props.rows]);

  return (
    <div className="list">
      {items.length ? (
        items.map(({ r, preview }) => (
          <div
            key={r.conversation_id}
            className={`item ${props.activeOtherId === r.other_user_id ? "active" : ""}`}
            onClick={() => props.onOpen(r.other_user_id)}
          >
            <div style={{ minWidth: 0 }}>
              <div className="item-title">{r.other_display_name ?? "Unknown user"}</div>
              <div className="item-sub">{preview}</div>
              <div className="item-sub" style={{ marginTop: 2 }}>
                {formatTime(r.last_message_at)} {r.other_role ? `• ${r.other_role}` : ""}
              </div>
            </div>
            {r.unread_count ? <div className="badge">{r.unread_count}</div> : null}
          </div>
        ))
      ) : (
        <div className="item-sub">No conversations yet. Search a user to start.</div>
      )}
    </div>
  );
}

