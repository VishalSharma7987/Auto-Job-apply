# Architecture

```
 Telegram ─► Cloudflare Worker (optional) ─► GitHub repository_dispatch ─┐
     ▲                                                                    ▼
     │                                         cron (2×/day) ─► GitHub Actions (ubuntu) ─► python -m jobagent run
     │                                                                    │
     └───────────── reports / WAITING_USER + screenshot ◄─────────────────┤
                                                                          ▼
   1) Telegram commands (relay payload + getUpdates)   2) LangGraph pipeline ──► Supabase Postgres
```

## Pipeline (`pipeline/graph.py`, LangGraph `StateGraph`)
`discover → normalize → dedupe → cheap_filter → ai_match → contact → prepare → act → record → report`
(`apply` mode starts at `act`; `jobs` mode stops before sending).

| Node | Deterministic / AI | What it does | Persisted |
|---|---|---|---|
| discover | det. | runs enabled `SourceAdapter`s; a failing source is logged and skipped | event |
| normalize/dedupe | det. | `RawJob→Job`, sha256 `job_key`, in-batch duplicate removal | `scanned` stat |
| cheap_filter | det. | title allow/deny, years, location; survivors are upserted | `skipped` + reasons |
| ai_match | **LLM** | structured match, cached in `jobs.match_json`; `scoring.py` adds an explainable 0–100 score | status, reasons, score |
| contact | det. | picks ≤ cap routable jobs by score; published-recruiting-email finder (HIGH/MEDIUM) else ATS browser route | `contacts`, status |
| prepare | **LLM** + guard | email/cover letter from profile only; claims guard → regenerate → template | `applications` (UNIQUE job+route) |
| act | det. | email send (SMTP) or Playwright apply, all guarded by DRY_RUN / pause / killswitch / DB checks | status, tasks, stats |
| record/report | det. | events, daily stats, Telegram + stdout report | `events`, `daily_stats` |

Every node reads job state from the DB, so a crashed run resumes where it stopped.

## Safety layers
1. **DRY_RUN** is checked in the pipeline *and* inside `GmailSender.send` and `run_form` (defence in depth).
2. **At-most-once sending:** `UNIQUE(job_id, route)` + `SENDING` marker written before SMTP + status-machine guards (`pipeline/state.py`).
3. **Untrusted text:** `llm/sanitize.py` strips HTML/scripts and injection phrases, `prompts.py` wraps data in `<<<UNTRUSTED_…>>>` markers and tells the model not to obey it; no PII (phone/email) goes into prompts.
4. **No fabrication:** matched skills are intersected with the profile; emails are checked against `profile.skills`; contacts must literally appear on a fetched page.
5. **Blockers → human:** `apply/detectors.py` (CAPTCHA, OTP, login, anti-bot), `apply/field_mapper.py` (consequential/low-confidence → ask).
6. **Kill switch / pause** are stored in `telegram_state` and checked before every action.

## Modules
`config` (pydantic-settings) · `profile` (verified facts) · `db/` (Repository ABC; sqlite + supabase) · `llm/` (client, prompts, sanitize, fake) ·
`discovery/` (adapters, normalize, dedupe, registry, fake) · `matching/` (cheap_filter, ai_match, scoring) · `contacts/` (finder, robots) ·
`email/` (generator, gmail_smtp) · `apply/` (playwright_runner, detectors, field_mapper, strategies/) · `telegram/` (client, commands, reports) ·
`pipeline/` (state machine, graph, runner) · `main` (CLI).

## Data model
See `db/migrations/001_init.sql`: `jobs`, `contacts`, `applications` (UNIQUE job_id+route), `tasks`, `events`, `telegram_state`, `daily_stats`; RLS enabled everywhere
(the worker uses the service key; anon keys get no access).

## Extending
- New job source: subclass `SourceAdapter`, register in `discovery/registry.py`, add to `config/sources.yaml`, add a recorded fixture + respx test.
- New ATS: add a `Strategy` in `apply/strategies/` (URL rewriting, selectors) — field filling is shared.
- Different LLM: set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.
