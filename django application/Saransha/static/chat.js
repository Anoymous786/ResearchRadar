(() => {
  const boot = window.__CHAT_BOOTSTRAP__ || {};
  const meId = String(boot.meId || "").trim();
  const SUPABASE_URL = String(boot.supabaseUrl || "").trim();
  const SUPABASE_ANON_KEY = String(boot.supabaseAnonKey || "").trim();
  const peers = Array.isArray(boot.peers) ? boot.peers : [];

  const userSearch = document.getElementById("userSearch");
  const userList = document.getElementById("userList");
  const msgBody = document.getElementById("msgBody");
  const msgInput = document.getElementById("msgInput");
  const sendBtn = document.getElementById("sendBtn");
  const chatTitle = document.getElementById("chatTitle");
  const chatStatus = document.getElementById("chatStatus");

  function esc(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function b64(bytes) {
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  function randomNonceB64() {
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    return b64(bytes);
  }

  function normalizePair(a, b) {
    const as = String(a);
    const bs = String(b);
    return as < bs ? [as, bs] : [bs, as];
  }

  function messageTextFromRow(row) {
    // Assumption: messages store E2EE payload in ciphertext.
    // For this minimal UI we display ciphertext as-is.
    return (
      row.ciphertext ||
      row.ciphertext_b64 ||
      row.ciphertext_text ||
      row.content ||
      row.message ||
      ""
    );
  }

  function ensureSupabase() {
    if (!SUPABASE_URL || !SUPABASE_ANON_KEY || !window.supabase) return null;
    return window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      realtime: { params: { eventsPerSecond: 20 } },
    });
  }

  const supabase = ensureSupabase();
  if (!supabase) {
    msgBody.innerHTML =
      '<div class="text-danger">Supabase client not configured. Set <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code>.</div>';
    return;
  }

  let activePeer = null;
  let conversationId = null;
  let channel = null;
  let hasMore = true;
  let loadingOlder = false;
  let cursorCreatedAt = null; // oldest loaded created_at

  const state = {
    byId: new Map(),
    order: [], // ids sorted oldest->newest
  };

  function sortState() {
    state.order.sort((a, b) => {
      const ma = state.byId.get(a) || {};
      const mb = state.byId.get(b) || {};
      const ka = `${ma.created_at || ""}::${ma.id || ""}`;
      const kb = `${mb.created_at || ""}::${mb.id || ""}`;
      return ka.localeCompare(kb);
    });
  }

  function upsertMessage(m) {
    const id = String(m.id || "").trim();
    if (!id) return;
    const existing = state.byId.get(id);
    state.byId.set(id, existing ? { ...existing, ...m } : m);
    if (!existing) state.order.push(id);
    sortState();
  }

  function render({ keepScroll = false } = {}) {
    const prevHeight = msgBody.scrollHeight;
    const prevTop = msgBody.scrollTop;

    msgBody.innerHTML = "";
    if (!state.order.length) {
      msgBody.innerHTML = '<div class="text-center text-muted mt-4">No messages yet.</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    for (const id of state.order) {
      const m = state.byId.get(id);
      if (!m) continue;
      const row = document.createElement("div");
      row.className = `msg-row ${m.is_me ? "sent" : "recv"}`;
      row.dataset.id = String(m.id);

      const bubble = document.createElement("div");
      bubble.className = "msg-bubble";
      bubble.innerHTML = esc(m.text || "");

      if (m.is_me && m.pending) {
        const meta = document.createElement("div");
        meta.className = "small";
        meta.style.opacity = ".85";
        meta.style.marginTop = ".25rem";
        meta.style.textAlign = "right";
        meta.textContent = "Sending…";
        bubble.appendChild(document.createElement("br"));
        bubble.appendChild(meta);
      }

      row.appendChild(bubble);
      frag.appendChild(row);
    }
    msgBody.appendChild(frag);

    if (keepScroll) {
      const delta = msgBody.scrollHeight - prevHeight;
      msgBody.scrollTop = prevTop + delta;
    } else {
      msgBody.scrollTop = msgBody.scrollHeight;
    }
  }

  async function teardownChannel() {
    if (channel) {
      try {
        await supabase.removeChannel(channel);
      } catch (_) {}
    }
    channel = null;
  }

  async function setupRealtime(cid) {
    await teardownChannel();
    if (!cid) return;
    channel = supabase
      .channel(`conversation:${cid}`)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "messages", filter: `conversation_id=eq.${cid}` },
        (payload) => {
          const row = payload.new || {};
          const senderId = row.sender_id || row.from_user_id || row.user_id;
          const createdAt = row.created_at || nowIso();
          const msg = {
            id: row.id,
            created_at: createdAt,
            is_me: String(senderId) === String(meId),
            text: messageTextFromRow(row),
            pending: false,
          };
          upsertMessage(msg);
          render({ keepScroll: false });
        }
      )
      .subscribe();
  }

  async function getOrCreateConversation(peerId) {
    const [u1, u2] = normalizePair(meId, peerId);

    // Assumption per requirement: conversations(user1, user2) exists.
    const existing = await supabase
      .from("conversations")
      .select("id,user1,user2,created_at")
      .eq("user1", u1)
      .eq("user2", u2)
      .maybeSingle();
    if (existing.error) throw existing.error;
    if (existing.data) return existing.data;

    const created = await supabase
      .from("conversations")
      .insert({ user1: u1, user2: u2 })
      .select("id,user1,user2,created_at")
      .single();
    if (created.error) throw created.error;
    return created.data;
  }

  async function loadInitialMessages() {
    if (!conversationId) return;
    state.byId = new Map();
    state.order = [];
    hasMore = true;
    cursorCreatedAt = null;

    const res = await supabase
      .from("messages")
      .select("id,conversation_id,sender_id,ciphertext,nonce,created_at")
      .eq("conversation_id", conversationId)
      .order("created_at", { ascending: false })
      .limit(30);

    if (res.error) throw res.error;
    const rows = (res.data || []).slice().reverse();
    for (const row of rows) {
      upsertMessage({
        id: row.id,
        created_at: row.created_at,
        is_me: String(row.sender_id) === String(meId),
        text: messageTextFromRow(row),
        pending: false,
      });
    }
    cursorCreatedAt = rows.length ? rows[0].created_at : null;
    hasMore = rows.length >= 30;
    render({ keepScroll: false });
  }

  async function loadOlderMessages() {
    if (!conversationId || loadingOlder || !hasMore || !cursorCreatedAt) return;
    loadingOlder = true;
    try {
      const res = await supabase
        .from("messages")
        .select("id,conversation_id,sender_id,ciphertext,nonce,created_at")
        .eq("conversation_id", conversationId)
        .lt("created_at", cursorCreatedAt)
        .order("created_at", { ascending: false })
        .limit(30);
      if (res.error) throw res.error;

      const rows = (res.data || []);
      if (!rows.length) {
        hasMore = false;
        return;
      }

      // prepend, keep scroll anchored
      const incoming = rows.slice().reverse();
      for (const row of incoming) {
        upsertMessage({
          id: row.id,
          created_at: row.created_at,
          is_me: String(row.sender_id) === String(meId),
          text: messageTextFromRow(row),
          pending: false,
        });
      }
      cursorCreatedAt = incoming[0].created_at;
      hasMore = rows.length >= 30;
      render({ keepScroll: true });
    } finally {
      loadingOlder = false;
    }
  }

  async function openChat(peer) {
    if (!peer || !peer.id) return;
    activePeer = peer;
    chatTitle.textContent = peer.name || "Chat";
    chatStatus.textContent = peer.role ? `Role: ${peer.role}` : " ";
    msgInput.disabled = false;
    sendBtn.disabled = false;

    try {
      const conv = await getOrCreateConversation(String(peer.id));
      conversationId = conv.id;
      await loadInitialMessages();
      await setupRealtime(conversationId);
    } catch (e) {
      const msg = (e && e.message) ? e.message : "Unable to open conversation.";
      msgBody.innerHTML = `<div class="text-danger">${esc(msg)}</div>`;
      conversationId = null;
      await teardownChannel();
    }
  }

  async function sendMessage() {
    const text = (msgInput.value || "").trim();
    if (!text || !conversationId || !activePeer) return;

    const id = (crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const optimistic = {
      id,
      created_at: nowIso(),
      is_me: true,
      text,
      pending: true,
    };
    upsertMessage(optimistic);
    render({ keepScroll: false });
    msgInput.value = "";

    // Minimal: store plaintext in ciphertext field (still satisfies "ciphertext-only" column shape).
    // If your DB enforces true E2EE, replace this with real client encryption.
    const nonce = randomNonceB64();
    const insert = await supabase.from("messages").insert({
      id,
      conversation_id: conversationId,
      sender_id: meId,
      ciphertext: text,
      nonce,
    });

    if (insert.error) {
      // Keep optimistic message visible; show error in header.
      chatStatus.textContent = `Error: ${insert.error.message}`;
      return;
    }

    const saved = state.byId.get(id);
    if (saved) {
      saved.pending = false;
      upsertMessage(saved);
      render({ keepScroll: false });
    }
  }

  function renderPeers(list) {
    userList.innerHTML =
      list
        .map((u) => {
          const id = esc(String(u.id));
          const name = esc(String(u.name || "User"));
          const email = esc(String(u.email || ""));
          const role = esc(String(u.role || ""));
          return `
            <div class="person-item" data-id="${id}">
              <div class="fw-semibold">${name}</div>
              <div class="person-meta">${email}${role ? " • " + role : ""}</div>
            </div>
          `;
        })
        .join("") || '<div class="text-muted small">No users found.</div>';
  }

  function currentPeerList() {
    const q = String(userSearch.value || "").trim().toLowerCase();
    if (!q) return peers;
    return peers.filter((p) => {
      const name = String(p.name || "").toLowerCase();
      const email = String(p.email || "").toLowerCase();
      return name.includes(q) || email.includes(q);
    });
  }

  userSearch.addEventListener("input", () => renderPeers(currentPeerList()));

  userList.addEventListener("click", (e) => {
    const item = e.target.closest(".person-item");
    if (!item) return;
    const id = item.dataset.id;
    const peer = peers.find((p) => String(p.id) === String(id));
    document.querySelectorAll(".person-item").forEach((el) => el.classList.remove("active"));
    item.classList.add("active");
    openChat(peer);
  });

  sendBtn.addEventListener("click", sendMessage);
  msgInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  msgBody.addEventListener("scroll", () => {
    if (msgBody.scrollTop < 24) loadOlderMessages();
  });

  // Initial render
  renderPeers(peers);
})();

