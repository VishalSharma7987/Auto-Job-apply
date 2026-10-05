"""Full pipeline: fake adapter + fake LLM + sqlite, DRY_RUN."""

from __future__ import annotations

import json

from helpers import CountingApply, FakeSMTP, make_ctx

from jobagent.main import run
from jobagent.pipeline import state as S
from jobagent.pipeline.runner import run_pipeline
from jobagent.utils.dates import today_utc


def test_full_dry_run_sends_nothing_and_stores_drafts(settings, repo, profile, tg):
    apply = CountingApply("DRY_RUN_STOPPED")
    ctx = make_ctx(settings, repo, profile, tg, apply_fn=apply)
    s = run_pipeline(ctx)
    assert s.scanned == 20  # 20 fake jobs (one is a cross-posted duplicate, counted as skipped)
    assert s.qualified >= 5 and s.selected >= 1
    assert FakeSMTP.instances == [] and s.emails_sent == 0 and s.browser_submitted == 0

    apps = repo.list_applications(100)
    assert apps and all(a["email_subject"].startswith("Application – ") for a in apps)
    assert all(a["email_sent_at"] is None and a["submitted_at"] is None for a in apps)
    emails = [a for a in apps if a["route"] == "email"]
    assert emails and all(a["contact_id"] for a in emails)
    for a in emails:  # contact provenance is stored
        c = repo.get_contact(a["contact_id"])
        assert c["source_url"].startswith("http") and c["confidence"] in ("HIGH", "MEDIUM")


def test_reasons_scores_and_match_cache_are_stored(settings, repo, profile, tg):
    run_pipeline(make_ctx(settings, repo, profile, tg))
    qualified = [j for j in repo.list_jobs(limit=100) if j["match_json"]]
    assert qualified
    j = next(x for x in qualified if x["status"] in (S.READY, S.WAITING_USER, S.QUALIFIED))
    assert j["match_reasons"] and j["score"] is not None and j["requirements_json"] is not None
    rejected = [x for x in repo.list_jobs([S.REJECTED], 100)]
    assert rejected and all(x["skip_reason"] for x in rejected)


def test_second_run_costs_zero_llm_calls(settings, repo, profile, tg):
    ctx1 = make_ctx(settings, repo, profile, tg)
    run_pipeline(ctx1)
    assert ctx1.llm.calls > 0
    ctx2 = make_ctx(settings, repo, profile, tg)
    run_pipeline(ctx2)
    assert ctx2.llm.calls == 0


def test_daily_cap_respected(settings, repo, profile, tg):
    capped = settings.model_copy(update={"max_applications_per_day": 3})
    ctx = make_ctx(capped, repo, profile, tg, apply_fn=CountingApply("DRY_RUN_STOPPED"))
    s = run_pipeline(ctx)
    assert s.selected == 3 and repo.count_applications_on(today_utc()) == 3
    run_pipeline(make_ctx(capped, repo, profile, tg))  # same day again: cap already used
    assert repo.count_applications_on(today_utc()) == 3


def test_prompt_injection_text_never_reaches_llm_or_email(settings, repo, profile, tg):
    ctx = make_ctx(settings, repo, profile, tg)
    seen: list[str] = []
    real = ctx.llm.complete_json

    def spy(system, user, schema, temperature=0.0):
        seen.append(user)
        return real(system, user, schema, temperature)

    ctx.llm.complete_json = spy  # type: ignore[method-assign]
    run_pipeline(ctx)
    assert seen and not any("ignore all previous" in u.lower() for u in seen)
    assert not any("attacker@evil.example" in (a["email_body"] or "") for a in repo.list_applications(100))


def test_quota_exceeded_is_reported_not_crashed(settings, repo, profile, tg):
    from jobagent.llm.client import QuotaExceeded

    class QuotaLLM:
        calls = 0

        def complete_json(self, *a, **k):
            raise QuotaExceeded("openrouter.ai", "HTTP 429: daily free limit")

    s = run_pipeline(make_ctx(settings, repo, profile, tg, llm=QuotaLLM()))
    assert s.quota_limited and "openrouter.ai" in s.quota_detail
    assert any("⚠️ Free-tier limit reached: openrouter.ai" in m for m in tg.sent)
    assert repo.list_jobs([S.DISCOVERED]), "unmatched jobs stay DISCOVERED so a later run can finish them"
    assert any("Free-tier limit reached" in m for m in tg.sent if "Daily Job Report" in m)


def test_paused_run_does_nothing(settings, repo, profile, tg):
    repo.set_state("paused", "1")
    s = run_pipeline(make_ctx(settings, repo, profile, tg))
    assert s.scanned == 0 and repo.list_jobs() == []


def test_waiting_user_notification_contains_continue_commands(settings, repo, profile, tg):
    run_pipeline(make_ctx(settings, repo, profile, tg, apply_fn=lambda *a, **k: __import__("jobagent.apply.playwright_runner", fromlist=["x"]).ApplyOutcome(
        "WAITING_USER", "captcha detected", "shot.png")))
    msgs = [m for m in tg.sent if m.startswith("⏳ Waiting for you")]
    assert msgs and "/approve " in msgs[0] and "/skip " in msgs[0] and "captcha" in msgs[0]
    assert tg.photos == ["shot.png"] * len(tg.photos) and tg.photos


def test_main_run_end_to_end_via_cli_function(settings, repo, tg, capsys, tmp_path):
    """python -m jobagent run --mode full (FAKE_MODE, sqlite, dry run) prints the daily report."""
    code = run(settings, "full", repo=repo, tg=tg)
    out = capsys.readouterr().out
    assert code == 0 and "Daily Job Report" in out and "DRY RUN" in out
    assert "Jobs scanned: 20" in out and "Selected opportunities (company | role | status | application URL):" in out


def test_main_processes_relay_payload_before_running(settings, repo, tg, tmp_path):
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"client_payload": {"update": {"update_id": 7, "message": {"chat": {"id": 4242}, "text": "/pause"}}}}))
    code = run(settings, "pause", repo=repo, tg=tg, event_path=str(ev))
    assert code == 0 and repo.get_state("paused") == "1" and any("Paused" in m for m in tg.sent)
    # a paused agent skips scheduled runs
    assert run(settings, "full", repo=repo, tg=tg) == 0
    assert repo.list_jobs() == [] and any("paused" in m.lower() for m in tg.sent)


def test_real_mode_without_profile_skills_aborts(settings, repo, tg, tmp_path):
    empty = tmp_path / "profile.yaml"
    empty.write_text("name: Vishal Sharma\nskills: []\n", encoding="utf-8")
    real = settings.model_copy(update={"fake_mode": False, "profile_path": str(empty)})
    assert run(real, "full", repo=repo, tg=tg) == 2
    assert any("profile.yaml has no skills" in m for m in tg.sent)
