"""CLI: python -m jobagent run --mode jobs|apply|status|report|full [--job-id ID]
     python -m jobagent telegram [--listen [--max-seconds N] [--force]] [--restore-webhook]

Order of work in every run: (1) pending Telegram commands (relay payload + getUpdates), (2) the requested mode.
"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import sys

from jobagent.budget import RunBudget, RunInterrupted, handle_signals, hard_deadline
from jobagent.config import Settings, get_settings
from jobagent.db.base import make_repo
from jobagent.logging_setup import register_secrets, setup_logging
from jobagent.pipeline.runner import build_ctx, run_pipeline
from jobagent.runtime import load_runtime_profile
from jobagent.storage import make_store
from jobagent.telegram.client import TelegramClient
from jobagent.telegram.commands import event_payload_updates, handle_command, is_paused, process_updates

log = logging.getLogger("jobagent")
HANG_DUMP_SECONDS = 120

PIPELINE_MODES = {"jobs", "apply", "full"}
INFO_MODES = {"status", "report"}
# repository_dispatch event types: the command itself is handled from the Telegram update payload
COMMAND_MODES = {"pause", "resume", "retry", "approve", "skip", "history", "settings", "killswitch", "telegram",
                 "start", "help"}


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jobagent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the agent")
    r.add_argument("--mode", default="full")
    r.add_argument("--job-id", default=None)
    r.add_argument("--debug-hang", action="store_true",
                   help=f"dump every thread's stack to stderr every {HANG_DUMP_SECONDS}s (finds the blocking call)")
    t = sub.add_parser("telegram", help="process Telegram messages; --listen = live chat mode for first-time setup")
    t.add_argument("--listen", action="store_true", help="long-poll and answer immediately until /done or Ctrl+C")
    t.add_argument("--max-seconds", type=float, default=None, help="stop listening after this many seconds")
    t.add_argument("--force", action="store_true", help="remove a webhook even though TELEGRAM_WEBHOOK_SECRET is unknown")
    t.add_argument("--restore-webhook", action="store_true", help="put back a webhook saved by an interrupted --listen")
    return p.parse_args(argv)


def run(settings: Settings, mode: str, job_id: str | None = None, tg: TelegramClient | None = None,
        repo=None, llm=None, event_path: str | None = None, store=None, budget: RunBudget | None = None) -> int:
    budget = budget or RunBudget(settings.run_time_budget_seconds)  # one budget for the whole process
    register_secrets(settings.telegram_bot_token, settings.supabase_key, settings.llm_api_key,
                     settings.gmail_app_password, settings.candidate_phone)
    repo = repo or make_repo(settings)
    tg = tg or TelegramClient(settings.telegram_bot_token, settings.telegram_allowed_chat_id)
    store = store or make_store(settings, repo)
    mode = (mode or "full").lower()

    # 1) Telegram commands: relay-forwarded update first, then anything pending via getUpdates
    updates = event_payload_updates(event_path or os.getenv("GITHUB_EVENT_PATH"))
    if not settings.fake_mode and tg.enabled:
        offset = int(repo.get_state("last_update_id", "0") or 0) + 1
        updates += tg.get_updates(offset)
    seen: set[int] = set()
    uniq = [u for u in updates if not (u.get("update_id") in seen or seen.add(u.get("update_id")))]  # type: ignore[func-returns-value]
    requested = process_updates(uniq, repo, tg, settings, store)

    # 2) the requested mode
    if mode in INFO_MODES:
        res = handle_command(f"/{mode}", repo, settings)
        print(res.reply)
        tg.send_message(res.reply)
        return 0
    modes: list[str] = []
    if mode in PIPELINE_MODES:
        modes.append(mode)
    elif mode not in COMMAND_MODES:
        log.error("unknown mode %r", mode)
        return 2
    for m in requested:
        if m in PIPELINE_MODES and m not in modes and "full" not in modes:
            modes.append(m)
    if not modes:
        log.info("no pipeline work requested (mode=%s)", mode)
        return 0
    return execute_modes(settings, repo, tg, store, modes, llm=llm, job_id=job_id, budget=budget)


def execute_modes(settings: Settings, repo, tg: TelegramClient, store, modes: list[str], llm=None,
                  job_id: str | None = None, budget: RunBudget | None = None) -> int:
    """Run pipeline modes (jobs/apply/full). Shared by `run` and by `telegram --listen`."""
    register_secrets(settings.telegram_bot_token, settings.supabase_key, settings.llm_api_key,
                     settings.gmail_app_password, settings.candidate_phone)
    if is_paused(repo):
        msg = "⏸ Agent paused/killswitch active - skipping run. Send /resume."
        log.info(msg)
        tg.send_message(msg)
        return 0

    rt = load_runtime_profile(settings, repo, store)  # personal details: DB -> env; resume: Storage -> env -> local
    profile = rt.profile
    register_secrets(profile.phone)
    if not profile.is_complete:
        msg = "❌ profile/profile.yaml has no skills yet - fill it in (see docs/SETUP.md). Run aborted."
        log.error(msg)
        tg.send_message(msg)
        return 2
    if not settings.fake_mode and not settings.llm_api_key and "ollama" not in settings.llm_base_url:
        log.warning("no LLM API key configured: AI steps will fail")

    if rt.resume_source == "missing" and not settings.fake_mode:
        log.warning("no resume configured: email/apply steps will be skipped")
        tg.send_message("⚠️ No resume configured. Send me your PDF.")
    else:
        log.info("resume source: %s (variants: %s)", rt.resume_source, ",".join(rt.variants) or "-")

    try:  # requirements section 14: the DB keeps the verified profile (non-secret facts only)
        repo.save_profile(profile.prompt_view())
    except Exception as e:  # noqa: BLE001 - never block a run on this
        log.warning("could not store profile: %s", e)

    budget = budget or RunBudget(settings.run_time_budget_seconds)
    code = 0
    for m in modes:
        ctx = None
        try:
            ctx = build_ctx(settings, repo, tg, llm, mode=m, profile=profile, budget=budget)
            ctx.job_id = job_id
            log.info("run start mode=%s dry_run=%s fake=%s backend=%s model=%s budget=%.0fs (%.0fs used)", m,
                     ctx.settings.dry_run, settings.fake_mode, settings.db_backend, settings.llm_model,
                     budget.seconds, budget.elapsed())
            run_pipeline(ctx)  # sends the report itself, in a `finally`
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline crashed")
            try:
                repo.add_event("error", "run_crashed", None, {"mode": m, "error": f"{type(e).__name__}: {str(e)[:300]}"})
            except Exception:  # noqa: BLE001
                pass
            if ctx is None:  # crashed before the pipeline (and its report) could start
                _safe_send(tg, f"❌ Run crashed before the pipeline started ({m}): {type(e).__name__}: {str(e)[:300]}")
            code = 1
    return code


def _safe_send(tg: TelegramClient | None, text: str) -> None:
    if tg is None:
        return
    try:
        tg.send_message(text)
    except Exception as e:  # noqa: BLE001
        log.warning("could not send Telegram message: %s", e)


def telegram_command(settings: Settings, args: argparse.Namespace) -> int:
    from jobagent.listen import ListenError, listen, restore_webhook, stop_webhook  # noqa: F401

    register_secrets(settings.telegram_bot_token, settings.supabase_key, settings.llm_api_key,
                     settings.gmail_app_password, settings.candidate_phone)
    repo = make_repo(settings)
    tg = TelegramClient(settings.telegram_bot_token, settings.telegram_allowed_chat_id)
    store = make_store(settings, repo)
    if args.restore_webhook:
        ok = restore_webhook(settings, repo, tg, print)
        print("nothing to restore" if not ok and not repo.get_state("saved_webhook") else "")
        return 0 if ok or not repo.get_state("saved_webhook") else 1
    if not args.listen:
        return run(settings, "telegram", repo=repo, tg=tg, store=store)
    try:
        listen(settings, repo, tg, store, max_seconds=args.max_seconds, force=args.force,
               run_modes=lambda modes: execute_modes(settings, repo, tg, store, modes))
    except ListenError as e:
        print(f"ERROR: {e}")
        return 2
    return 0


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _enable_hang_dumps() -> None:
    """Print the stack of every thread to stderr every HANG_DUMP_SECONDS until the process exits."""
    faulthandler.enable()
    faulthandler.dump_traceback_later(HANG_DUMP_SECONDS, repeat=True, file=sys.stderr)
    log.info("hang debugging on: all thread stacks are dumped to stderr every %ds", HANG_DUMP_SECONDS)


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    args = _parse(argv)
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    settings = get_settings()
    # first line of every run, printed before any network call so a hang can never leave the log empty
    log.info("run started, mode=%s, budget=%ds", getattr(args, "mode", args.cmd), settings.run_time_budget_seconds)
    if getattr(args, "debug_hang", False) or os.getenv("DEBUG_HANG") == "1":
        _enable_hang_dumps()
    if args.cmd == "telegram":
        return telegram_command(settings, args)
    tg = TelegramClient(settings.telegram_bot_token, settings.telegram_allowed_chat_id)
    budget = RunBudget(settings.run_time_budget_seconds)
    with handle_signals(), hard_deadline(budget):
        try:
            return run(settings, args.mode, args.job_id, tg=tg, budget=budget)
        except RunInterrupted as e:
            if not e.reported:  # interrupted before the pipeline could send its partial report
                _safe_send(tg, f"🛑 Run interrupted ({e.signal_name}) before the pipeline started - nothing was lost; "
                               "the next run continues.")
            return 143 if e.signal_name == "SIGTERM" else 130
        except Exception as e:  # noqa: BLE001 - e.g. DB unreachable: still tell the user, never fail silently
            log.exception("run failed")
            _safe_send(tg, f"❌ Run failed: {type(e).__name__}: {str(e)[:300]}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
