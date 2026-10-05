"""Running the pipeline twice (or retrying after failures/crashes) must never send/submit twice."""

from __future__ import annotations

import pytest
from helpers import CountingApply, FakeSMTP, make_ctx

from jobagent.pipeline import state as S
from jobagent.pipeline.runner import run_pipeline


@pytest.fixture
def live(settings):
    return settings.model_copy(update={"dry_run": False})


def test_two_live_runs_send_zero_duplicates(live, repo, profile, tg, resume):
    apply = CountingApply()
    run_pipeline(make_ctx(live, repo, profile, tg, apply_fn=apply))
    first_emails, first_submits = len(FakeSMTP.instances), len(apply.calls)
    assert first_emails >= 1 and first_submits >= 1

    ctx2 = make_ctx(live, repo, profile, tg, apply_fn=apply)  # same DB, same data
    run_pipeline(ctx2)
    assert len(FakeSMTP.instances) == 0, "second run re-sent an email"
    assert len(apply.calls) == first_submits, "second run re-submitted a form"
    emails = [a for a in repo.list_applications(500) if a["route"] == "email"]
    assert all(a["email_sent_at"] for a in emails) and len(emails) == first_emails


def test_apply_mode_rerun_after_success_is_noop(live, repo, profile, tg, resume):
    apply = CountingApply()
    run_pipeline(make_ctx(live, repo, profile, tg, apply_fn=apply))
    sent, calls = len(FakeSMTP.instances), len(apply.calls)
    for _ in range(3):
        run_pipeline(make_ctx(live, repo, profile, tg, mode="apply", apply_fn=apply))
    assert len(FakeSMTP.instances) == 0 and len(apply.calls) == calls and sent >= 1


def test_smtp_failure_then_retry_sends_exactly_once(live, repo, profile, tg, resume):
    box = [1]  # first send raises
    run_pipeline(make_ctx(live, repo, profile, tg, smtp_fail_box=box, apply_fn=CountingApply("DRY_RUN_STOPPED")))
    failed = repo.list_jobs([S.FAILED])
    assert failed, "a failed send must leave the job FAILED"
    # /retry path: tasks re-queued, job back to READY
    from jobagent.telegram.commands import handle_command

    handle_command("/retry", repo, live)
    run_pipeline(make_ctx(live, repo, profile, tg, mode="apply", smtp_fail_box=[0], apply_fn=CountingApply("DRY_RUN_STOPPED")))
    assert len(FakeSMTP.instances) == 1  # the retried email went out exactly once
    run_pipeline(make_ctx(live, repo, profile, tg, mode="apply", smtp_fail_box=[0], apply_fn=CountingApply("DRY_RUN_STOPPED")))
    assert len(FakeSMTP.instances) == 0  # a further run sends nothing
    sent_rows = [a for a in repo.list_applications(500) if a["route"] == "email" and a["email_sent_at"]]
    assert len(sent_rows) == 2  # both email jobs sent once each overall


def test_crash_between_send_and_record_never_resends(live, repo, profile, tg, resume):
    run_pipeline(make_ctx(live, repo, profile, tg, mode="jobs"))  # drafts only
    app = next(a for a in repo.list_applications(100) if a["route"] == "email")
    repo.update_application(app["id"], status="SENDING")  # simulate: crashed right after "about to send"
    ctx = make_ctx(live, repo, profile, tg, mode="apply", apply_fn=CountingApply("DRY_RUN_STOPPED"))
    run_pipeline(ctx)
    row = repo.get_application(app["job_id"], "email")
    assert row["email_sent_at"] is None, "ambiguous send must not be retried automatically"
    assert repo.get_job(app["job_id"])["status"] == S.WAITING_USER
    assert any("state unknown" in m for m in tg.sent)


def test_unique_application_per_job_route(repo, job_factory):
    row, _ = repo.upsert_job(job_factory())
    a, created = repo.claim_application(row["id"], "email", "c", "r")
    b, created2 = repo.claim_application(row["id"], "email", "c", "r")
    assert created and not created2 and a["id"] == b["id"]
    assert repo.claim_application(row["id"], "browser", "c", "r")[1] is True


def test_status_guard_blocks_email_sent_without_record(repo, job_factory):
    row, _ = repo.upsert_job(job_factory())
    for s in (S.QUALIFIED, S.READY):
        S.transition(repo, row["id"], s)
    with pytest.raises(S.IllegalTransition):
        S.transition(repo, row["id"], S.EMAIL_SENT)  # no email application row yet
    app, _ = repo.claim_application(row["id"], "email", "c", "r")
    with pytest.raises(S.IllegalTransition):
        S.transition(repo, row["id"], S.EMAIL_SENT)  # row exists but nothing was sent
    repo.update_application(app["id"], email_sent_at="2026-01-01T00:00:00+00:00")
    S.transition(repo, row["id"], S.EMAIL_SENT)
    with pytest.raises(S.IllegalTransition):
        S.transition(repo, row["id"], S.READY)  # terminal: never goes back to sendable


def test_illegal_transitions(repo, job_factory):
    row, _ = repo.upsert_job(job_factory())
    with pytest.raises(S.IllegalTransition):
        S.transition(repo, row["id"], S.SUBMITTED)
    with pytest.raises(S.IllegalTransition):
        S.transition(repo, row["id"], S.EMAIL_SENT)


def test_killswitch_blocks_all_actions(live, repo, profile, tg, resume):
    from jobagent.telegram.commands import handle_command

    run_pipeline(make_ctx(live, repo, profile, tg, mode="jobs"))
    handle_command("/killswitch", repo, live)
    apply = CountingApply()
    run_pipeline(make_ctx(live, repo, profile, tg, mode="apply", apply_fn=apply))
    assert FakeSMTP.instances == [] and apply.calls == []
