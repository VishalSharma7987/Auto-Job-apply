# Cloudflare Worker: Telegram → GitHub relay (optional)

Makes bot commands run within ~1 minute instead of waiting for the next cron. Without it the agent still
works: commands are picked up from Telegram `getUpdates` at the start of every scheduled run.

> Telegram's `getUpdates` is **disabled while a webhook is set**. The relay therefore stores each update in the Supabase
> `telegram_inbox` table (and also forwards it in `client_payload.update`); the Python worker drains the inbox in order.
> It forwards commands, **PDF uploads** and plain-text replies (for `/setup`) from your chat only.
> If you delete the webhook (`/deleteWebhook`) the cron path works again.

## 5-step deploy (dashboard, no CLI)

1. **Create a GitHub fine-grained token**: GitHub → Settings → Developer settings → Fine-grained tokens →
   repository access: *only this repo* → permission **Contents: Read and write** (needed for `dispatches`).
2. **Create the Worker**: dash.cloudflare.com → Workers & Pages → Create → *Hello World* → Deploy →
   *Edit code* → paste all of [`worker.js`](worker.js) → Deploy.
3. **Add settings** (Worker → Settings → Variables and Secrets):
   - `TELEGRAM_WEBHOOK_SECRET` (secret) – any random string, e.g. `openssl rand -hex 24`
   - `ALLOWED_CHAT_ID` – your Telegram chat id (same as `TELEGRAM_ALLOWED_CHAT_ID`)
   - `GITHUB_REPO` – `owner/repo`
   - `GITHUB_TOKEN` (secret) – the token from step 1
   - `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (secret) – same values as the repo secrets `SUPABASE_URL` / `SUPABASE_KEY`.
     The relay writes every message into the `telegram_inbox` table first, so a resume upload or an onboarding answer can never be lost
     when GitHub merges queued runs. (Without them the relay still works but falls back to the dispatch payload only.)
4. **Point Telegram at the Worker** (replace the placeholders; PowerShell or bash):
   ```
   curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" -d "url=https://<your-worker>.workers.dev" -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" -d "allowed_updates=[\"message\"]"
   ```
5. **Test**: send `/status` to the bot. GitHub → Actions should show a `repository_dispatch` run within seconds,
   and the bot answers. (Workflows triggered by `repository_dispatch` only run from the **default branch**.)

Free tier: 100,000 Worker requests/day – far more than a personal bot needs.
