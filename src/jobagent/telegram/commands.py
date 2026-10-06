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
            "skip", "history", "settings", "killswitch", "setup", "profile", "myresume", "cancel", "set", "done"]

STOP = "__stop__"  # returned in the requested-modes list when /done ends `telegram --listen`

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
    "/killswitch – hard stop: no sends or submits until /resume\n"
    "\n"
    "👤 Onboarding\n"
    "Send me your resume as a PDF (caption 'ai' or 'fullstack' saves a variant)\n"
    "/setup – one-message setup: I send a template, you reply with it filled in\n"
    "/set <field> <value> – change one detail, e.g. /set phone +919876543210\n"
    "/profile – show your saved details\n"
    "/myresume – send back the stored resume PDF\n"
    "/cancel – abort /setup\n"
    "/done – end `jobagent telegram --listen` (laptop chat mode)"
)


@dataclass
class CommandResult:
    reply: str
    run_mode: str | None = None  # a mode the worker should run after processing commands
    document: tuple[bytes, str, str] | None = None  # (pdf bytes, filename, caption) to send back


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


def handle_command(text: str, repo: Repository, settings: Settings, store=None) -> CommandResult:
    cmd, args = parse_command(text)
    if cmd is None:
        return CommandResult("Unknown command. Send /help.")
    fn = _HANDLERS[cmd]
    return fn(args, repo, settings, store)


def _start(args, repo, settings, store=None) -> CommandResult:
    return CommandResult("👋 AI Job Agent is online.\n\n" + HELP)


def _help(args, repo, settings, store=None) -> CommandResult:
    return CommandResult(HELP)


def _jobs(args, repo, settings, store=None) -> CommandResult:
    if is_paused(repo):
        return CommandResult("⏸ Agent is paused. Send /resume first.")
    return CommandResult("🔎 Scanning for jobs now…", run_mode="jobs")


def _apply(args, repo, settings, store=None) -> CommandResult:
    if is_paused(repo):
        return CommandResult("⏸ Agent is paused. Send /resume first.")
    return CommandResult("📨 Processing ready jobs…" + (" (DRY RUN – nothing will be sent)" if settings.dry_run else ""),
                         run_mode="apply")


def _status(args, repo, settings, store=None) -> CommandResult:
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


def _report(args, repo, settings, store=None) -> CommandResult:
    day = today_utc()
    return CommandResult(format_report(day, repo.get_stats(day), todays_selected(repo), settings.dry_run,
                                       cap=settings.max_applications_per_day))


def _pause(args, repo, settings, store=None) -> CommandResult:
    repo.set_state("paused", "1")
    return CommandResult("⏸ Paused. Discovery and actions are suspended until /resume.")


def _resume(args, repo, settings, store=None) -> CommandResult:
    repo.set_state("paused", "0")
    repo.set_state("killswitch", "0")
    return CommandResult("▶️ Resumed (pause and killswitch cleared).")


def _killswitch(args, repo, settings, store=None) -> CommandResult:
    repo.set_state("killswitch", "1")
    repo.set_state("paused", "1")
    return CommandResult("🛑 KILLSWITCH ON. No emails or submissions will happen until /resume.")


def _retry(args, repo, settings, store=None) -> CommandResult:
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


def _approve(args, repo, settings, store=None) -> CommandResult:
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


def _skip(args, repo, settings, store=None) -> CommandResult:
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


def _history(args, repo, settings, store=None) -> CommandResult:
    apps = repo.list_applications(10)
    if not apps:
        return CommandResult("No applications yet.")
    lines = ["🗂 Recent applications"]
    for a in apps:
        when = a.get("email_sent_at") or a.get("submitted_at") or "not sent"
        lines.append(f"• {a['company']} | {a['role']} | {a['route']} | {a['status']} | {str(when)[:16]}")
    return CommandResult("\n".join(lines))


def _settings(args, repo, settings, store=None) -> CommandResult:
    from jobagent.profile import load_profile

    prof = load_profile(settings)
    view = settings.public_view()
    view["TARGET_ROLES"] = ", ".join(prof.target_roles) or "-"
    view["PREFERRED_LOCATIONS"] = ", ".join(prof.preferred_locations) or "-"
    view["EXPERIENCE_TARGET"] = prof.experience_level or "-"
    view["DAILY_TARGET"] = prof.daily_target or f"{settings.max_applications_per_day}/day cap"
    view["paused"] = is_paused(repo)
    view["killswitch"] = is_killed(repo)
    return CommandResult("⚙️ Settings\n" + "\n".join(f"{k}: {v}" for k, v in view.items()))


def _setup(args, repo, settings, store=None) -> CommandResult:
    from jobagent import onboarding

    return CommandResult(onboarding.begin(repo, settings, " ".join(args)))


def _set(args, repo, settings, store=None) -> CommandResult:
    from jobagent import onboarding

    return CommandResult(onboarding.set_field(args, repo, settings))


def _done(args, repo, settings, store=None) -> CommandResult:
    if repo.get_state("listen_active") == "1":
        return CommandResult("👋 Listen mode ended. Your messages will be handled by the scheduled / relay runs again.",
                             run_mode=STOP)
    return CommandResult("Listen mode is not running (/done only ends `python -m jobagent telegram --listen`).")


def _cancel(args, repo, settings, store=None) -> CommandResult:
    from jobagent import onboarding

    return CommandResult(onboarding.cancel(repo))


