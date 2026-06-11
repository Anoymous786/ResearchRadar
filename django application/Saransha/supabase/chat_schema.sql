-- Production-ready 1:1 E2EE Chat schema (Supabase/Postgres)
-- Apply in Supabase SQL Editor.
--
-- Goals:
-- - 1 conversation per user pair (exactly 2 participants)
-- - Message content is E2EE ciphertext only (client encrypts; server never sees plaintext)
-- - Inbox loads fast (last message + unread count + peer profile in one query)
-- - Message history paginates efficiently (DESC index on created_at)
-- - Strict RLS enforced via Supabase Auth (auth.uid())
--
-- Assumptions:
-- - You use Supabase Auth for chat (RLS relies on JWT)
-- - `public.profiles` exists (or will be created here) for role/display_name + E2EE public key

create extension if not exists pgcrypto;

-- =========================================================
-- Profiles (role + E2EE identity public key)
-- =========================================================
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('student', 'faculty')),
  display_name text not null,
  -- Curve25519 public key (base64). Private key never stored server-side.
  e2ee_public_key_b64 text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists profiles_role_idx on public.profiles(role);
create index if not exists profiles_display_name_idx on public.profiles(display_name);

-- =========================================================
-- Conversations (strict 1:1)
-- =========================================================
create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  -- Canonical ordering (user1 < user2) enforced by CHECK + unique
  user1 uuid not null references auth.users(id) on delete cascade,
  user2 uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_message_id uuid,
  last_message_at timestamptz
);

alter table public.conversations
  add constraint conversations_user_pair_check check (user1 < user2 and user1 <> user2);

create unique index if not exists conversations_unique_pair_idx
  on public.conversations(user1, user2);

create index if not exists conversations_user1_last_idx
  on public.conversations(user1, last_message_at desc nulls last);

create index if not exists conversations_user2_last_idx
  on public.conversations(user2, last_message_at desc nulls last);

-- =========================================================
-- Conversation per-user state (unread counts, last_read)
-- =========================================================
create table if not exists public.conversation_user_state (
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  last_read_at timestamptz,
  unread_count int not null default 0 check (unread_count >= 0),
  updated_at timestamptz not null default now(),
  primary key (conversation_id, user_id)
);

create index if not exists conversation_user_state_user_idx
  on public.conversation_user_state(user_id, updated_at desc);

-- =========================================================
-- Messages (ciphertext only)
-- =========================================================
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  sender_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  client_created_at timestamptz,
  type text not null default 'text' check (type in ('text', 'voice')),
  -- E2EE payload (tweetnacl.secretbox output, base64)
  ciphertext_b64 text not null,
  nonce_b64 text not null,
  -- Optional metadata for attachments (still encrypted content; these are routing/display fields)
  media_bucket text,
  media_path text,
  media_mime text,
  media_bytes int,
  -- Server-side delivery helpers (does not expose plaintext)
  sender_device_id text
);

create index if not exists messages_conversation_created_desc_idx
  on public.messages (conversation_id, created_at desc, id desc);

create index if not exists messages_conversation_sender_created_idx
  on public.messages (conversation_id, sender_id, created_at desc);

