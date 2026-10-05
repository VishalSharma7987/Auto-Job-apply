# Setup guide

Time: ~30 minutes. Cost: $0. Do the steps in order; the agent stays in **DRY_RUN** until you flip it.

## 0. Review your profile
`profile/profile.yaml` (skills / roles / project areas only) is already filled from section 2 of the requirements doc (skills, project areas, target roles, locations,
experience *level*). The doc names no concrete projects/education/employers, so those lists are empty - add them (true facts only) if you want
the agent to be able to mention them. Phone, email and links come from env/secrets, never from this file. `legal_prefs` controls what may be
answered automatically on forms: `work_authorization_india: true` (auto "Yes" for "authorised to work in India"); `needs_sponsorship`,
`relocation`, `salary` stay `unknown/ask` (the agent asks you); `accept_terms: true` lets it tick consent/privacy checkboxes.

## 1. Accounts (all free)
| Service | What to create |
|---|---|
| Telegram | Talk to **@BotFather** → `/newbot` → copy the **bot token**. Talk to **@userinfobot** → copy your numeric **chat id**. Send your bot `/start` once. |
| Supabase | Project already exists (ref in `.mcp.json`). **Project Settings → API**: copy **Project URL** and the **service_role / secret key**. The schema (`src/jobagent/db/migrations/001_init.sql`) is already applied; to re-apply paste it in the SQL editor. |
| OpenRouter | https://openrouter.ai/keys → create a key (free models need no credit). |
| Gmail | Enable 2-Step Verification → https://myaccount.google.com/apppasswords → create an **app password** (16 chars). |
| GitHub | Create a repo (public = unlimited Actions minutes) and push this folder. |

## 2. Local dry run (no accounts needed)
```powershell
.\scripts\run_local.ps1 -Setup -Fake          # installs deps + Chromium, runs offline demo, prints the daily report
python -m pytest -q                            # (inside .venv) 200+ tests
```
Bash: `SETUP=1 FAKE=1 ./scripts/run_local.sh`.

To try real discovery locally without sending anything: copy `.env.example` → `.env`, set `DB_BACKEND=sqlite`,
`LLM_API_KEY=…`, keep `DRY_RUN=true`, fill your profile, then `.\scripts\run_local.ps1`.

## 3. GitHub Actions secrets and variables
Repo → Settings → Secrets and variables → Actions.

