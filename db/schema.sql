-- CallMoves tables in Supabase project nzviiorrlsdtwzvzodpg. The project holds unrelated tables, so every object
-- here carries the callmoves_ prefix. Idempotent: rerun with
--   supabase db query --remote=nzviiorrlsdtwzvzodpg --yes -f db/schema.sql

-- Processed calls. summary feeds the library, data is the full CallOutput the replay page reads.
create table if not exists public.callmoves_calls (
  id text primary key,
  ticker text not null,
  quarter text not null,
  company text,
  call_start timestamptz not null,
  summary jsonb not null,
  data jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.callmoves_calls enable row level security;
drop policy if exists "callmoves_calls public read" on public.callmoves_calls;
create policy "callmoves_calls public read" on public.callmoves_calls for select to anon, authenticated using (true);

-- Calls submitted from the web form, processed by the GitHub Actions worker. No policies: only the service role
-- (the web API route and the worker) reads or writes, so ip_hash and URLs never reach the browser directly.
create table if not exists public.callmoves_submissions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  ticker text not null check (ticker ~ '^[A-Z][A-Z.]{0,5}$'),
  quarter text not null check (quarter ~ '^Q[1-4]-20[0-9]{2}$'),
  company text check (char_length(company) <= 80),
  etf text not null check (etf ~ '^[A-Z]{2,5}$'),
  call_start timestamptz not null,
  audio_url text not null check (audio_url ~ '^https://' and char_length(audio_url) <= 500),
  release_url text not null check (release_url ~ '^https://' and char_length(release_url) <= 500),
  prior_url text check (prior_url ~ '^https://' and char_length(prior_url) <= 500),
  ip_hash text not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  error text,
  call_id text,
  started_at timestamptz,
  finished_at timestamptz
);
alter table public.callmoves_submissions enable row level security;
create index if not exists callmoves_submissions_ip on public.callmoves_submissions (ip_hash, created_at);
create index if not exists callmoves_submissions_status on public.callmoves_submissions (status, created_at);

-- Claims the oldest queued submission for 1 worker run. A run that died mid-job leaves a row running, so rows
-- running for over 6 hours (the GitHub Actions job limit) go back to the queue first.
create or replace function public.callmoves_claim_submission() returns setof public.callmoves_submissions
language plpgsql security definer set search_path = public as $$
begin
  update callmoves_submissions set status = 'queued', started_at = null
    where status = 'running' and started_at < now() - interval '6 hours';
  return query
    update callmoves_submissions set status = 'running', started_at = now()
    where id = (select id from callmoves_submissions where status = 'queued'
                order by created_at limit 1 for update skip locked)
    returning *;
end $$;
revoke all on function public.callmoves_claim_submission() from public, anon, authenticated;
grant execute on function public.callmoves_claim_submission() to service_role;
