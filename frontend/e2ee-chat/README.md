## Saransha E2EE Chat (Supabase)

### What this is
- 1:1 chat (student↔student, student↔faculty, faculty↔faculty)
- **True E2EE at application level**: the browser encrypts messages before insert; DB stores only ciphertext + nonce.
- Fast inbox: `chat_inbox()` RPC returns last message ciphertext + unread count + peer profile in one call.
- Fast history: `messages(conversation_id, created_at desc)` index + infinite scroll (load older on scroll-up).
- Realtime: subscribes only to the active conversation.

### Setup (Supabase)
1. In Supabase SQL editor, run `supabase/chat.sql`.
2. Ensure Realtime is enabled for:
   - `public.messages`
   - `public.conversations` (recommended)
   - `public.message_receipts` (recommended)
   - `public.conversation_user_state` (recommended)
3. Keep RLS enabled on:
   - `public.profiles`
   - `public.conversations`
   - `public.messages`
   - `public.conversation_user_state`
   - `public.message_receipts`

### Setup (local dev)
1. `cd frontend/e2ee-chat`
2. Copy `.env.example` → `.env` and fill:
   - `VITE_SUPABASE_URL`
   - `VITE_SUPABASE_ANON_KEY`
3. Install & run:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

### Runtime flow (what must work)
- Sign in with Supabase Auth, then create a `profiles` row (role + display name + public key).
- E2EE identity private key is generated client-side and stored in IndexedDB per account.
- Conversation uses canonical pair logic (`user1 < user2`) and stays unique per 1:1 pair.
- Messages are inserted as ciphertext + nonce only; plaintext is never stored.
- Active chat subscribes to Realtime only for that conversation (`messages` INSERT filter).
- Inbox uses `chat_inbox()` RPC for last message + unread count + peer profile in one call.

### Notes
- This implementation uses Curve25519 ECDH + `secretbox` for encryption. It is E2EE (server can't decrypt) but not a full Signal-style Double Ratchet (forward secrecy can be added later).
- The E2EE private key is stored **locally** in IndexedDB (per logged-in Supabase user on this browser). Clearing site data will lose the key and old messages become undecryptable.
- If a user signs in on a different browser/device without key migration, previously encrypted messages cannot be decrypted there.

