from __future__ import annotations

import json

import pytest
from conftest import CHAT, FakeTelegram, make_job

from jobagent.pipeline import state as S
from jobagent.telegram.client import TelegramClient, split_message
from jobagent.telegram.commands import (
    COMMANDS,
    event_payload_updates,
    handle_command,
    is_killed,
    is_paused,
    parse_command,
    process_updates,
)


def upd(uid: int, text: str, chat: str = CHAT) -> dict:
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


def test_all_required_commands_exist():
    assert set(COMMANDS) == {"start", "help", "jobs", "apply", "status", "report", "pause", "resume", "retry",
                             "approve", "skip", "history", "settings", "killswitch",
                             "setup", "profile", "myresume", "cancel", "set", "done"}


def test_parse_command():
    assert parse_command("/approve abc123") == ("approve", ["abc123"])
    assert parse_command("/jobs@MyBot") == ("jobs", [])
    assert parse_command("hello") == (None, [])
    assert parse_command("/nope") == (None, [])


@pytest.mark.parametrize("cmd", [c for c in COMMANDS])
def test_every_command_replies(cmd, repo, settings):
    res = handle_command(f"/{cmd}", repo, settings)
    assert res.reply and isinstance(res.reply, str)


def test_pause_resume_killswitch(repo, settings):
    handle_command("/pause", repo, settings)
    assert is_paused(repo) and not is_killed(repo)
    assert "paused" in handle_command("/jobs", repo, settings).reply.lower()
    handle_command("/resume", repo, settings)
    assert not is_paused(repo)
    handle_command("/killswitch", repo, settings)
    assert is_killed(repo) and is_paused(repo)
    handle_command("/resume", repo, settings)
    assert not is_killed(repo) and not is_paused(repo)


def test_jobs_and_apply_request_runs(repo, settings):
    assert handle_command("/jobs", repo, settings).run_mode == "jobs"
    assert handle_command("/apply", repo, settings).run_mode == "apply"


def test_skip_by_prefix_and_guard(repo, settings):
    row, _ = repo.upsert_job(make_job())
    res = handle_command(f"/skip {row['id'][:8]}", repo, settings)
    assert "Skipped" in res.reply
    j = repo.get_job(row["id"])
    assert j["status"] == S.REJECTED and j["skip_reason"] == "user_skip"
    assert "No unique" in handle_command("/skip zzzz", repo, settings).reply
    assert "Usage" in handle_command("/skip", repo, settings).reply


def test_approve_requeues_waiting_task_and_job(repo, settings):
    row, _ = repo.upsert_job(make_job())
    repo.update_job(row["id"], status=S.WAITING_USER)
    t = repo.create_task(row["id"], "browser", {"questions": [{"label": "Salary", "proposed": "10 LPA"}]}, "waiting_user")
    res = handle_command(f"/approve {t['id'][:8]}", repo, settings)
    assert "Approved" in res.reply and res.run_mode == "apply"
    assert repo.get_task(t["id"])["status"] == "queued" and repo.get_task(t["id"])["payload"]["approved"] is True
    assert repo.get_job(row["id"])["status"] == S.READY
    assert "No unique" in handle_command("/approve nothere", repo, settings).reply


def test_retry_requeues_failed_but_never_resends(repo, settings):
    row, _ = repo.upsert_job(make_job())
    repo.update_job(row["id"], status=S.FAILED)
    t = repo.create_task(row["id"], "email", None, "failed")
    res = handle_command("/retry", repo, settings)
    assert "Re-queued 1" in res.reply
    assert repo.get_task(t["id"])["status"] == "queued" and repo.get_job(row["id"])["status"] == S.READY


def test_history_and_settings_hide_secrets(repo, settings):
    row, _ = repo.upsert_job(make_job())
    app, _ = repo.claim_application(row["id"], "email", "Acme AI", "AI Developer")
    assert "Acme AI" in handle_command("/history", repo, settings).reply
    out = handle_command("/settings", repo, settings).reply
    for secret in (settings.telegram_bot_token, settings.gmail_app_password, "TESTTOKEN"):
        assert secret not in out
    assert "DRY_RUN: True" in out


# ---------------------------------------------------------------- chat allow-list + update processing
def test_only_allowed_chat_is_served(repo, settings, tg):
    modes = process_updates([upd(1, "/jobs", chat="999"), upd(2, "/pause")], repo, tg, settings)
    assert modes == [] and len(tg.sent) == 1 and "Paused" in tg.sent[0]


def test_update_ids_are_deduplicated_but_late_arrivals_are_not_lost(repo, settings, tg):
    process_updates([upd(5, "/pause")], repo, tg, settings)
    process_updates([upd(5, "/pause"), upd(4, "/help")], repo, tg, settings)
    # update 5 is not handled twice; update 4 arrived late (out of order) and must still be handled
    assert len([m for m in tg.sent if "Paused" in m]) == 1 and any("commands" in m for m in tg.sent)
    assert repo.get_state("last_update_id") == "5" and repo.inbox_pending() == []


def test_no_allowed_chat_configured_serves_nobody(repo, settings):
    t = FakeTelegram(chat_id="")
    t.chat_id = None
    assert process_updates([upd(1, "/pause")], repo, t, settings) == [] and t.sent == []


def test_relay_payload_extraction(tmp_path):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"action": "pause", "client_payload": {"update": upd(9, "/pause")}}))
    assert event_payload_updates(str(p))[0]["update_id"] == 9
    assert event_payload_updates(None) == [] and event_payload_updates(str(tmp_path / "no.json")) == []


def test_message_splitting_over_4000():
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(200))
    parts = split_message(text)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts) and "\n".join(parts) == text
    assert split_message("x" * 9000)[0] == "x" * 4000 and sum(len(p) for p in split_message("x" * 9000)) == 9000


def test_client_posts_to_telegram_api_in_parts():
    import httpx
    import respx

    with respx.mock:
        route = respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {}}))
        c = TelegramClient("123:tok", "42")
        assert c.send_message("a\n" * 3000) == 2 and route.call_count == 2
        assert json.loads(route.calls[0].request.content)["chat_id"] == "42"


def test_get_updates_swallows_webhook_conflict():
    import httpx
    import respx

    with respx.mock:
        respx.post(url__regex=r".*/getUpdates").mock(return_value=httpx.Response(
            409, json={"ok": False, "description": "Conflict: can't use getUpdates method while webhook is active"}))
        assert TelegramClient("123:tok", "42").get_updates(1) == []
