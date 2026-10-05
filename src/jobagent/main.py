"""CLI: python -m jobagent run --mode jobs|apply|status|report|full [--job-id ID]

Order of work in every run: (1) pending Telegram commands (relay payload + getUpdates), (2) the requested mode.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from jobagent.config import Settings, get_settings
from jobagent.db.base import make_repo
from jobagent.logging_setup import register_secrets, setup_logging
from jobagent.pipeline.runner import build_ctx, run_pipeline
from jobagent.runtime import load_runtime_profile
from jobagent.storage import make_store
from jobagent.telegram.client import TelegramClient
from jobagent.telegram.commands import event_payload_updates, handle_command, is_paused, process_updates

log = logging.getLogger("jobagent")

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
    return p.parse_args(argv)


def run(settings: Settings, mode: str, job_id: str | None = None, tg: TelegramClient | None = None,
        repo=None, llm=None, event_path: str | None = None, store=None) -> int:
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
        if m not in modes and "full" not in modes:
            modes.append(m)
    if not modes:
        log.info("no pipeline work requested (mode=%s)", mode)
        return 0
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

    code = 0
    for m in modes:
        ctx = build_ctx(settings, repo, tg, llm, mode=m, profile=profile)
        ctx.job_id = job_id
        log.info("run start mode=%s dry_run=%s fake=%s backend=%s", m, ctx.settings.dry_run, settings.fake_mode,
                 settings.db_backend)
        try:
            run_pipeline(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline crashed")
            repo.add_event("error", "run_crashed", None, {"mode": m, "error": str(e)[:300]})
            tg.send_message(f"❌ Run crashed in mode {m}: {type(e).__name__}: {str(e)[:300]}")
            code = 1
    return code


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    args = _parse(argv)
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    settings = get_settings()
    return run(settings, args.mode, args.job_id)


if __name__ == "__main__":
    sys.exit(main())
