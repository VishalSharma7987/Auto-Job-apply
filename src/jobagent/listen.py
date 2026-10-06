"""`python -m jobagent telegram --listen` - chat mode for first-time setup (laptop on).

Long-polls getUpdates (timeout 30 s) and handles every message immediately: resume PDF upload, /setup, /set, /profile,
/myresume and all other commands. Ends on Ctrl+C, when you send /done, or after --max-seconds.

Webhook handling: getUpdates cannot work while a webhook is set. If one is set (e.g. the Cloudflare relay) its URL is
remembered (in telegram_state, never the secret), it is deleted (pending updates are kept), and it is put back on exit -
also after Ctrl+C or an error. Telegram never reveals a webhook's secret token, so restoring it needs
TELEGRAM_WEBHOOK_SECRET (the same value as the Worker's) in .env; otherwise listen mode refuses to remove the webhook
unless --force is given. `telegram --restore-webhook` repairs a session that was killed hard.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from jobagent.config import Settings
from jobagent.db.base import Repository
from jobagent.telegram.client import TelegramClient, TelegramError
from jobagent.telegram.commands import STOP, process_updates

SAVED_KEY = "saved_webhook"
PHONE_LIKE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


class ListenError(Exception):
    pass


def redact(text: str) -> str:
    """Console-only masking of phone-like digit runs (the Telegram chat itself is private)."""
    return PHONE_LIKE.sub(lambda m: m.group(0)[:3] + "*" * max(len(m.group(0)) - 5, 3) + m.group(0)[-2:], text or "")


def describe(update: dict) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    doc = msg.get("document")
    if doc:
        cap = f" caption={msg['caption']!r}" if msg.get("caption") else ""
        return f"[document {doc.get('file_name', '?')} {max(1, int(doc.get('file_size') or 0) // 1024)} KB{cap}]"
    return redact(msg.get("text") or "[unsupported message]")


def stop_webhook(settings: Settings, repo: Repository, tg: TelegramClient, echo: Callable[[str], None], force: bool = False) -> None:
    info = tg.get_webhook_info()
    url = info.get("url") or ""
    if url:
        if not settings.telegram_webhook_secret and not force:
            raise ListenError(
                f"A webhook is set ({urlparse(url).netloc}). Telegram never reveals its secret token, so I could not put the "
                "relay back afterwards. Add TELEGRAM_WEBHOOK_SECRET=<same value as the Cloudflare Worker's "
                "TELEGRAM_WEBHOOK_SECRET> to .env and run again - or pass --force to continue anyway (you would then have "
                "to run setWebhook yourself).")
        repo.set_state(SAVED_KEY, json.dumps({"url": url, "allowed_updates": info.get("allowed_updates") or ["message"]}))
        tg.delete_webhook(drop_pending_updates=False)
        echo(f"webhook {urlparse(url).netloc} removed for the session - it will be restored on exit")
    elif repo.get_state(SAVED_KEY):
        echo("note: a webhook saved by an earlier session is still pending restore - it will be restored on exit")


def restore_webhook(settings: Settings, repo: Repository, tg: TelegramClient, echo: Callable[[str], None]) -> bool:
    raw = repo.get_state(SAVED_KEY)
    if not raw:
        return False
    data = json.loads(raw)
    try:
        tg.set_webhook(data["url"], settings.telegram_webhook_secret, data.get("allowed_updates"))
    except Exception as e:  # noqa: BLE001
        echo(f"!! could not restore the webhook ({type(e).__name__}); run `python -m jobagent telegram --restore-webhook`")
        return False
    repo.set_state(SAVED_KEY, "")
    suffix = "" if settings.telegram_webhook_secret else " (WITHOUT a secret token - set TELEGRAM_WEBHOOK_SECRET and run --restore-webhook)"
    echo(f"webhook {urlparse(data['url']).netloc} restored{suffix}")
    return True


def listen(settings: Settings, repo: Repository, tg: TelegramClient, store, *, max_seconds: float | None = None,
           poll_timeout: int = 30, run_modes: Callable[[list[str]], object] | None = None,
           echo: Callable[[str], None] = print, now: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep, force: bool = False) -> str:
    """Returns why it stopped: done | timeout | interrupt | conflict."""
    if not tg.enabled:
        raise ListenError("TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID must be set")
    stop_webhook(settings, repo, tg, echo, force)
    repo.set_state("listen_active", "1")
    tg.echo = lambda direction, text: echo("  -> " + redact(text).replace("\n", "\n     "))
    reason = "timeout"
    started = now()
    try:
        echo("Listening on Telegram - send your resume PDF and /setup. Send /done or press Ctrl+C to stop.")
        pending = process_updates([], repo, tg, settings, store)  # anything already queued (e.g. by the relay)
        if STOP in pending:
            return "done"
        while True:
            remaining = None if max_seconds is None else max_seconds - (now() - started)
            if remaining is not None and remaining <= 0:
                reason = "timeout"
                break
            wait = poll_timeout if remaining is None else max(1, min(poll_timeout, int(remaining)))
            offset = int(repo.get_state("last_update_id", "0") or 0) + 1
            try:
                batch = tg.poll(offset, wait)
            except TelegramError as e:
                if "Conflict" in str(e):
                    echo("!! another getUpdates/webhook is active for this bot - stopping")
                    reason = "conflict"
                    break
                echo(f"!! telegram error: {e} - retrying")
                sleep(3)
                continue
            except httpx.HTTPError as e:
                echo(f"!! network problem ({type(e).__name__}) - retrying")
                sleep(3)
                continue
            if not batch:
                continue
            stamp = time.strftime("%H:%M:%S")
            for u in sorted(batch, key=lambda x: x.get("update_id", 0)):
                echo(f"[{stamp}] <- {describe(u)}")
            modes = process_updates(batch, repo, tg, settings, store)
            if STOP in modes:
                reason = "done"
                break
            todo = [m for m in modes if m != STOP]
            if todo and run_modes:
                echo(f"running pipeline mode(s): {', '.join(todo)} ...")
                run_modes(todo)
    except KeyboardInterrupt:
        reason = "interrupt"
        echo("interrupted")
    finally:
        repo.set_state("listen_active", "0")
        tg.echo = None
        restore_webhook(settings, repo, tg, echo)
    echo(f"Listen mode ended ({reason}).")
    return reason
