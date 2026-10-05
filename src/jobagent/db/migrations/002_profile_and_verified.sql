-- Section 14 of the requirements: the database also keeps the verified candidate profile.
-- Holds ONLY non-secret facts (skills, projects, education, preferences). No phone/email/links.
create table if not exists profile (
  key text primary key,              -- 'default'
  data jsonb not null,
  updated_at timestamptz default now()
);
alter table profile enable row level security;

-- contacts.verified_at: when the worker last saw the address on the source page
alter table contacts alter column verified_at set default now();
