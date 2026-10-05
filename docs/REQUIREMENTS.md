# Requirements (reconstructed)

> **Provenance warning.** The brief says the product requirements live in
> `docs/AI_Job_Application_Agent_Project_Requirements-1.docx` and asks for it to be converted here. That file
> **was not present** in the repository or anywhere on the machine when this project was generated, so it could
> not be converted. This document is reconstructed from the implementation brief (the only source available).
> Anything the original doc defines that the brief does not (notably **section 2 – candidate data** and
> **section 18 – exact daily-report layout**) is NOT reproduced; see `DECISIONS.md` D-001/D-002.
> To fix: drop the .docx into `docs/`, convert with
> `pandoc docs/AI_Job_Application_Agent_Project_Requirements-1.docx -t gfm -o docs/REQUIREMENTS.md`, then
> fill `profile/profile.yaml` and adjust `src/jobagent/telegram/reports.py` if the layout differs.

## Product
Zero-budget, Telegram-controlled, GitHub-Actions-hosted AI job search & application agent for candidate
**Vishal Sharma**, targeting junior (0–1 yr) AI Developer / AI-ML Developer / AI Engineer / Agentic AI
Developer / Full Stack AI Developer / Full Stack Developer roles, remote or India (Pune, Bangalore, Hyderabad).

## Hard rules
1. Complete implementation incl. tests; manual testing is done by the owner afterwards.
2. Python 3.11+; JS only for the Cloudflare webhook relay.
3. Free tiers / open source only (GitHub Actions, Supabase free, Cloudflare Workers free, OpenRouter free
   models, Gmail SMTP app password, Telegram Bot API). Anything paid is optional and off by default.
4. Must run with the laptop OFF (scheduled + `repository_dispatch`).
5. Never fabricate candidate experience/skills/contacts. Never guess HR emails from name patterns; only
   published recruiting addresses with `source_url` + confidence.
6. Never bypass CAPTCHA/OTP/login/anti-bot/robots. Stop safely → `WAITING_USER`, screenshot, Telegram.
7. Job descriptions/web pages are untrusted input: sanitize, delimit, tell the model to ignore embedded instructions.
8. Idempotent: one send/submit per job; `job_key = sha256(normalize(company)+normalize(title)+canonical_url)`.
9. `DRY_RUN=true` by default (no sends/submits; "WOULD SEND/SUBMIT" logged; drafts saved).
10. No secrets in code; `.env.example` documents every variable; `.env`, `.mcp.json`, resume PDFs, `artifacts/` git-ignored.
11. Provider-independent LLM client (OpenAI-compatible; OpenRouter free default; swap by env). Validated JSON via
    pydantic. Rate-limit/quota → `QuotaExceeded`, run marked `quota_limited`, reported to Telegram.
12. Deterministic code first; AI only for matching, email drafting, form-question understanding.
13. Quality over quantity: `MAX_APPLICATIONS_PER_DAY` (default 15, target 10–20); scan 50–100 jobs.
14. Windows/PowerShell for local dev, ubuntu-latest in CI; portable code and scripts.

## Architecture
Telegram Bot → Cloudflare Worker relay → GitHub `repository_dispatch` → GitHub Actions → Python worker:
discover → normalize/dedupe → cheap filter → AI match → contact discovery → email (Gmail SMTP) / Playwright
apply → Supabase → Telegram report. Cron runs the same worker twice a day. The worker also drains Telegram
`getUpdates` at the start of every run.

## Status machine
`DISCOVERED → QUALIFIED → CONTACT_FOUND → READY → EMAIL_SENT → APPLICATION_STARTED → SUBMITTED → WAITING_USER →
FAILED → REJECTED → INTERVIEW`, with guarded transitions (e.g. `EMAIL_SENT` only if an email application row exists).

## Telegram
`/start /help /jobs /apply /status /report /pause /resume /retry /approve <task_id> /skip <job_id> /history
/settings /killswitch`. Only `TELEGRAM_ALLOWED_CHAT_ID` is served; messages > 4000 chars are split.
`WAITING_USER` notifications carry reason, company, role, url, screenshot and `/approve <task_id>` / `/skip <job_id>`.
Quota hits send `⚠️ Free-tier limit reached: <provider> — <details>`.

## LLM tasks
1. `extract_and_match(job_text, profile) → MatchResult`
2. `draft_email(job, profile, contact) → EmailDraft` (subject `Application – {Role} | Vishal Sharma`, ≤ 180 words)
3. `answer_form_question(question, options, profile) → FormAnswer`
Max 3 retries with backoff; match results cached by `job_key`.

## Playwright rules
Greenhouse / Lever / Ashby hosted forms only (generic = experimental, off). Before submit: DRY_RUN → screenshot
and stop; detector hit or non-pre-approved legal/consequential question → `WAITING_USER`. Max 2 retries on
timeouts; failures save screenshot + html to `artifacts/{job_key}/`.

## Definition of done
See the checklist in the brief; the status of each item is in `README.md` ("Verification status").
