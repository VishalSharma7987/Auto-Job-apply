"""Telegram command parser + handlers. Handlers are pure(ish) functions over the repository."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from jobagent.config import Settings
from jobagent.db.base import Repository
from jobagent.pipeline import state as S
from jobagent.telegram.client import TelegramClient
from jobagent.telegram.reports import format_report, todays_selected
from jobagent.utils.dates import today_utc

log = logging.getLogger(__name__)

COMMANDS = ["start", "help", "jobs", "apply", "status", "report", "pause", "resume", "retry", "approve",
            "skip", "history", "settings", "killswitch"]

HELP = (
    "🤖 AI Job Agent commands\n"
    "/jobs – scan for new jobs now\n"
    "/apply – process ready jobs now\n"
    "/status – pipeline status\n"
    "/report – today's report\n"
    "/pause – pause all actions\n"
    "/resume – resume (also clears killswitch)\n"
    "/retry – retry failed tasks\n"
    "/approve <task_id> – approve a waiting task\n"
    "/skip <job_id> – skip a job\n"
    "/history – recent applications\n"
    "/settings – show settings\n"
    "/killswitch – hard stop: no sends or submits until /resume"
)


@dataclass
class CommandResult:
    reply: str
    run_mode: str | None = None  # a mode the worker should run after processing commands


def parse_command(text: str) -> tuple[str | None, list[str]]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    head, *args = text.split()
    cmd = head[1:].split("@")[0].lower()
    return (cmd if cmd in COMMANDS else None), args


def is_paused(repo: Repository) -> bool:
    return repo.get_state("paused") == "1" or repo.get_state("killswitch") == "1"


def is_killed(repo: Repository) -> bool:
    return repo.get_state("killswitch") == "1"


def _find_by_prefix(rows: list[dict], prefix: str) -> list[dict]:
    return [r for r in rows if str(r["id"]).startswith(prefix)]


def handle_command(text: str, repo: Repository, settings: Settings) -> CommandResult:
    cmd, args = parse_command(text)
    if cmd is None:
        return CommandResult("Unknown command. Send /help.")
    fn = _HANDLERS[cmd]
    return fn(args, repo, settings)


def _start(args, repo, settings) -> CommandResult:
    return CommandResult("👋 AI Job Agent is online.\n\n" + HELP)


def _help(args, repo, settings) -> CommandResult:
    return CommandResult(HELP)


def _jobs(args, repo, settings) -> CommandResult:
    if is_paused(repo):
        return CommandResult("⏸ Agent is paused. Send /resume first.")
    return CommandResult("🔎 Scanning for jobs now…", run_mode="jobs")


def _apply(args, repo, settings) -> CommandResult:
    if is_paused(repo):
        return CommandResult("⏸ Agent is paused. Send /resume first.")
    return CommandResult("📨 Processing ready jobs…" + (" (DRY RUN – nothing will be sent)" if settings.dry_run else ""),
                         run_mode="apply")


def _status(args, repo, settings) -> CommandResult:
    counts: dict[str, int] = {}
    for j in repo.list_jobs(limit=5000):
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    tasks: dict[str, int] = {}
    for t in repo.list_tasks(limit=1000):
        tasks[t["status"]] = tasks.get(t["status"], 0) + 1
    stats = repo.get_stats(today_utc())
    lines = [
        "📌 Status",
        f"Mode: {'DRY RUN' if settings.dry_run else 'LIVE'} | paused={is_paused(repo)} | killswitch={is_killed(repo)}",
        "Jobs: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"),
        "Tasks: " + (", ".join(f"{k}={v}" for k, v in sorted(tasks.items())) or "none"),
        f"Today: scanned={stats['scanned']} qualified={stats['qualified']} selected={stats['selected']} "
        f"emails={stats['emails_sent']} submitted={stats['browser_submitted']}",
    ]
    waiting = repo.list_tasks("waiting_user", 10)
    for t in waiting:
        lines.append(f"⏳ task {str(t['id'])[:8]} {t['type']}: /approve {str(t['id'])[:8]}")
    return CommandResult("\n".join(lines))


def _report(args, repo, settings) -> CommandResult:
    day = today_utc()
    return CommandResult(format_report(day, repo.get_stats(day), todays_selected(repo), settings.dry_run,
                                       cap=settings.max_applications_per_day))


def _pause(args, repo, settings) -> CommandResult:
    repo.set_state("paused", "1")
    return CommandResult("⏸ Paused. Discovery and actions are suspended until /resume.")


def _resume(args, repo, settings) -> CommandResult:
    repo.set_state("paused", "0")
    repo.set_state("killswitch", "0")
    return CommandResult("▶️ Resumed (pause and killswitch cleared).")


def _killswitch(args, repo, settings) -> CommandResult:
    repo.set_state("killswitch", "1")
    repo.set_state("paused", "1")
    return CommandResult("🛑 KILLSWITCH ON. No emails or submissions will happen until /resume.")


def _retry(args, repo, settings) -> CommandResult:
    n = 0
    for t in repo.list_tasks("failed", 200):
        if t["retry_count"] >= 3:
            continue
        repo.update_task(t["id"], status="queued", retry_count=t["retry_count"] + 1)
        if t.get("job_id"):
            job = repo.get_job(t["job_id"])
            if job and job["status"] == S.FAILED:
                S.transition(repo, job["id"], S.READY, skip_reason=None)
        n += 1
    if not n:
        return CommandResult("Nothing to retry.")
    return CommandResult(f"🔁 Re-queued {n} failed task(s). Retries never re-send emails already sent.",
                         run_mode=None if is_paused(repo) else "apply")


def _approve(args, repo, settings) -> CommandResult:
    if not args:
        return CommandResult("Usage: /approve <task_id>")
    matches = _find_by_prefix(repo.list_tasks("waiting_user", 500), args[0])
    if len(matches) != 1:
        return CommandResult("No unique waiting task with that id." if not matches else "Ambiguous id prefix.")
    t = matches[0]
    payload = dict(t.get("payload") or {})
    payload["approved"] = True
    repo.update_task(t["id"], status="queued", payload=payload)
    if t.get("job_id"):
        job = repo.get_job(t["job_id"])
        if job and job["status"] == S.WAITING_USER:
            S.transition(repo, job["id"], S.READY)
    repo.add_event("info", "task_approved", t.get("job_id"), {"task_id": t["id"]})
    return CommandResult(f"✅ Approved task {str(t['id'])[:8]}. Resuming…",
                         run_mode=None if is_paused(repo) else "apply")


def _skip(args, repo, settings) -> CommandResult:
    if not args:
        return CommandResult("Usage: /skip <job_id>")
    matches = _find_by_prefix(repo.list_jobs(limit=5000), args[0])
    if len(matches) != 1:
        return CommandResult("No unique job with that id." if not matches else "Ambiguous id prefix.")
    j = matches[0]
    if j["status"] in S.TERMINAL:
        return CommandResult(f"Job already {j['status']}; cannot skip.")
    S.transition(repo, j["id"], S.REJECTED, skip_reason="user_skip")
    for t in repo.list_tasks(limit=500):
        if t.get("job_id") == j["id"] and t["status"] in ("queued", "waiting_user", "failed"):
            repo.update_task(t["id"], status="completed", last_error="skipped by user")
    return CommandResult(f"⏭ Skipped {j['company']} – {j['title']}.")


def _history(args, repo, settings) -> CommandResult:
    apps = repo.list_applications(10)
    if not apps:
        return CommandResult("No applications yet.")
    lines = ["🗂 Recent applications"]
    for a in apps:
        when = a.get("email_sent_at") or a.get("submitted_at") or "not sent"
        lines.append(f"• {a['company']} | {a['role']} | {a['route']} | {a['status']} | {str(when)[:16]}")
    return CommandResult("\n".join(lines))


def _settings(args, repo, settings) -> CommandResult:
    view = settings.public_view()
    view["paused"] = is_paused(repo)
    view["killswitch"] = is_killed(repo)
    return CommandResult("⚙️ Settings\n" + "\n".join(f"{k}: {v}" for k, v in view.items()))


_HANDLERS = {
    "start": _start, "help": _help, "jobs": _jobs, "apply": _apply, "status": _status, "report": _report,
    "pause": _pause, "resume": _resume, "retry": _retry, "approve": _approve, "skip": _skip,
    "history": _history, "settings": _settings, "killswitch": _killswitch,
}


def process_updates(updates: list[dict], repo: Repository, tg: TelegramClient, settings: Settings) -> list[str]:
    """Handle updates in order; ignore non-allowed chats; dedupe by update_id. Returns requested run modes."""
    modes: list[str] = []
    last = int(repo.get_state("last_update_id", "0") or 0)
    for u in sorted(updates, key=lambda x: x.get("update_id", 0)):
        uid = int(u.get("update_id", 0))
        if uid and uid <= last:
            continue
        if uid:
            repo.set_state("last_update_id", str(uid))
            last = uid
        msg = u.get("message") or u.get("edited_message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if not tg.is_allowed(chat_id):
            log.warning("ignoring update %s from non-allowed chat", uid)
            continue
        text = msg.get("text") or ""
        cmd, _ = parse_command(text)
        if cmd is None:
            continue
        res = handle_command(text, repo, settings)
        repo.add_event("info", f"command_{cmd}", None, {"update_id": uid})
        try:
            tg.send_message(res.reply)
        except Exception as e:  # noqa: BLE001 - a Telegram hiccup must not stop the worker
            log.warning("could not reply: %s", e)
        if res.run_mode and res.run_mode not in modes:
            modes.append(res.run_mode)
    return modes


def event_payload_updates(event_path: str | None) -> list[dict]:
    """Telegram update forwarded by the Cloudflare relay inside repository_dispatch client_payload."""
    if not event_path:
        return []
    try:
        with open(event_path, encoding="utf-8") as f:
            ev = json.load(f)
        upd = (ev.get("client_payload") or {}).get("update")
        return [upd] if isinstance(upd, dict) else []
    except (OSError, ValueError):
        return []
