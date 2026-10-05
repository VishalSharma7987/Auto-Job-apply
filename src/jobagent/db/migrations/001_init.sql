-- ai-job-agent initial schema (Supabase / Postgres)
create extension if not exists pgcrypto;

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  job_key text unique not null,
  company text not null,
  title text not null,
  url text,
  source text,
  location text,
  remote boolean default false,
  description text,
  requirements_json jsonb,
  posted_at timestamptz,
  discovered_at timestamptz default now(),
  status text not null default 'DISCOVERED',
  match_json jsonb,
  match_reasons text[],
  score int,
  skip_reason text
);

create table if not exists contacts (
  id uuid primary key default gen_random_uuid(),
  company text not null,
  email text not null,
  source_url text not null,
  confidence text not null,
  found_at timestamptz default now(),
  verified_at timestamptz,
  unique (company, email)
);

create table if not exists applications (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references jobs(id) on delete cascade,
  company text,
  role text,
  status text not null default 'READY',
  route text not null check (route in ('email','browser')),
  contact_id uuid references contacts(id),
  email_sent_at timestamptz,
  submitted_at timestamptz,
  application_url text,
  resume_version text,
  email_subject text,
  email_body text,
  notes text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (job_id, route)
);

create table if not exists tasks (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references jobs(id) on delete cascade,
  type text not null,
  status text not null default 'queued'
    check (status in ('queued','running','waiting_user','completed','failed')),
  retry_count int not null default 0,
  last_error text,
  payload jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists events (
  id bigserial primary key,
  ts timestamptz default now(),
  level text,
  job_id uuid,
  action text,
  detail jsonb
);

create table if not exists telegram_state (
  key text primary key,
  value text
);

create table if not exists daily_stats (
  day date primary key,
  scanned int default 0,
  qualified int default 0,
  selected int default 0,
  emails_sent int default 0,
  browser_submitted int default 0,
  waiting_user int default 0,
  failed int default 0,
  skipped int default 0,
  skip_reasons jsonb default '{}'::jsonb
);

create index if not exists idx_jobs_status on jobs(status);
create index if not exists idx_jobs_job_key on jobs(job_key);
create index if not exists idx_applications_job_id on applications(job_id);
create index if not exists idx_tasks_status on tasks(status);

-- RLS on (service-role key bypasses RLS; anon/publishable keys get no access)
alter table jobs enable row level security;
alter table contacts enable row level security;
alter table applications enable row level security;
alter table tasks enable row level security;
alter table events enable row level security;
alter table telegram_state enable row level security;
alter table daily_stats enable row level security;