-- =========================================================
-- Receipts (delivered/seen per recipient)
-- For 1:1, there is exactly one recipient != sender.
-- =========================================================
create table if not exists public.message_receipts (
  message_id uuid not null references public.messages(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  delivered_at timestamptz,
  seen_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (message_id, user_id)
);

create index if not exists message_receipts_user_seen_idx
  on public.message_receipts(user_id, seen_at desc nulls last);

-- =========================================================
-- Helpers
-- =========================================================
create or replace function public._chat_is_member(p_conversation_id uuid, p_user_id uuid)
returns boolean
language sql
stable
as $$
  select exists (
    select 1
    from public.conversations c
    where c.id = p_conversation_id and (c.user1 = p_user_id or c.user2 = p_user_id)
  );
$$;

-- Get the "other" participant for a conversation
create or replace function public._chat_other_user_id(p_conversation_id uuid, p_me uuid)
returns uuid
language sql
stable
as $$
  select case
    when c.user1 = p_me then c.user2
    when c.user2 = p_me then c.user1
    else null
  end
  from public.conversations c
  where c.id = p_conversation_id;
$$;

-- Touch conversation + unread counts after message insert
create or replace function public.chat_after_message_insert()
returns trigger
language plpgsql
as $$
declare
  v_other uuid;
begin
  update public.conversations
    set last_message_id = new.id,
        last_message_at = new.created_at,
        updated_at = now()
  where id = new.conversation_id;

  -- Ensure conversation_user_state rows exist
  insert into public.conversation_user_state(conversation_id, user_id, unread_count, updated_at)
  select c.id, c.user1, 0, now() from public.conversations c where c.id = new.conversation_id
  on conflict do nothing;

  insert into public.conversation_user_state(conversation_id, user_id, unread_count, updated_at)
  select c.id, c.user2, 0, now() from public.conversations c where c.id = new.conversation_id
  on conflict do nothing;

  v_other := public._chat_other_user_id(new.conversation_id, new.sender_id);
  if v_other is not null then
    update public.conversation_user_state
      set unread_count = unread_count + 1,
          updated_at = now()
    where conversation_id = new.conversation_id and user_id = v_other;

    -- Create empty receipt row for recipient (delivered/seen updated by client)
    insert into public.message_receipts(message_id, user_id, delivered_at, seen_at, updated_at)
    values (new.id, v_other, null, null, now())
    on conflict do nothing;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_chat_after_message_insert on public.messages;
create trigger trg_chat_after_message_insert
after insert on public.messages
for each row execute function public.chat_after_message_insert();

-- Mark conversation read (fast unread reset)
create or replace function public.chat_mark_read(p_conversation_id uuid, p_read_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public._chat_is_member(p_conversation_id, auth.uid()) then
    raise exception 'not a member';
  end if;

  insert into public.conversation_user_state(conversation_id, user_id, last_read_at, unread_count, updated_at)
  values (p_conversation_id, auth.uid(), p_read_at, 0, now())
  on conflict (conversation_id, user_id)
  do update set last_read_at = excluded.last_read_at, unread_count = 0, updated_at = now();
end;
$$;

revoke all on function public.chat_mark_read(uuid, timestamptz) from public;
grant execute on function public.chat_mark_read(uuid, timestamptz) to authenticated;

-- Mark messages as delivered/seen for the current user (recipient)
create or replace function public.chat_mark_delivered(p_conversation_id uuid, p_delivered_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public._chat_is_member(p_conversation_id, auth.uid()) then
    raise exception 'not a member';
  end if;

  update public.message_receipts r
    set delivered_at = coalesce(r.delivered_at, p_delivered_at),
        updated_at = now()
  where r.user_id = auth.uid()
    and exists (
      select 1
      from public.messages m
      where m.id = r.message_id
        and m.conversation_id = p_conversation_id
        and m.sender_id <> auth.uid()
    );
end;
$$;

revoke all on function public.chat_mark_delivered(uuid, timestamptz) from public;
grant execute on function public.chat_mark_delivered(uuid, timestamptz) to authenticated;

create or replace function public.chat_mark_seen(p_conversation_id uuid, p_seen_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public._chat_is_member(p_conversation_id, auth.uid()) then
    raise exception 'not a member';
  end if;

  update public.message_receipts r
    set seen_at = coalesce(r.seen_at, p_seen_at),
        delivered_at = coalesce(r.delivered_at, p_seen_at),
        updated_at = now()
  where r.user_id = auth.uid()
    and exists (
      select 1
      from public.messages m
      where m.id = r.message_id
        and m.conversation_id = p_conversation_id
        and m.sender_id <> auth.uid()
    );
end;
$$;

revoke all on function public.chat_mark_seen(uuid, timestamptz) from public;
grant execute on function public.chat_mark_seen(uuid, timestamptz) to authenticated;

-- Inbox RPC: one round-trip for conversation list
create or replace function public.chat_inbox(p_limit int default 50, p_before timestamptz default null)
returns table (
  conversation_id uuid,
  other_user_id uuid,
  other_display_name text,
  other_role text,
  other_e2ee_public_key_b64 text,
  last_message_id uuid,
  last_message_at timestamptz,
  last_message_sender_id uuid,
  last_message_type text,
  last_message_ciphertext_b64 text,
  last_message_nonce_b64 text,
  unread_count int
)
language sql
stable
as $$
  with my_convs as (
    select c.*,
           case when c.user1 = auth.uid() then c.user2 else c.user1 end as other_id
    from public.conversations c
    where (c.user1 = auth.uid() or c.user2 = auth.uid())
      and (p_before is null or c.last_message_at < p_before)
    order by c.last_message_at desc nulls last
    limit greatest(1, least(p_limit, 100))
  )
  select
    mc.id as conversation_id,
    mc.other_id as other_user_id,
    p.display_name as other_display_name,
    p.role as other_role,
    p.e2ee_public_key_b64 as other_e2ee_public_key_b64,
    mc.last_message_id,
    mc.last_message_at,
    m.sender_id as last_message_sender_id,
    m.type as last_message_type,
    m.ciphertext_b64 as last_message_ciphertext_b64,
    m.nonce_b64 as last_message_nonce_b64,
    coalesce(s.unread_count, 0) as unread_count
  from my_convs mc
  left join public.messages m on m.id = mc.last_message_id
  left join public.profiles p on p.id = mc.other_id
  left join public.conversation_user_state s
    on s.conversation_id = mc.id and s.user_id = auth.uid()
  order by mc.last_message_at desc nulls last;
$$;

revoke all on function public.chat_inbox(int, timestamptz) from public;
grant execute on function public.chat_inbox(int, timestamptz) to authenticated;

-- =========================================================
-- RLS
-- =========================================================
alter table public.profiles enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.message_receipts enable row level security;
alter table public.conversation_user_state enable row level security;

-- Profiles
drop policy if exists "profiles_select_all" on public.profiles;
create policy "profiles_select_all"
on public.profiles
for select
to authenticated
using (true);

drop policy if exists "profiles_insert_self" on public.profiles;
create policy "profiles_insert_self"
on public.profiles
for insert
to authenticated
with check (id = auth.uid());

drop policy if exists "profiles_update_self" on public.profiles;
create policy "profiles_update_self"
on public.profiles
for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

-- Conversations
drop policy if exists "conversations_select_member" on public.conversations;
create policy "conversations_select_member"
on public.conversations
for select
to authenticated
using (user1 = auth.uid() or user2 = auth.uid());

drop policy if exists "conversations_insert_member_pair" on public.conversations;
create policy "conversations_insert_member_pair"
on public.conversations
for insert
to authenticated
with check (
  -- must include current user and satisfy canonical ordering constraint
  (user1 = auth.uid() or user2 = auth.uid())
  and (user1 < user2)
);

drop policy if exists "conversations_update_member" on public.conversations;
create policy "conversations_update_member"
on public.conversations
for update
to authenticated
using (user1 = auth.uid() or user2 = auth.uid())
with check (user1 = auth.uid() or user2 = auth.uid());

-- Messages
drop policy if exists "messages_select_member" on public.messages;
create policy "messages_select_member"
on public.messages
for select
to authenticated
using (public._chat_is_member(conversation_id, auth.uid()));

drop policy if exists "messages_insert_sender_is_me" on public.messages;
create policy "messages_insert_sender_is_me"
on public.messages
for insert
to authenticated
with check (
  sender_id = auth.uid()
  and public._chat_is_member(conversation_id, auth.uid())
);

-- Prevent message edits/deletes from clients (immutability)
drop policy if exists "messages_update_none" on public.messages;
create policy "messages_update_none"
on public.messages
for update
to authenticated
using (false);

drop policy if exists "messages_delete_none" on public.messages;
create policy "messages_delete_none"
on public.messages
for delete
to authenticated
using (false);

-- Conversation state
drop policy if exists "conversation_state_select_self" on public.conversation_user_state;
create policy "conversation_state_select_self"
on public.conversation_user_state
for select
to authenticated
using (user_id = auth.uid() and public._chat_is_member(conversation_id, auth.uid()));

drop policy if exists "conversation_state_update_self" on public.conversation_user_state;
create policy "conversation_state_update_self"
on public.conversation_user_state
for update
to authenticated
using (user_id = auth.uid() and public._chat_is_member(conversation_id, auth.uid()))
with check (user_id = auth.uid() and public._chat_is_member(conversation_id, auth.uid()));

drop policy if exists "conversation_state_insert_self" on public.conversation_user_state;
create policy "conversation_state_insert_self"
on public.conversation_user_state
for insert
to authenticated
with check (user_id = auth.uid() and public._chat_is_member(conversation_id, auth.uid()));

-- Receipts
drop policy if exists "receipts_select_member" on public.message_receipts;
create policy "receipts_select_member"
on public.message_receipts
for select
to authenticated
using (
  exists (
    select 1
    from public.messages m
    where m.id = message_id and public._chat_is_member(m.conversation_id, auth.uid())
  )
);

drop policy if exists "receipts_update_self" on public.message_receipts;
create policy "receipts_update_self"
on public.message_receipts
for update
to authenticated
using (
  user_id = auth.uid()
  and exists (
    select 1
    from public.messages m
    where m.id = message_id and public._chat_is_member(m.conversation_id, auth.uid())
  )
)
with check (
  user_id = auth.uid()
  and exists (
    select 1
    from public.messages m
    where m.id = message_id and public._chat_is_member(m.conversation_id, auth.uid())
  )
);

-- Realtime: ensure these tables are in the realtime publication in Supabase UI:
-- conversations, messages, message_receipts, conversation_user_state

