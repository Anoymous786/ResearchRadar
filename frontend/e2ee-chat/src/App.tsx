import { useMemo, useState } from "react";
import { AuthGate } from "./components/AuthGate";
import { Inbox } from "./components/Inbox";
import { Chat } from "./components/Chat";
import { useInbox } from "./hooks/useInbox";
import { useUserSearch } from "./hooks/useUserSearch";
import { useConversation } from "./hooks/useConversation";

export function App() {
  const [q, setQ] = useState("");
  const [activeOtherId, setActiveOtherId] = useState<string | null>(null);

  const inbox = useInbox();
  const search = useUserSearch(q);
  const convo = useConversation(activeOtherId);

  const searchResults = useMemo(() => {
    if (!q.trim()) return [];
    return search.rows;
  }, [q, search.rows]);

  return (
    <AuthGate>
      <div className="shell">
        <aside className="panel left">
          <div className="toolbar">
            <input
              className="search"
              placeholder="Search users…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>

          {q.trim() ? (
            <div className="list">
              {search.loading ? <div className="item-sub">Searching…</div> : null}
              {searchResults.map((p) => (
                <div
                  className={`item ${activeOtherId === p.id ? "active" : ""}`}
                  key={p.id}
                  onClick={() => {
                    setActiveOtherId(p.id);
                    setQ("");
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div className="item-title">{p.display_name}</div>
                    <div className="item-sub">{p.role}</div>
                  </div>
                </div>
              ))}
              {!search.loading && !searchResults.length ? <div className="item-sub">No users found.</div> : null}
            </div>
          ) : (
            <Inbox rows={inbox.rows} activeOtherId={activeOtherId} onOpen={(id) => setActiveOtherId(id)} />
          )}
        </aside>

        <section className="panel right">
          {activeOtherId ? (
            <Chat
              title={convo.other?.display_name ?? "Chat"}
              meta={convo.other?.role ? `Role: ${convo.other.role}` : " "}
              messages={convo.messages}
              loading={convo.loading}
              onSend={convo.sendText}
              onLoadOlder={convo.loadOlder}
              hasMore={convo.hasMore}
            />
          ) : (
            <div className="chatHead">
              <div>
                <div className="chatTitle">Messages</div>
                <div className="chatMeta">Select a conversation or search a user.</div>
              </div>
            </div>
          )}
        </section>
      </div>
    </AuthGate>
  );
}

