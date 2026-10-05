# Decisions & assumptions

Each entry: what was ambiguous or impossible → what was chosen → why.

**D-001 - Requirements doc (RESOLVED).** The .docx was missing in the first pass; it is now `docs/AI_Job_Application_Agent_Project_Requirements-1.docx`
and fully converted into `docs/REQUIREMENTS.md`. The daily report follows its section 18 exactly (`telegram/reports.py`,
`test_report_format.py`); the audit is in `REQUIREMENTS_AUDIT.md`.

**D-002 - Candidate data (RESOLVED, nothing invented).** `profile/profile.yaml` is filled **only** from section 2 of the doc: name, target
roles, "0-1 year" experience *level* (kept as text - the doc gives a target range, not a fact, so no numeric `experience_years`),
skills, project *areas*, preferred locations and the daily target. The doc names no concrete projects, education or employers, so
`projects`, `education`, `experience` stay empty and email drafts cite the project areas instead. `profile.sample.yaml` is only used by tests.
Add real project names later if you want them mentioned.

**D-003 – Telegram commands and webhooks.** `getUpdates` returns HTTP 409 while a webhook is active, so the
"both paths" design needs care: the Cloudflare relay forwards the whole Telegram `update` inside
`client_payload.update`; the worker reads it from `GITHUB_EVENT_PATH`. The cron path uses `getUpdates` (offset kept in
`telegram_state.last_update_id`); a 409 is swallowed. Updates are de-duplicated by `update_id`, so both paths
can coexist.

**D-004 – DB-level filtering before storage.** Big boards (Databricks ~900 jobs, Stripe ~700 …) would flood the free
500 MB Supabase DB. Order is normalize → in-batch dedupe → cheap filter → **then** upsert survivors. Rejected-by-rule
jobs are only counted in `daily_stats` (skipped + skip_reasons). DB-level dedupe is the unique `job_key`.

**D-005 - One route per job.** Email if a HIGH/MEDIUM published recruiting address exists (and that address was not mailed in the last 14 days), else browser apply if the
URL is a supported ATS, else the job becomes `WAITING_USER` with a `manual_apply` task (apply yourself, then `/skip`) and is counted as *skipped: no suitable application route* (section 18 wording), not as 'waiting for manual action'.
Schema still allows both routes (`UNIQUE(job_id, route)`).

**D-006 – Cron from a repo variable is impossible.** GitHub does not allow `${{ vars.* }}` inside `on.schedule`. The two
crons (03:30 and 12:30 UTC = 09:00/18:00 IST) are static in `agent.yml`; edit them there. `CRON_SCHEDULE` is not used.

**D-007 – `GITHUB_URL` secret name.** GitHub rejects secret names starting with `GITHUB_`. The secret is
`CANDIDATE_GITHUB_URL`; the config also accepts `GITHUB_URL` for local `.env` files.

**D-008 – "At most once" email sending.** Before SMTP the application row is set to `SENDING`; on success
`SENT` + `email_sent_at`. If a run dies in between, the next run does **not** resend: the job goes to `WAITING_USER`
("check your Sent folder"; `/approve` = "it was not sent, go ahead", `/skip` = done). A plain SMTP exception resets the row to
`READY` and the job to `FAILED` (retryable with `/retry`). Residual risk: SMTP accepted the mail but raised on close → one retry could duplicate.

**D-009 – Submit clicked but no confirmation page.** Treated as `WAITING_USER` ("verify manually"), never retried, because the
application may have been received.

**D-010 – FAKE_MODE forces DRY_RUN.** Fake data must never trigger real sends, so `FAKE_MODE=true` overrides `DRY_RUN=false`.

**D-011 – Dry-run re-runs.** The daily cap counts application rows created that UTC day, so repeated runs are idempotent.
Dry-run browser rehearsals are marked (`notes: dry_run…`) and skipped on later runs.

**D-012 – LLM choice.** Default `nvidia/nemotron-3-super-120b-a12b:free` (JSON-mode capable, checked against the OpenRouter
models API on 2026-10-05). Free model IDs change often; override with `LLM_MODEL`. HTTP 429 is retried (2 backoffs) and then
becomes `QuotaExceeded`; HTTP 402 is immediately `QuotaExceeded`. At most `MAX_AI_MATCHES_PER_RUN` (30) uncached matches
per run; the rest stay `DISCOVERED` for the next run. After a quota hit, emails fall back to the deterministic template.

