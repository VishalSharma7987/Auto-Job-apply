# ai-job-agent

Zero-budget, Telegram-controlled, GitHub-Actions-hosted AI job search & application agent for **Vishal Sharma**
(junior AI / ML / agentic / full-stack developer roles; remote or Pune / Bangalore / Hyderabad).
Runs with your laptop **off**; costs **$0** (see [docs/FREE_TIER_REPORT.md](docs/FREE_TIER_REPORT.md)).

```
Telegram → Cloudflare Worker (optional) → GitHub repository_dispatch → GitHub Actions → Python worker
 discover → normalize/dedupe → cheap filter → AI match → contact → email (Gmail) / Playwright apply → Supabase → Telegram report
```

**Safe by default:** `DRY_RUN=true` (nothing is sent or submitted), idempotent (one send per job), no CAPTCHA/OTP/login bypass,
no guessed HR emails, no invented experience, job text treated as untrusted. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Before the first real run
1. `profile/profile.yaml` is filled from section 2 of [your requirements doc](docs/REQUIREMENTS.md) (skills, project areas, roles, locations).
   The doc names no concrete projects/education, so those stay empty — nothing is invented ([D-002](docs/DECISIONS.md)). Add true facts there if you want them used.
2. Create the accounts/secrets and follow the manual test order in [docs/SETUP.md](docs/SETUP.md#manual-test-order-do-these-in-order-stop-at-the-first-problem).
3. How the code maps to the requirements doc: [docs/REQUIREMENTS_AUDIT.md](docs/REQUIREMENTS_AUDIT.md).

## Quick start (offline demo, no accounts)
```powershell
.\scripts\run_local.ps1 -Setup -Fake      # Windows;   bash: SETUP=1 FAKE=1 ./scripts/run_local.sh
```
Prints the daily report (section 18 format) for 20 fake jobs, writes drafts to a local SQLite DB and logs `WOULD SEND/SUBMIT`.
Manual equivalent:
```
pip install -e ".[dev]" && playwright install chromium
FAKE_MODE=true DRY_RUN=true DB_BACKEND=sqlite python -m jobagent run --mode full
pytest -q && ruff check .
```

## CLI
`python -m jobagent run --mode jobs|apply|status|report|full [--job-id <id-prefix>]`
Pending Telegram commands are processed first on every run.

## Deploy
1. Push to GitHub, add the secrets/variables listed in [docs/SETUP.md](docs/SETUP.md#3-github-actions-secrets-and-variables).
2. Run the **agent** workflow manually (`full`), check Telegram, then set variable `DRY_RUN=false` when happy.
3. Optional instant commands: [cloudflare/README.md](cloudflare/README.md).

Schedule: 09:00 and 18:00 IST (edit the two crons in `.github/workflows/agent.yml`).

## Telegram commands
`/start /help /jobs /apply /status /report /pause /resume /retry /approve <task_id> /skip <job_id> /history /settings /killswitch`
(only `TELEGRAM_ALLOWED_CHAT_ID` is served).

## Repo map
`src/jobagent/` code · `config/` companies (verified board slugs) + sources · `profile/` verified facts · `cloudflare/` relay ·
`.github/workflows/` agent + CI · `scripts/` local runners · `tests/` 200+ tests with recorded API fixtures · `docs/`.

## Verification status (what was actually checked)
| Item | Status |
|---|---|
| `pip install -e .` + `playwright install chromium` | ✅ done in this environment |
| `run --mode full` offline (FAKE_MODE, sqlite, DRY_RUN) prints the section-18 report | ✅ |
| Real adapters parse real API shapes | ✅ recorded fixtures from the live APIs + respx tests; company slugs probed live; one real-profile pipeline run fetched live jobs end-to-end up to the LLM call (no key, so it stopped there) |
| Idempotency (2 runs ⇒ 0 duplicates; crash/retry cases) | ✅ tests |
| Contact finder never guesses | ✅ tests |
| Playwright strategies | ✅ driven against local HTML fixtures (Chromium); **not** run against live ATS forms |
| Supabase schema (migrations 001 + 002) applied + constraints verified | ✅ via MCP (8 tables, RLS on) |
| `supabase_repo.py` | ✅ shared contract test against an in-memory PostgREST fake; ⚠️ real-DB run is opt-in: `pytest tests/test_supabase_integration.py` with `SUPABASE_URL`/`SUPABASE_KEY` |
| `cloudflare/worker.js` | ⚠️ not executed (no Node here) |
| Workflow YAML | ✅ parses; ⚠️ not run on GitHub yet (push + check Actions, see SETUP 3b) |

## Known limitations
- Real ATS forms vary; unknown required questions go to you (`WAITING_USER`) rather than being guessed. Custom widgets (e.g. React-select) may need strategy tweaks after reviewing dry-run screenshots.
- Free LLMs are rate-limited; matching is capped per run and resumes next run.
- Contact discovery only works where a recruiting address is *published*; many jobs will use the browser route or `manual_apply`.
- Greenhouse/Lever/Ashby only; other sites need `ENABLE_GENERIC_APPLY=true` (experimental).
