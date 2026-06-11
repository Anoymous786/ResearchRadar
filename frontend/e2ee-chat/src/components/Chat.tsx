import { useEffect, useRef, useState } from "react";
import type { UiMessage } from "../hooks/useConversation";

function formatBubbleTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function Chat(props: {
  title: string;
  meta: string;
  messages: UiMessage[];
  loading: boolean;
  onSend: (text: string) => void;
  onLoadOlder: () => void;
  hasMore: boolean;
}) {
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [props.messages.length]);

  function onScroll() {
    const el = listRef.current;
    if (!el) return;
    if (el.scrollTop < 24 && props.hasMore) props.onLoadOlder();
  }

  return (
    <>
      <div className="chatHead">
        <div>
          <div className="chatTitle">{props.title}</div>
          <div className="chatMeta">{props.meta}</div>
        </div>
      </div>
      <div className="msgs" ref={listRef} onScroll={onScroll}>
        {props.loading ? (
          <div className="item-sub">Loading…</div>
        ) : props.messages.length ? (
          props.messages.map((m) => (
            <div key={m.id} className={`row ${m.mine ? "me" : ""}`}>
              <div className="bubble">
                {m.text}
                <div className="time">
                  {formatBubbleTime(m.created_at)}
                  {m.mine && m.pending ? " • sending…" : ""}
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="item-sub">No messages yet.</div>
        )}
      </div>
      <div className="composer">
        <input
          className="input"
          value={text}
          placeholder="Type a message…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              props.onSend(text);
              setText("");
            }
          }}
        />
        <button
          className="btn"
          onClick={() => {
            props.onSend(text);
            setText("");
          }}
          disabled={!text.trim()}
        >
          Send
        </button>
      </div>
    </>
  );
}