**D-013 – Cheap filter strictness.** Titles must match an AI/ML/full-stack/developer allow-list and contain none of
senior/staff/principal/lead/manager/architect/director/head/VP/intern/sales/…; postings whose *minimum* required experience is
> 2 years (e.g. "3+ years", "5+ years of experience") are dropped; "1-3 years" passes. Internships are excluded (change `TITLE_DENY`).
Location: remote (unless "US only"-style restrictions) or an India city.

**D-014 – Email guard vocabulary.** A technology named in a draft that is not in `profile.skills`/project tech is rejected
(regenerate once with feedback, then deterministic template). The policed list is finite (`email/generator.py::POLICED_TECH`);
ambiguous words like "go" are deliberately excluded.

**D-015 – Contact finder scope.** A page is fetched only if robots.txt allows it; max 4 pages (job page, company homepage,
careers page, contact page). Only the job page (HIGH) and careers-like pages (MEDIUM) yield usable addresses; the generic
homepage/contact pages are LOW and ignored. Local parts must be a recruiting role (+ optional region suffix), never a person; free-mail
domains are rejected. The homepage is only visited if the company's website is known (`website:` in `companies.yaml`).

**D-016 – robots.txt for ATS forms.** The Playwright runner also checks robots.txt of the form URL; a disallow → `WAITING_USER`.

**D-017 – Secrets size limit.** A GitHub secret holds ≤ 48 KB; a base64 resume must therefore be ≲ 35 KB. Compress the PDF if needed.

**D-018 – API attribution.** RemoteOK/Jobicy/Remotive ask for attribution and not to hide the origin link: every job keeps its
original URL and `source`. Remotive asks for few requests/day (≤ 3 searches × 2 runs/day here).

**D-019 - Not verified live (updated).** `supabase_repo.py` has not been executed against Supabase from this sandbox (the service key was
deliberately not exposed to the build session); the **schema** was applied and its constraints/upsert semantics verified through MCP
SQL. First real run: check `/status`. `worker.js` was not executed (no Node available); it is ~30 lines of standard Fetch API.

**D-020 - Supabase instead of Cloudflare D1** (the doc's section 6 names D1; the brief replaced it). Postgres has a first-class Python client, constraints for
idempotency (`UNIQUE(job_id, route)`) and an MCP for migrations. The Cloudflare Worker stays as a ~30-line webhook relay only.

**D-021 - Privacy of failure artifacts.** Screenshots of filled forms contain name/phone/email. The workflow therefore never uploads
`pre_submit/submitted/unconfirmed` shots, keeps artifacts 1 day, and the same images go only to your private Telegram chat. A **private repo**
is recommended (2,000 free Actions minutes/month is plenty: ~600-900 min/month expected).

**D-022 - Job sources.** Wellfound, Indeed, Internshala, Naukri and LinkedIn are not scraped: their terms/robots/anti-bot measures forbid automated access, and the doc
(section 9) says not to bypass them. Used instead: official ATS JSON APIs (Greenhouse, Lever, Ashby), public aggregators (Remotive, RemoteOK, Arbeitnow, Jobicy) and a
robots-aware JSON-LD adapter for any company career page you add to `config/companies.yaml`.

**D-023 - Already-contacted rules (section 5).** (a) Same normalised company+role as any sent/submitted application => `REJECTED / already_contacted`.
(b) A recruiting address that received an email in the last 14 days is not used again (falls back to the ATS form or `manual_apply`).
(c) Jobs seen on earlier runs count as `duplicate` in the daily report.

**D-024 - Resume variants (section 5).** Optional `resume_ai.pdf` / `resume_fullstack.pdf` beside `resume.pdf` (git-ignored); AI-flavoured titles use the first,
pure full-stack titles the second, everything else the default. Only the default is needed.

**D-025 - Repository testing.** `supabase_repo.py` runs in CI against an in-memory PostgREST fake (same unique constraints/defaults as the SQL) via one shared
contract (`tests/test_repository_contract.py`); the identical contract runs against the real project with `tests/test_supabase_integration.py` when
`SUPABASE_URL`/`SUPABASE_KEY` are set (opt-in, cleans up after itself).

**D-026 - Git remote.** At one point the working tree's `origin` pointed at `.../ai-job-agent.git` (not found); it now points at the repository named in the rules,
`https://github.com/VishalSharma7987/Auto-Job-apply.git`, and pushes to `main` work. `gh` is not installed, so repository Variables and the CI check are manual (SETUP.md 3b).
