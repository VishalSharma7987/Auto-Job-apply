"""`telegram --listen`: long-poll loop, offsets, /done, timeout, Ctrl+C, webhook save/restore (all with a scripted Telegram)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import FakeTelegram
from test_onboarding import PDF, msg, pdf_doc

from jobagent.listen import ListenError, describe, listen, redact, restore_webhook
from jobagent.main import _parse
from jobagent.telegram.client import TelegramError
from jobagent.telegram.commands import STOP, handle_command, process_updates

HOOK = {"url": "https://relay.example.workers.dev/hook", "allowed_updates": ["message"]}


class ScriptedTG(FakeTelegram):
    """FakeTelegram + a scripted getUpdates long-poll and a webhook that can be inspected."""

    def __init__(self, batches=(), webhook=None):
        super().__init__()
        self.batches = list(batches)
        self.polls: list[tuple[int, int]] = []
        self.webhook = dict(webhook or {})
        self.calls: list[tuple] = []

    def poll(self, offset, timeout=30):
        self.polls.append((offset, timeout))
        if not self.batches:
            return []
        b = self.batches.pop(0)
        if isinstance(b, BaseException):
            raise b
        return b

    def get_webhook_info(self):
        return dict(self.webhook)

    def delete_webhook(self, drop_pending_updates=False):
        self.calls.append(("delete", drop_pending_updates))
        self.webhook = {}

    def set_webhook(self, url, secret_token=None, allowed_updates=None):
        self.calls.append(("set", url, secret_token, allowed_updates))
        self.webhook = {"url": url}


def run_listen(repo, settings, tg, store, **kw):
    out: list[str] = []
    kw.setdefault("sleep", lambda s: None)
    reason = listen(settings, repo, tg, store, echo=out.append, **kw)
    return reason, out


@pytest.fixture
def s(settings):
    return settings.model_copy(update={"telegram_webhook_secret": "s3cret"})


# ================================================================ the loop
def test_loop_handles_each_batch_immediately_and_advances_the_offset(repo, s, store):
    tg = ScriptedTG([[msg(100, "/status")], [msg(101, "/help"), msg(103, "/pause")], [msg(104, "/done")]])
    reason, out = run_listen(repo, s, tg, store)
    assert reason == "done"
    assert [o for o, _ in tg.polls] == [1, 101, 104]  # last_update_id + 1 each time: nothing is replayed or skipped
    assert any("Status" in m or "📌" in m for m in tg.sent) and any("commands" in m for m in tg.sent)
    assert repo.get_state("paused") == "1" and any("Listen mode ended" in m for m in tg.sent)
    assert repo.inbox_pending() == [] and repo.get_state("last_update_id") == "104"
    assert repo.get_state("listen_active") == "0"
    assert any("<- /status" in line for line in out) and any("Listen mode ended (done)" in line for line in out)


def test_offset_resumes_from_the_stored_last_update_id(repo, s, store):
    repo.set_state("last_update_id", "500")
    tg = ScriptedTG([[msg(501, "/done")]])
    run_listen(repo, s, tg, store)
    assert tg.polls[0][0] == 501


def test_messages_already_queued_in_the_inbox_are_processed_before_polling(repo, s, store):
    repo.inbox_add(7, msg(7, "/help"))  # e.g. stored by the relay earlier
    tg = ScriptedTG([[msg(8, "/done")]])
    run_listen(repo, s, tg, store)
    assert any("commands" in m for m in tg.sent) and repo.inbox_pending() == []


def test_timeout_stops_the_loop_and_shortens_the_last_poll(repo, s, store):
    clock = {"t": 0.0}

    def now():
        clock["t"] += 7.0
        return clock["t"]

    tg = ScriptedTG()
    reason, _ = run_listen(repo, s, tg, store, max_seconds=20, now=now, poll_timeout=30)
    assert reason == "timeout" and tg.polls and all(t <= 30 for _, t in tg.polls)
    assert tg.polls[-1][1] < 30  # never waits past the deadline


def test_done_outside_listen_mode_does_nothing(repo, settings):
    res = handle_command("/done", repo, settings)
    assert "not running" in res.reply and res.run_mode is None


def test_done_inside_listen_mode_requests_stop(repo, settings):
    repo.set_state("listen_active", "1")
    assert handle_command("/done", repo, settings).run_mode == STOP


def test_stop_never_leaks_into_pipeline_modes_for_normal_runs(repo, settings, tg):
    repo.set_state("listen_active", "1")
    modes = process_updates([msg(1, "/done")], repo, tg, settings, None)
    from jobagent.main import PIPELINE_MODES

    assert STOP in modes and not [m for m in modes if m in PIPELINE_MODES]


def test_ctrl_c_ends_cleanly_and_restores_the_webhook(repo, s, store):
    tg = ScriptedTG([KeyboardInterrupt()], webhook=HOOK)
    reason, out = run_listen(repo, s, tg, store)
    assert reason == "interrupt" and repo.get_state("listen_active") == "0"
    assert tg.calls[0] == ("delete", False) and tg.calls[-1][0] == "set" and tg.webhook["url"] == HOOK["url"]
    assert any("interrupted" in line for line in out)


def test_conflict_with_another_poller_stops(repo, s, store):
    tg = ScriptedTG([TelegramError("getUpdates: Conflict: terminated by other getUpdates request")])
    reason, out = run_listen(repo, s, tg, store)
    assert reason == "conflict" and any("stopping" in line for line in out)


def test_transient_errors_are_retried(repo, s, store):
    sleeps: list[float] = []
    tg = ScriptedTG([httpx.ConnectError("boom"), TelegramError("getUpdates: Too Many Requests"), [msg(1, "/done")]])
    reason, out = run_listen(repo, s, tg, store, sleep=sleeps.append)
    assert reason == "done" and sleeps == [3, 3] and sum("retrying" in line for line in out) == 2


def test_pipeline_commands_run_in_process(repo, s, store):
    tg = ScriptedTG([[msg(1, "/jobs")], [msg(2, "/apply")], [msg(3, "/done")]])
    ran: list[list[str]] = []
    run_listen(repo, s, tg, store, run_modes=ran.append)
    assert ran == [["jobs"], ["apply"]]


def test_strangers_are_ignored_in_listen_mode(repo, s, store):
    tg = ScriptedTG([[msg(1, "/pause", chat="999")], [msg(2, "/done")]])
    run_listen(repo, s, tg, store)
    assert repo.get_state("paused") != "1"


def test_unexpected_exception_still_cleans_up(repo, s, store):
    tg = ScriptedTG([RuntimeError("bug")], webhook=HOOK)
    with pytest.raises(RuntimeError):
        run_listen(repo, s, tg, store)
    assert repo.get_state("listen_active") == "0" and tg.webhook["url"] == HOOK["url"]  # webhook came back anyway


def test_needs_telegram_credentials(repo, s, store):
    bad = ScriptedTG()
    bad.token = None
    with pytest.raises(ListenError):
        run_listen(repo, s, bad, store)


# ================================================================ the real flow, end to end, in listen mode
def test_pdf_and_setup_are_processed_immediately(repo, s, store):
    tg = ScriptedTG([[msg(1, document=pdf_doc(), caption="/resume")], [msg(2, "/setup")],
                     [msg(3, "phone: +919876543210\nlinkedin: skip\ngithub: github.com/vishal\nlocation: Pune")],
                     [msg(4, "/profile")], [msg(5, "/myresume")], [msg(6, "/done")]])
    tg.files["f1"] = PDF
    reason, out = run_listen(repo, s, tg, store)
    assert reason == "done" and store.download("default.pdf") == PDF
    row = repo.get_profile_row()
    assert row["phone"] == "+919876543210" and row["location"] == "Pune" and row["onboarding_state"] is None
    assert any(m.startswith("✅ Resume saved") for m in tg.sent) and any("Details saved" in m for m in tg.sent)
    assert tg.documents == [("resume.pdf", len(PDF))]
    assert any("<- [document CV.pdf" in line and "caption='/resume'" in line for line in out)


def test_console_masks_phone_numbers_but_the_bot_still_gets_the_real_one(repo, s, store):
    tg = ScriptedTG([[msg(1, "/set phone +919876543210")], [msg(2, "/done")]])
    _, out = run_listen(repo, s, tg, store)
    text = "\n".join(out)
    assert "+919876543210" not in text and "+91" in text  # masked on screen...
    assert repo.get_profile_row()["phone"] == "+919876543210"  # ...saved correctly


def test_redact_and_describe():
    assert "9876543210" not in redact("call me on +91 98765 43210 now") and redact("plain text") == "plain text"
    assert describe(msg(1, "/status")) == "/status"
    assert describe(msg(1, document=pdf_doc(size=3000), caption="ai")) == "[document CV.pdf 2 KB caption='ai']"
    assert describe({"update_id": 1, "message": {"chat": {"id": 1}, "photo": []}}) == "[unsupported message]"


def test_tg_echo_is_wired_during_the_session_and_removed_after(repo, s, store):
    tg = ScriptedTG([[msg(1, "/done")]])
    run_listen(repo, s, tg, store)
    assert tg.echo is None


# ================================================================ webhook save / restore
def test_webhook_is_deleted_for_the_session_and_restored_with_secret_after(repo, s, store):
    tg = ScriptedTG([[msg(1, "/done")]], webhook=HOOK)
    reason, out = run_listen(repo, s, tg, store)
    assert reason == "done"
    assert tg.calls == [("delete", False), ("set", HOOK["url"], "s3cret", ["message"])]  # pending updates are NOT dropped
    assert repo.get_state("saved_webhook") == ""  # cleared after a successful restore
    assert any("removed for the session" in line for line in out) and any("restored" in line for line in out)
    assert "s3cret" not in "\n".join(out)


def test_webhook_secret_is_never_stored_in_the_database(repo, s, store):
    class Peek(ScriptedTG):
        def poll(self, offset, timeout=30):
            self.peeked = repo.get_state("saved_webhook")
            return [msg(1, "/done")]

    tg = Peek(webhook=HOOK)
    run_listen(repo, s, tg, store)
    saved = json.loads(tg.peeked)
    assert saved["url"] == HOOK["url"] and "s3cret" not in tg.peeked and "secret" not in saved


def test_refuses_to_remove_a_webhook_it_cannot_restore(repo, settings, store):
    tg = ScriptedTG([[msg(1, "/done")]], webhook=HOOK)  # settings has no TELEGRAM_WEBHOOK_SECRET
    with pytest.raises(ListenError, match="TELEGRAM_WEBHOOK_SECRET"):
        run_listen(repo, settings, tg, store)
    assert tg.calls == [] and tg.webhook["url"] == HOOK["url"] and tg.polls == []  # untouched


def test_force_goes_ahead_without_a_secret_and_warns(repo, settings, store):
    tg = ScriptedTG([[msg(1, "/done")]], webhook=HOOK)
    _, out = run_listen(repo, settings, tg, store, force=True)
    assert tg.calls[-1] == ("set", HOOK["url"], None, ["message"]) and any("WITHOUT a secret token" in line for line in out)


def test_no_webhook_means_nothing_to_restore(repo, s, store):
    tg = ScriptedTG([[msg(1, "/done")]])
    run_listen(repo, s, tg, store)
    assert tg.calls == []


def test_a_failed_restore_keeps_the_saved_url_for_restore_webhook(repo, s, store):
    class Flaky(ScriptedTG):
        def set_webhook(self, *a, **k):
            raise TelegramError("setWebhook: bad gateway")

    tg = Flaky([[msg(1, "/done")]], webhook=HOOK)
    _, out = run_listen(repo, s, tg, store)
    assert json.loads(repo.get_state("saved_webhook"))["url"] == HOOK["url"] and any("--restore-webhook" in line for line in out)
    # later: the repair command puts it back
    fixed = ScriptedTG()
    assert restore_webhook(s, repo, fixed, lambda *_: None) is True
    assert fixed.calls == [("set", HOOK["url"], "s3cret", ["message"])] and repo.get_state("saved_webhook") == ""
    assert restore_webhook(s, repo, fixed, lambda *_: None) is False  # nothing left


def test_pending_restore_from_a_killed_session_happens_on_the_next_exit(repo, s, store):
    repo.set_state("saved_webhook", json.dumps(HOOK))  # a previous session died hard
    tg = ScriptedTG([[msg(1, "/done")]])  # no webhook currently set
    _, out = run_listen(repo, s, tg, store)
    assert any("earlier session" in line for line in out) and tg.calls == [("set", HOOK["url"], "s3cret", ["message"])]


# ================================================================ CLI + client
def test_cli_flags():
    a = _parse(["telegram", "--listen", "--max-seconds", "180", "--force"])
    assert (a.cmd, a.listen, a.max_seconds, a.force, a.restore_webhook) == ("telegram", True, 180.0, True, False)
    assert _parse(["telegram", "--restore-webhook"]).restore_webhook is True
    assert _parse(["run", "--mode", "jobs"]).mode == "jobs"


def test_client_poll_uses_offset_long_poll_and_raises_on_webhook_conflict():
    import respx

    from jobagent.telegram.client import TelegramClient

    with respx.mock:
        route = respx.post(url__regex=r".*/getUpdates").mock(side_effect=[
            httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]}),
            httpx.Response(409, json={"ok": False, "description": "Conflict: can't use getUpdates while webhook is active"})])
        c = TelegramClient("123:tok", "42")
        assert c.poll(5, 30) == [{"update_id": 5}]
        body = json.loads(route.calls[0].request.content)
        assert body["offset"] == 5 and body["timeout"] == 30 and body["allowed_updates"] == ["message"]
        with pytest.raises(TelegramError, match="Conflict"):
            c.poll(6, 30)


def test_client_webhook_calls():
    import respx

    from jobagent.telegram.client import TelegramClient

    with respx.mock:
        info = respx.post(url__regex=r".*/getWebhookInfo").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"url": "https://x.example/h"}}))
        dele = respx.post(url__regex=r".*/deleteWebhook").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
        setw = respx.post(url__regex=r".*/setWebhook").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
        c = TelegramClient("123:tok", "42")
        assert c.get_webhook_info()["url"] == "https://x.example/h" and info.called
        c.delete_webhook()
        assert json.loads(dele.calls[0].request.content) == {"drop_pending_updates": False}
        c.set_webhook("https://x.example/h", "sek", ["message"])
        assert json.loads(setw.calls[0].request.content) == {"url": "https://x.example/h", "allowed_updates": ["message"],
                                                              "secret_token": "sek"}