def _profile_cmd(args, repo, settings, store=None) -> CommandResult:
    from jobagent import onboarding

    row = repo.get_profile_row() or {}
    env = {"phone": settings.candidate_phone, "linkedin": settings.linkedin_url, "github": settings.github_url,
           "portfolio": settings.portfolio_url}
    db = {"phone": row.get("phone"), "linkedin": row.get("linkedin_url"), "github": row.get("github_url"),
          "portfolio": row.get("portfolio_url")}

    def show(k: str) -> str:
        if db.get(k):
            return db[k]
        if env.get(k):
            return f"{env[k]} (from env)"
        return "not set"

    lines = ["👤 Your saved details", f"Phone: {show('phone')}", f"LinkedIn: {show('linkedin')}",
             f"GitHub: {show('github')}", f"Portfolio: {show('portfolio')}",
             f"Location: {row.get('location') or 'not set'}", "", "📄 Resume"]
    variants = row.get("resume_variants") or {}
    if variants:
        for name, v in sorted(variants.items()):
            lines.append(f"• {name}: {max(1, round(int(v.get('size') or 0) / 1024))} KB, saved {str(v.get('updated_at', ''))[:16]}")
    elif settings.resume_pdf_b64:
        lines.append("• default: from RESUME_PDF_B64 env (send a PDF to store it in the database)")
    else:
        lines.append("• none yet - send me your resume as a PDF")
    if onboarding.is_active(repo):
        lines += ["", "ℹ️ /setup is in progress - answer the last question or send 'cancel'."]
    return CommandResult("\n".join(lines))


def _myresume(args, repo, settings, store=None) -> CommandResult:
    if store is None:
        return CommandResult("⚠️ Resume storage is not available.")
    name = args[0].lower() if args else "default"
    if name not in ("default", "ai", "fullstack"):
        return CommandResult("Usage: /myresume [ai|fullstack]")
    data = store.download(f"{name}.pdf")
    if not data:
        return CommandResult("No resume stored yet. Send me your PDF.")
    return CommandResult(f"📄 Stored resume '{name}' ({max(1, round(len(data) / 1024))} KB)",
                         document=(data, f"resume_{name}.pdf" if name != "default" else "resume.pdf", "Stored resume"))


_HANDLERS = {
    "setup": _setup, "cancel": _cancel, "set": _set, "done": _done, "profile": _profile_cmd, "myresume": _myresume,
    "start": _start, "help": _help, "jobs": _jobs, "apply": _apply, "status": _status, "report": _report,
    "pause": _pause, "resume": _resume, "retry": _retry, "approve": _approve, "skip": _skip,
    "history": _history, "settings": _settings, "killswitch": _killswitch,
}


def ingest_updates(updates: list[dict], repo: Repository) -> int:
    """Store updates in the durable inbox (idempotent by update_id). Returns how many were new."""
    new, top = 0, 0
    for u in updates:
        uid = int(u.get("update_id", 0) or 0)
        if not uid:
            continue
        top = max(top, uid)
        if repo.inbox_add(uid, u):
            new += 1
    if top and top > int(repo.get_state("last_update_id", "0") or 0):
        repo.set_state("last_update_id", str(top))  # getUpdates offset
    return new


def _send(tg: TelegramClient, text: str) -> None:
    try:
        tg.send_message(text)
    except Exception as e:  # noqa: BLE001 - a Telegram hiccup must not stop the worker
        log.warning("could not reply: %s", e)


def _handle_one(u: dict, repo: Repository, tg: TelegramClient, settings: Settings, store) -> str | None:
    """Handle one stored update; returns a requested run mode (or None)."""
    from jobagent import onboarding
    from jobagent.telegram.uploads import handle_document

    msg = u.get("message") or u.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not tg.is_allowed(chat_id):  # uploads, /setup and everything else: allowed chat only
        log.warning("ignoring update %s from non-allowed chat", u.get("update_id"))
        return None
    if msg.get("document"):
        _send(tg, handle_document(msg, repo, tg, store))
        return None
    text = msg.get("text") or ""
    cmd, _ = parse_command(text)
    if cmd is None:
        if text and not text.startswith("/"):
            reply = onboarding.handle_text(text, repo, settings)  # None unless /setup is in progress
            _send(tg, reply or "👋 Send /help to see what I can do, or send me your resume as a PDF.")
        return None
    res = handle_command(text, repo, settings, store)
    repo.add_event("info", f"command_{cmd}", None, {"update_id": u.get("update_id")})
    _send(tg, res.reply)
    if res.document:
        data, filename, caption = res.document
        try:
            tg.send_document(data, filename, caption)
        except Exception as e:  # noqa: BLE001
            log.warning("could not send document: %s", type(e).__name__)
    return res.run_mode


def process_updates(updates: list[dict], repo: Repository, tg: TelegramClient, settings: Settings,
                    store=None) -> list[str]:
    """Ingest `updates` into the inbox, then drain everything pending in update_id order.

    Each step is stateless (onboarding state lives in the DB) and every update is marked done only after it was
    handled, so a crashed/cancelled run is simply continued by the next one. Returns requested run modes."""
    ingest_updates(updates, repo)
    modes: list[str] = []
    for row in repo.inbox_pending(500):
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        try:
            mode = _handle_one(payload, repo, tg, settings, store)
        except Exception as e:  # noqa: BLE001 - never loop forever on one bad message
            log.exception("update %s failed", row["update_id"])
            repo.add_event("error", "update_failed", None, {"update_id": row["update_id"], "error": type(e).__name__})
            _send(tg, "⚠️ I could not process that message. Please try again.")
            mode = None
        repo.inbox_mark_done(row["update_id"])
        if mode and mode not in modes:
            modes.append(mode)
    repo.inbox_prune(14)
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
