// Telegram webhook -> (1) durable inbox in Supabase, (2) GitHub repository_dispatch wake-up. Cloudflare Worker, free tier.
//
// Env (Worker settings):
//   TELEGRAM_WEBHOOK_SECRET, ALLOWED_CHAT_ID, GITHUB_REPO ("owner/repo"), GITHUB_TOKEN (secret),
//   SUPABASE_URL, SUPABASE_SERVICE_KEY (secret)   <- needed so a resume upload / onboarding answer is never lost
//
// Why the inbox: GitHub merges/cancels queued runs of the same concurrency group, so a message that only rides inside
// a dispatch payload could be dropped. The worker drains telegram_inbox in update_id order on every run, so any run
// (even one triggered by a later message) handles everything pending. The update is also sent in client_payload as a
// fallback if the Supabase insert fails.
const COMMANDS = ["jobs", "apply", "status", "report", "pause", "resume", "retry", "approve", "skip",
  "history", "settings", "killswitch", "setup", "profile", "myresume"];

async function saveToInbox(env, update) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return false;
  const headers = {
    apikey: env.SUPABASE_SERVICE_KEY,
    "Content-Type": "application/json",
    Prefer: "resolution=ignore-duplicates,return=minimal",
  };
  // legacy JWT service keys also want a bearer header; new sb_secret_ keys must only use `apikey`
  if (!env.SUPABASE_SERVICE_KEY.startsWith("sb_")) headers.Authorization = `Bearer ${env.SUPABASE_SERVICE_KEY}`;
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/telegram_inbox?on_conflict=update_id`, {
    method: "POST",
    headers,
    body: JSON.stringify({ update_id: update.update_id, payload: update }),
  });
  return res.ok;
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);
    const msg = update && update.message;
    // only the one allowed chat can trigger anything (no GitHub run, no DB write otherwise)
    if (!msg || String(msg.chat && msg.chat.id) !== String(env.ALLOWED_CHAT_ID)) return new Response("ok");
    // forward commands, resume uploads (documents) and plain-text replies (onboarding answers)
    const text = msg.text || "";
    if (!msg.document && !text) return new Response("ok");

    let stored = false;
    try { stored = await saveToInbox(env, update); } catch (e) { stored = false; }

    const m = /^\/([A-Za-z_]+)/.exec(text);
    const cmd = m ? m[1].toLowerCase() : "";
    const event_type = COMMANDS.includes(cmd) ? cmd : "telegram"; // documents, replies, /start, /help, unknown
    const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-job-agent-relay",
      },
      body: JSON.stringify({ event_type, client_payload: { update, stored } }),
    });
    return new Response(res.ok ? "ok" : "dispatch failed", { status: 200 }); // always 200: no Telegram retry storms
  },
};
