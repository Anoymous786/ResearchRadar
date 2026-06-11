-- Supabase-only 1:1 E2EE chat schema (public schema)
-- Stores ONLY ciphertext + nonce. No plaintext is ever stored.
--
-- Apply in Supabase SQL editor (or as a migration).

begin;

-- Required for gen_random_uuid()
create extension if not exists pgcrypto;

-- ---------- Profiles ----------
-- Public profile + E2EE public key. Private key never leaves the browser.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 80),
  role text not null check (role in ('student', 'faculty')),
  e2ee_public_key_b64 text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function public.tg_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_profiles_updated_at on public.profiles;
create trigger set_profiles_updated_at
before update on public.profiles
for each row execute function public.tg_set_updated_at();

-- ---------- Conversations (1:1 only) ----------
create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  user1 uuid not null references auth.users(id) on delete cascade,
  user2 uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  -- Enforce canonical ordering (sorted user ids) and prevent self-chat
  constraint conversations_user_order check (user1 < user2),
  constraint conversations_not_self check (user1 <> user2),
  constraint conversations_unique_pair unique (user1, user2)
);

create index if not exists conversations_user1_idx on public.conversations(user1);
create index if not exists conversations_user2_idx on public.conversations(user2);

-- ---------- Messages (ciphertext only; immutable) ----------
create table if not exists public.messages (
  id uuid primary key,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  sender_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  client_created_at timestamptz null,
  type text not null check (type in ('text', 'voice')),
  ciphertext_b64 text not null check (char_length(ciphertext_b64) > 0),
  nonce_b64 text not null check (char_length(nonce_b64) > 0)
);

-- Required performance index
create index if not exists messages_conversation_created_at_desc_idx
  on public.messages (conversation_id, created_at desc, id desc);

create index if not exists messages_conversation_sender_created_at_idx
  on public.messages (conversation_id, sender_id, created_at desc, id desc);

-- ---------- Per-user state (unread calculations + fast inbox) ----------
create table if not exists public.conversation_user_state (
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  last_read_at timestamptz null,
  last_seen_at timestamptz null,
  last_delivered_at timestamptz null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (conversation_id, user_id)
);

drop trigger if exists set_conversation_user_state_updated_at on public.conversation_user_state;
create trigger set_conversation_user_state_updated_at
before update on public.conversation_user_state
for each row execute function public.tg_set_updated_at();

create index if not exists conversation_user_state_user_idx
  on public.conversation_user_state(user_id, updated_at desc);

