# Setup guide

Time: ~30 minutes. Cost: $0. Do the steps in order; the agent stays in **DRY_RUN** until you flip it.

## 0. Review your profile
`profile/profile.yaml` is already filled from section 2 of the requirements doc (skills, project areas, target roles, locations,
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

**Secrets**
`SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `OPENROUTER_API_KEY`, `LLM_BASE_URL`
(`https://openrouter.ai/api/v1`), `LLM_MODEL` (e.g. `nvidia/nemotron-3-super-120b-a12b:free`), `GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`, `RESUME_PDF_B64`, `CANDIDATE_PHONE`, `LINKEDIN_URL`, `CANDIDATE_GITHUB_URL` *(not `GITHUB_URL`: GitHub forbids that prefix)*,
`PORTFOLIO_URL`.

**Variables:** `DRY_RUN` (`true` until you are happy), `MAX_APPLICATIONS_PER_DAY` (`15`).

**Resume secret** (PDF should be ≲ 35 KB; GitHub secrets are limited to 48 KB):
```powershell
.\scripts\encode_resume.ps1 -Path C:\path\resume.pdf     # copies base64 to the clipboard
```

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

## 5. Optional: instant commands (Cloudflare relay)
Follow `cloudflare/README.md` (5 steps). Without it commands run at the next cron (09:00 / 18:00 IST) or when you press *Run workflow*.

## 6. Daily use
| Command | Effect |
|---|---|
| `/jobs` | scan + qualify + draft now |
| `/apply` | act on READY jobs now |
| `/status` `/report` `/history` `/settings` | read-only views |
| `/pause` `/resume` `/killswitch` | stop/continue; killswitch = hard stop until `/resume` |
| `/approve <task_id>` `/skip <job_id>` | answer a `WAITING_USER` notification (ids may be the short 8-char prefix) |
| `/retry` | re-queue failed tasks (never re-sends an already-sent email) |

## Troubleshooting
- *"profile.yaml has no skills"* → step 0.
- *"⚠️ Free-tier limit reached"* → wait for the next run, switch `LLM_MODEL`, or point `LLM_BASE_URL` at Groq/Ollama.
- Nothing from the bot → is `TELEGRAM_ALLOWED_CHAT_ID` your chat id? Did you press *Start*? If a webhook is set, only the relay path delivers commands.
- Scheduled runs stopped → GitHub disables schedules after 60 days of repo inactivity; push a commit.
- Supabase paused → open the project dashboard and restore it.

## Manual test order (do these in order; stop at the first problem)
1. **Local, fake** - `.\scriptsun_local.ps1 -Setup -Fake` -> prints "Daily Job Report", writes drafts to `data/jobagent.sqlite`, sends nothing. Then `pytest -q`.
2. **Local, real data, dry run** - fill `.env` (at least `LLM_API_KEY`, optional Telegram), `DRY_RUN=true`, `DB_BACKEND=sqlite`, `FAKE_MODE=false`, put your resume at
   `profile/resume/resume.pdf`, run `.\scriptsun_local.ps1`. Read the drafted emails/reasons (`sqlite` DB or the report), check `/status`-style output, look at screenshots in `artifacts/`.
   Optional DB check: `$env:SUPABASE_URL=...; $env:SUPABASE_KEY=...; pytest tests/test_supabase_integration.py -v`.
3. **GitHub Actions, dry run** - add the Secrets (step 3), Variables `DRY_RUN=true`, run *agent -> Run workflow -> full*. Confirm the Telegram report and rows in Supabase.
4. **Telegram command** - send `/status`, `/jobs`, `/pause`, `/resume`, `/settings` (cron path: answered at the next run or when you press *Run workflow*). Optional relay: `cloudflare/README.md`
   (then commands answer within ~1 minute).
5. **Go live** - set `DRY_RUN=false`, start with `MAX_APPLICATIONS_PER_DAY=3` for a day or two, watch Telegram, then raise to 10-15. `/killswitch` stops everything instantly.
