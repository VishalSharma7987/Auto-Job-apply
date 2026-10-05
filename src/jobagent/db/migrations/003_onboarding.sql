-- Telegram onboarding: personal details + resume pointers live in the DB (env stays as fallback).

alter table profile alter column data set default '{}'::jsonb;
alter table profile add column if not exists phone text;
alter table profile add column if not exists linkedin_url text;
alter table profile add column if not exists github_url text;
alter table profile add column if not exists portfolio_url text;
alter table profile add column if not exists location text;
alter table profile add column if not exists resume_path text;            -- e.g. 'default.pdf' (key inside the 'resumes' bucket)
alter table profile add column if not exists resume_updated_at timestamptz;
alter table profile add column if not exists resume_variants jsonb;       -- {"default": {"path","size","updated_at"}, "ai": {...}}
alter table profile add column if not exists onboarding_state jsonb;     -- {"step": "...", "answers": {...}} (stateless between runs)

-- Durable inbox for Telegram updates: the Cloudflare relay (and the worker itself) write here, the worker drains it in
-- update_id order. A cancelled/merged GitHub run can therefore never lose a message (resume upload, onboarding answers).
create table if not exists telegram_inbox (
  update_id bigint primary key,
  payload jsonb not null,
  received_at timestamptz default now(),
  processed_at timestamptz
);
create index if not exists idx_telegram_inbox_pending on telegram_inbox (update_id) where processed_at is null;
alter table telegram_inbox enable row level security;

-- Private bucket for resumes (service key only; max 5 MB; PDFs only)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('resumes', 'resumes', false, 5242880, array['application/pdf'])
on conflict (id) do update set public = false, file_size_limit = 5242880, allowed_mime_types = array['application/pdf'];
