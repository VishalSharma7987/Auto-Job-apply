// Telegram webhook -> GitHub repository_dispatch relay (Cloudflare Worker, free tier).
// Env (Worker settings): TELEGRAM_WEBHOOK_SECRET, ALLOWED_CHAT_ID, GITHUB_REPO ("owner/repo"), GITHUB_TOKEN (secret)
const COMMANDS = ["jobs", "apply", "status", "report", "pause", "resume", "retry", "approve", "skip",
  "history", "settings", "killswitch"];

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);
    const msg = update && update.message;
    // ignore everything except text commands from the one allowed chat (no GitHub run is triggered otherwise)
    if (!msg || String(msg.chat && msg.chat.id) !== String(env.ALLOWED_CHAT_ID)) return new Response("ok");
    const m = /^\/([A-Za-z_]+)/.exec(msg.text || "");
    if (!m) return new Response("ok");
    const cmd = m[1].toLowerCase();
    const event_type = COMMANDS.includes(cmd) ? cmd : "telegram"; // /start, /help, unknown -> "telegram"
    const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-job-agent-relay",
      },
      // the update itself rides along so the Python worker can process it even while a webhook is set
      // (Telegram's getUpdates is unavailable while a webhook is active)
      body: JSON.stringify({ event_type, client_payload: { update } }),
    });
    return new Response(res.ok ? "ok" : "dispatch failed", { status: 200 }); // always 200: no Telegram retry storms
  },
};
