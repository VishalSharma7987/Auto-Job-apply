# Free-tier report

Everything runs at **$0** for a personal job search. Limits below are the publicly documented free-tier numbers
at the time of writing (verify before relying on them; vendors change them).

| Component | Role | Free allowance | This project's expected use | Hits the limit when… | Paid alternative (later) |
|---|---|---|---|---|---|
| **GitHub Actions** (public repo) | Hosts the worker | Unlimited minutes on public repos; private repos: 2,000 min/month | ~2 scheduled + a few command runs/day × 3–10 min ≈ 300–900 min/month | Private repo with many `/jobs` commands | GitHub Team minutes (~$0.008/min Linux) |
| **Supabase Free** | Postgres | 500 MB DB, pauses after 1 week of **no** activity, 2 projects | tens of MB (only cheap-filter survivors are stored) | After many months, or if you raise caps heavily; project **pauses if idle 7 days** (the cron keeps it alive) | Pro $25/month |
| **Cloudflare Workers Free** | Telegram relay | 100,000 requests/day | < 100/day | Never in practice | Workers Paid $5/month |
| **OpenRouter free models** | LLM | ~20 requests/minute and ~50 requests/day per account for `:free` models (≈1,000/day after a one-time $10 top-up); models and quotas change often | ≤ 30 matches + ≤ 15 drafts per run, cached by `job_key` | A busy day: you will see `⚠️ Free-tier limit reached`; remaining jobs resume next run | Paid OpenRouter / Groq / any OpenAI-compatible API (change 3 env vars) |
| **Gmail SMTP** | Sends applications | ~500 recipients/day for a personal account | ≤ 15/day (cap) | Cap raised above ~100/day or account flagged for spam-like behaviour | Google Workspace (2,000/day) or a transactional provider |
| **Telegram Bot API** | Control + notifications | Free; ~30 messages/second, 1 msg/s per chat | a handful per run | Never | – |
| **Job APIs** (Greenhouse, Lever, Ashby, Remotive, RemoteOK, Arbeitnow, Jobicy) | Discovery | Free public JSON; Remotive asks ≤ ~2–4 calls/day; RemoteOK/Jobicy ask for attribution | ~50 board calls + a few aggregator calls per run | Aggressive polling → 429/blocks (the HTTP client throttles per host) | – |
| **Playwright / Chromium** | Browser apply | Open source; runs inside the Actions runner | 1–2 min per application form | – | – |
| **LangGraph / LangChain-core** | Orchestration | Open source (MIT); no LangSmith used | – | – | LangSmith tracing (optional, not enabled) |

## What costs money *later* (all optional, none enabled)
- A paid or higher-quota LLM if you want better matching than free models, or > ~50 LLM calls/day.
- Supabase Pro if you need no-pause + backups, or > 500 MB.
- Private repo + heavy usage > 2,000 Actions minutes/month.
- CAPTCHA-solving services, residential proxies, paid contact-enrichment APIs (e.g. Hunter/Apollo): **deliberately not used**; they would also
  violate the "no bypass / no guessing" rules.

## Things that surprise people on free tiers
1. **Supabase pauses idle projects** after 7 days without API activity – the twice-daily cron prevents this, but a paused agent (`/pause`) still pings the DB.
2. **Scheduled workflows are disabled** by GitHub after 60 days without repository activity on public repos – push any commit (or re-enable in the Actions tab).
3. **OpenRouter free models are shared and rate-limited**: a 429 is normal; the agent retries, then reports and resumes next run.
4. **Gmail**: sending from a brand-new app password to many unknown HR addresses can trigger Google's abuse detection. Keep the cap low, DRY_RUN first.
