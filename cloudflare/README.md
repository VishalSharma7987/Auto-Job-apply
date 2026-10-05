# Cloudflare Worker: Telegram → GitHub relay (optional)

Makes bot commands run within ~1 minute instead of waiting for the next cron. Without it the agent still
works: commands are picked up from Telegram `getUpdates` at the start of every scheduled run.

> Telegram's `getUpdates` is **disabled while a webhook is set**. That is why the relay forwards the full
> Telegram update inside `client_payload.update`; the Python worker processes it from `GITHUB_EVENT_PATH`.
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
4. **Point Telegram at the Worker** (replace the placeholders; PowerShell or bash):
   ```
   curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" -d "url=https://<your-worker>.workers.dev" -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>" -d "allowed_updates=[\"message\"]"
   ```
5. **Test**: send `/status` to the bot. GitHub → Actions should show a `repository_dispatch` run within seconds,
   and the bot answers. (Workflows triggered by `repository_dispatch` only run from the **default branch**.)

Free tier: 100,000 Worker requests/day – far more than a personal bot needs.