-- ---------- Message receipts (optional fidelity; required table) ----------
-- One row per (message, recipient). Created automatically on message insert.
create table if not exists public.message_receipts (
  message_id uuid not null references public.messages(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  delivered_at timestamptz null,
  seen_at timestamptz null,
  read_at timestamptz null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (message_id, user_id)
);

drop trigger if exists set_message_receipts_updated_at on public.message_receipts;
create trigger set_message_receipts_updated_at
before update on public.message_receipts
for each row execute function public.tg_set_updated_at();

create index if not exists message_receipts_user_idx
  on public.message_receipts(user_id, updated_at desc);

-- ---------- Helper function ----------
create or replace function public.is_conversation_participant(p_conversation_id uuid, p_user_id uuid)
returns boolean
language sql
stable
as $$
  select exists(
    select 1
    from public.conversations c
    where c.id = p_conversation_id
      and (c.user1 = p_user_id or c.user2 = p_user_id)
  );
$$;

-- ---------- Immutable messages guard (defense-in-depth) ----------
create or replace function public.tg_prevent_message_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'messages are immutable';
end;
$$;

drop trigger if exists prevent_messages_update on public.messages;
create trigger prevent_messages_update
before update on public.messages
for each row execute function public.tg_prevent_message_mutation();

drop trigger if exists prevent_messages_delete on public.messages;
create trigger prevent_messages_delete
before delete on public.messages
for each row execute function public.tg_prevent_message_mutation();

-- ---------- Create default state rows when a conversation is created ----------
create or replace function public.tg_conversation_init_state()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.conversation_user_state(conversation_id, user_id)
  values (new.id, new.user1), (new.id, new.user2)
  on conflict do nothing;
  return new;
end;
$$;

drop trigger if exists conversation_init_state on public.conversations;
create trigger conversation_init_state
after insert on public.conversations
for each row execute function public.tg_conversation_init_state();

-- ---------- Create receipt row for the recipient when a message is inserted ----------
create or replace function public.tg_message_create_receipt()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user1 uuid;
  v_user2 uuid;
  v_recipient uuid;
begin
  select c.user1, c.user2 into v_user1, v_user2
  from public.conversations c
  where c.id = new.conversation_id;

  if new.sender_id = v_user1 then
    v_recipient := v_user2;
  elsif new.sender_id = v_user2 then
    v_recipient := v_user1;
  else
    -- Should be impossible with RLS; keep safe.
    raise exception 'sender not in conversation';
  end if;

  insert into public.message_receipts(message_id, user_id, delivered_at)
  values (new.id, v_recipient, now())
  on conflict do nothing;

  return new;
end;
$$;

drop trigger if exists message_create_receipt on public.messages;
create trigger message_create_receipt
after insert on public.messages
for each row execute function public.tg_message_create_receipt();

-- ---------- RLS ----------
alter table public.profiles enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.conversation_user_state enable row level security;
alter table public.message_receipts enable row level security;

-- PROFILES: public read; user can insert/update only self.
drop policy if exists profiles_public_read on public.profiles;
create policy profiles_public_read
on public.profiles for select
using (true);

drop policy if exists profiles_self_insert on public.profiles;
create policy profiles_self_insert
on public.profiles for insert
with check (auth.uid() = id);

drop policy if exists profiles_self_update on public.profiles;
create policy profiles_self_update
on public.profiles for update
using (auth.uid() = id)
with check (auth.uid() = id);

-- CONVERSATIONS: participants only; insert only if auth.uid is participant and ordering is canonical.
drop policy if exists conversations_participant_read on public.conversations;
create policy conversations_participant_read
on public.conversations for select
using (auth.uid() = user1 or auth.uid() = user2);

drop policy if exists conversations_participant_insert on public.conversations;
create policy conversations_participant_insert
on public.conversations for insert
with check (
  (auth.uid() = user1 or auth.uid() = user2)
  and user1 < user2
  and user1 <> user2
);

-- No updates/deletes for conversations (not required; keep strict)
drop policy if exists conversations_no_update on public.conversations;
create policy conversations_no_update
on public.conversations for update
using (false);

drop policy if exists conversations_no_delete on public.conversations;
create policy conversations_no_delete
on public.conversations for delete
using (false);

-- MESSAGES: participants can read; insert only if sender is auth.uid and participant. No update/delete.
drop policy if exists messages_participant_read on public.messages;
create policy messages_participant_read
on public.messages for select
using (public.is_conversation_participant(conversation_id, auth.uid()));

drop policy if exists messages_participant_insert on public.messages;
create policy messages_participant_insert
on public.messages for insert
with check (
  sender_id = auth.uid()
  and public.is_conversation_participant(conversation_id, auth.uid())
);

drop policy if exists messages_no_update on public.messages;
create policy messages_no_update
on public.messages for update
using (false);

drop policy if exists messages_no_delete on public.messages;
create policy messages_no_delete
on public.messages for delete
using (false);

-- CONVERSATION_USER_STATE: participants can read their own row; can upsert/update only own row.
drop policy if exists cus_self_read on public.conversation_user_state;
create policy cus_self_read
on public.conversation_user_state for select
using (user_id = auth.uid() and public.is_conversation_participant(conversation_id, auth.uid()));

drop policy if exists cus_self_insert on public.conversation_user_state;
create policy cus_self_insert
on public.conversation_user_state for insert
with check (user_id = auth.uid() and public.is_conversation_participant(conversation_id, auth.uid()));

drop policy if exists cus_self_update on public.conversation_user_state;
create policy cus_self_update
on public.conversation_user_state for update
using (user_id = auth.uid() and public.is_conversation_participant(conversation_id, auth.uid()))
with check (user_id = auth.uid() and public.is_conversation_participant(conversation_id, auth.uid()));

drop policy if exists cus_no_delete on public.conversation_user_state;
create policy cus_no_delete
on public.conversation_user_state for delete
using (false);

-- MESSAGE_RECEIPTS: recipient can read/update their receipts; inserts happen via trigger (security definer).
drop policy if exists receipts_self_read on public.message_receipts;
create policy receipts_self_read
on public.message_receipts for select
using (user_id = auth.uid());

drop policy if exists receipts_self_update on public.message_receipts;
create policy receipts_self_update
on public.message_receipts for update
using (user_id = auth.uid())
with check (user_id = auth.uid());

drop policy if exists receipts_no_insert on public.message_receipts;
create policy receipts_no_insert
on public.message_receipts for insert
with check (false);

drop policy if exists receipts_no_delete on public.message_receipts;
create policy receipts_no_delete
on public.message_receipts for delete
using (false);

-- ---------- RPCs ----------
-- Inbox: peer profile + last message + unread count. Optimized (no N+1).
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
security invoker
set search_path = public
as $$
with my_conversations as (
  select
    c.id as conversation_id,
    case when c.user1 = auth.uid() then c.user2 else c.user1 end as other_user_id
  from public.conversations c
  where auth.uid() is not null
    and (c.user1 = auth.uid() or c.user2 = auth.uid())
),
last_msg as (
  select
    mc.conversation_id,
    m.id as last_message_id,
    m.created_at as last_message_at,
    m.sender_id as last_message_sender_id,
    m.type as last_message_type,
    m.ciphertext_b64 as last_message_ciphertext_b64,
    m.nonce_b64 as last_message_nonce_b64
  from my_conversations mc
  left join lateral (
    select *
    from public.messages m
    where m.conversation_id = mc.conversation_id
    order by m.created_at desc, m.id desc
    limit 1
  ) m on true
),
state as (
  select
    s.conversation_id,
    s.last_read_at
  from public.conversation_user_state s
  where s.user_id = auth.uid()
)
select
  mc.conversation_id,
  mc.other_user_id,
  p.display_name as other_display_name,
  p.role as other_role,
  p.e2ee_public_key_b64 as other_e2ee_public_key_b64,
  lm.last_message_id,
  lm.last_message_at,
  lm.last_message_sender_id,
  lm.last_message_type,
  lm.last_message_ciphertext_b64,
  lm.last_message_nonce_b64,
  coalesce((
    select count(*)::int
    from public.messages m2
    left join state s on s.conversation_id = mc.conversation_id
    where m2.conversation_id = mc.conversation_id
      and m2.sender_id = mc.other_user_id
      and (s.last_read_at is null or m2.created_at > s.last_read_at)
  ), 0) as unread_count
from my_conversations mc
join public.profiles p on p.id = mc.other_user_id
join last_msg lm on lm.conversation_id = mc.conversation_id
where (p_before is null or lm.last_message_at is null or lm.last_message_at < p_before)
order by lm.last_message_at desc nulls last, mc.conversation_id desc
limit greatest(p_limit, 1);
$$;

-- Mark delivered/seen/read are RPCs to avoid the client updating tables directly.
-- They upsert conversation_user_state and update relevant receipts for the current user.

create or replace function public.chat_mark_delivered(p_conversation_id uuid, p_delivered_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;
  if not public.is_conversation_participant(p_conversation_id, auth.uid()) then
    raise exception 'not a participant';
  end if;

  insert into public.conversation_user_state(conversation_id, user_id, last_delivered_at)
  values (p_conversation_id, auth.uid(), p_delivered_at)
  on conflict (conversation_id, user_id)
  do update set last_delivered_at = greatest(public.conversation_user_state.last_delivered_at, excluded.last_delivered_at);
end;
$$;

create or replace function public.chat_mark_seen(p_conversation_id uuid, p_seen_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;
  if not public.is_conversation_participant(p_conversation_id, auth.uid()) then
    raise exception 'not a participant';
  end if;

  insert into public.conversation_user_state(conversation_id, user_id, last_seen_at)
  values (p_conversation_id, auth.uid(), p_seen_at)
  on conflict (conversation_id, user_id)
  do update set last_seen_at = greatest(public.conversation_user_state.last_seen_at, excluded.last_seen_at);

  -- Update all my receipts in this conversation (best-effort)
  update public.message_receipts r
  set seen_at = coalesce(r.seen_at, p_seen_at)
  from public.messages m
  where r.user_id = auth.uid()
    and r.message_id = m.id
    and m.conversation_id = p_conversation_id;
end;
$$;

create or replace function public.chat_mark_read(p_conversation_id uuid, p_read_at timestamptz default now())
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;
  if not public.is_conversation_participant(p_conversation_id, auth.uid()) then
    raise exception 'not a participant';
  end if;

  insert into public.conversation_user_state(conversation_id, user_id, last_read_at)
  values (p_conversation_id, auth.uid(), p_read_at)
  on conflict (conversation_id, user_id)
  do update set last_read_at = greatest(public.conversation_user_state.last_read_at, excluded.last_read_at);

  update public.message_receipts r
  set read_at = coalesce(r.read_at, p_read_at)
  from public.messages m
  where r.user_id = auth.uid()
    and r.message_id = m.id
    and m.conversation_id = p_conversation_id;
end;
$$;

-- Lock down execution: allow authenticated users to call RPCs.
revoke all on function public.chat_inbox(int, timestamptz) from public;
grant execute on function public.chat_inbox(int, timestamptz) to authenticated;

revoke all on function public.chat_mark_delivered(uuid, timestamptz) from public;
grant execute on function public.chat_mark_delivered(uuid, timestamptz) to authenticated;

revoke all on function public.chat_mark_seen(uuid, timestamptz) from public;
grant execute on function public.chat_mark_seen(uuid, timestamptz) to authenticated;

revoke all on function public.chat_mark_read(uuid, timestamptz) from public;
grant execute on function public.chat_mark_read(uuid, timestamptz) to authenticated;

commit;