**Required secrets (9)**
| Secret | What |
|---|---|
| `SUPABASE_URL` | project URL |
| `SUPABASE_KEY` | service-role / secret key (also used for the private resume bucket) |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_ALLOWED_CHAT_ID` | your numeric chat id - the only chat that may upload a resume, run `/setup` or give commands |
| `OPENROUTER_API_KEY` | (or name it `LLM_API_KEY`) |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | e.g. `nvidia/nemotron-3-super-120b-a12b:free` |
| `GMAIL_ADDRESS` | sending account |
| `GMAIL_APP_PASSWORD` | 16-character Gmail app password |

**Optional secrets** - not needed any more, only fallbacks when the database has no value (DB first, env second):
`CANDIDATE_GITHUB_URL` *(not `GITHUB_URL`: GitHub forbids that prefix)*, `CANDIDATE_PHONE`, `LINKEDIN_URL`, `PORTFOLIO_URL`, `RESUME_PDF_B64`.

**Variables:** `DRY_RUN` (`true` until you are happy), `MAX_APPLICATIONS_PER_DAY` (`15`).

## 3a. Your resume and personal details: do it in Telegram (no base64, no .env editing)
The bot stores everything in Supabase (private Storage bucket `resumes` + the `profile` table); the worker loads it at every run, so it works with the laptop off.
Send these messages **to your bot** (from the chat that is `TELEGRAM_ALLOWED_CHAT_ID`):
1. **Attach your resume PDF** (≤ 5 MB) → `✅ Resume saved (NN KB). Send /setup to update your details.`
   Optional variants: attach another PDF with the caption `ai` or `fullstack` (used for AI-flavoured / pure full-stack roles).
2. `/setup` → answer one question at a time: phone (`+919876543210`, a 10-digit Indian number is accepted too) → LinkedIn URL → GitHub URL (prefilled from env if set;
   `keep` accepts the shown value) → portfolio URL → city. `skip` is allowed for LinkedIn / GitHub / portfolio. Then `yes` to save, `no` to redo, `cancel` any time.
3. `/profile` shows what is saved (and which values still come from env); `/myresume` sends the stored PDF back (`/myresume ai` for a variant).

*Timing:* every message is processed by the next worker run. **With the Cloudflare relay (section 5) that is ~1 minute**; with cron only it is the next scheduled run (answers sent in a row are
still applied in order, but you won't see the next question until then). Nothing is ever lost: messages are queued in the `telegram_inbox` table.

If no resume is stored anywhere (Storage → `RESUME_PDF_B64` → local file) the worker tells you **"⚠️ No resume configured. Send me your PDF."**, still does discovery, matching and drafting, but sends/applies nothing.
Missing phone/LinkedIn values simply make forms that need them wait for you (`WAITING_USER`).

## 3b. Push to GitHub and set the repository Variables (manual - `gh` CLI is not installed here)
```powershell
git remote -v                       # must point at https://github.com/VishalSharma7987/Auto-Job-apply.git
git remote set-url origin https://github.com/VishalSharma7987/Auto-Job-apply.git   # if it does not
git push -u origin main
```
Variables (Settings -> Secrets and variables -> Actions -> **Variables** tab -> New repository variable):
`DRY_RUN` = `true`, `MAX_APPLICATIONS_PER_DAY` = `15`. (`CRON_SCHEDULE` is not usable - GitHub cannot read variables in `on.schedule`; edit the two cron lines in
`.github/workflows/agent.yml`.) With the GitHub CLI instead:
```powershell
gh variable set DRY_RUN --body true
gh variable set MAX_APPLICATIONS_PER_DAY --body 15
gh run list --workflow ci.yml          # then: gh run watch <id>   - must be green
```
**Check CI:** repo -> Actions -> *ci* -> the latest run must be green (ruff + pytest). **Private repo recommended** (screenshots/artifacts contain your personal data; 2,000 free
minutes/month are enough).

## 4. First remote run
Actions → **agent** → *Run workflow* → mode `full`. You get the daily report on Telegram. Check `/status`, `/history`.
Review the drafted emails (DB table `applications`: `email_subject`, `email_body`) and the dry-run screenshots (failed/waiting runs upload
`artifacts/`). Set variable `DRY_RUN=false` only when satisfied.

## 5. Optional but recommended: instant replies (Cloudflare relay)
Follow `cloudflare/README.md` (5 steps). It also needs `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` in the Worker so uploads and onboarding answers are queued safely. Without it, commands/uploads are handled at the next cron (09:00 / 18:00 IST) or when you press *Run workflow*.

## 6. Daily use
| Command | Effect |
|---|---|
| `/jobs` | scan + qualify + draft now |
| `/apply` | act on READY jobs now |
| `/status` `/report` `/history` `/settings` | read-only views |
| `/pause` `/resume` `/killswitch` | stop/continue; killswitch = hard stop until `/resume` |
| `/approve <task_id>` `/skip <job_id>` | answer a `WAITING_USER` notification (ids may be the short 8-char prefix) |
| `/retry` | re-queue failed tasks (never re-sends an already-sent email) |
| *(send a PDF)* `/setup` `/profile` `/myresume` `/cancel` | onboarding - see 3a (`/resume` still means "resume automation", the PDF command is `/myresume`) |

## Troubleshooting
- *"profile.yaml has no skills"* → step 0.
- *"⚠️ Free-tier limit reached"* → wait for the next run, switch `LLM_MODEL`, or point `LLM_BASE_URL` at Groq/Ollama.
- Nothing from the bot → is `TELEGRAM_ALLOWED_CHAT_ID` your chat id? Did you press *Start*? If a webhook is set, only the relay path delivers commands.
- Scheduled runs stopped → GitHub disables schedules after 60 days of repo inactivity; push a commit.
- Supabase paused → open the project dashboard and restore it.

## Manual test order (do these in order; stop at the first problem)
1. **Local, fake** - `.\scripts\run_local.ps1 -Setup -Fake` -> prints "Daily Job Report", writes drafts to `data/jobagent.sqlite`, sends nothing. Then `pytest -q`.
2. **Telegram onboarding** - with your real `.env` (Supabase + Telegram keys): send the bot your resume PDF and `/setup` answers, then run
   `python -m jobagent run --mode telegram` (processes the queued messages and replies). Check `/profile` and `/myresume`.
   Optional DB/Storage check: `pytest tests/test_supabase_integration.py -v` (env loaded).
3. **Local, real data, dry run** - `DRY_RUN=true`, `FAKE_MODE=false`, `DB_BACKEND=supabase`, then `.\scripts\run_local.ps1`. The worker pulls the resume + details from Supabase. Read the drafts and reasons.
4. **GitHub Actions, dry run** - add the 9 secrets + Variables `DRY_RUN=true`, run *agent -> Run workflow -> full*. Confirm the Telegram report and rows in Supabase.
5. **Telegram command** - send `/status`, `/jobs`, `/pause`, `/resume`, `/settings` (cron path: answered at the next run; with the relay: ~1 minute).
6. **Go live** - set `DRY_RUN=false`, start with `MAX_APPLICATIONS_PER_DAY=3` for a day or two, watch Telegram, then raise to 10-15. `/killswitch` stops everything instantly.
