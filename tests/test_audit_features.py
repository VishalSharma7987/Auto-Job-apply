"""Behaviours added after auditing against the requirements doc (see docs/REQUIREMENTS_AUDIT.md)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import ROOT, make_job
from helpers import CountingApply, FakeSMTP, make_ctx

from jobagent.apply.detectors import detect_all, detect_identity
from jobagent.email.generator import unverified_claims
from jobagent.main import run
from jobagent.matching.scoring import score_job
from jobagent.models import MatchResult
from jobagent.pipeline import state as S
from jobagent.pipeline.runner import run_pipeline
from jobagent.profile import load_profile, select_resume, term_variants
from jobagent.telegram.commands import handle_command
from jobagent.utils.dates import utcnow


# ---------------------------------------------------------------- profile = section 2 of the doc, nothing else
def test_real_profile_comes_from_section_2_and_is_complete(settings):
    p = load_profile(settings.model_copy(update={"fake_mode": False}))
    assert p.name == "Vishal Sharma" and p.is_complete
    for must in ("Python", "React.js", "Next.js", "LangChain", "LlamaIndex", "Pinecone", "Twilio", "n8n", "MCP",
                 "Playwright", "Supabase", "WhatsApp APIs", "Google Calendar API"):
        assert must in p.skills
    assert "RAG applications" in p.project_areas and "WhatsApp appointment automation" in p.project_areas
    assert set(p.preferred_locations) >= {"Remote", "Pune", "Bangalore", "Hyderabad"}
    assert p.projects == [] and p.education == [] and p.experience == []  # nothing invented
    text = (ROOT / "profile" / "profile.yaml").read_text(encoding="utf-8")
    assert "@" not in text.replace("# ", "") or "gmail" not in text  # no email/phone in the yaml


def test_skill_variants_let_honest_wording_through_but_not_new_claims(profile):
    import yaml

    from jobagent.profile import Profile

    real = Profile(**yaml.safe_load((ROOT / "profile" / "profile.yaml").read_text(encoding="utf-8")))
    assert {"react", "react.js"} <= term_variants("React.js")
    assert "llm" in term_variants("LLMs") and "openai" in term_variants("OpenAI API")
    assert unverified_claims("I built RAG apps with React, Node and Python using LangChain and Pinecone.", real) == []
    assert set(unverified_claims("I know Kubernetes and Terraform.", real)) == {"kubernetes", "terraform"}


# ---------------------------------------------------------------- section 5: already contacted / applied
def test_same_company_and_role_is_never_applied_twice(settings, repo, profile, tg):
    first = make_ctx(settings, repo, profile, tg, apply_fn=CountingApply("DRY_RUN_STOPPED"))
    run_pipeline(first)
    a = next(x for x in repo.list_applications(100) if x["route"] == "email")
    repo.update_application(a["id"], status="SENT", email_sent_at=utcnow().isoformat())
    # the same role re-appears under a new URL (cross-post) and is qualified
    twin, _ = repo.upsert_job(make_job(company=a["company"], title=a["role"], url="https://elsewhere.example/job/9",
                                       location="Remote - India", description="0-2 years. Python, LangChain, RAG."))
    S.transition(repo, twin["id"], S.QUALIFIED, score=99)
    run_pipeline(make_ctx(settings, repo, profile, tg, mode="jobs"))
    assert repo.get_job(twin["id"])["status"] == S.REJECTED and repo.get_job(twin["id"])["skip_reason"] == "already_contacted"
    assert repo.get_application(twin["id"], "email") is None


def test_same_recruiting_address_not_emailed_twice_within_14_days(settings, repo, profile, tg):
    run_pipeline(make_ctx(settings, repo, profile, tg, apply_fn=CountingApply("DRY_RUN_STOPPED")))
    a = next(x for x in repo.list_applications(100) if x["route"] == "email")
    repo.update_application(a["id"], status="SENT", email_sent_at=(utcnow() - timedelta(days=2)).isoformat())
    other, _ = repo.upsert_job(make_job(company=a["company"], title="Different Full Stack Developer",
                                        url="https://elsewhere.example/job/77", location="Remote", description="0-1 years."))
    S.transition(repo, other["id"], S.QUALIFIED, score=99)
    run_pipeline(make_ctx(settings, repo, profile, tg, mode="jobs"))
    assert repo.get_application(other["id"], "email") is None  # cool-down: no second mail to the same address


def test_second_run_counts_known_jobs_as_duplicates(settings, repo, profile, tg):
    run_pipeline(make_ctx(settings, repo, profile, tg))
    ctx2 = make_ctx(settings, repo, profile, tg)
    run_pipeline(ctx2)
    stats = repo.get_stats(ctx2.day)
    assert stats["skip_reasons"].get("duplicate", 0) >= 10 and stats["scanned"] == 40


# ---------------------------------------------------------------- section 8: more matching signals
def test_match_result_carries_education_salary_employment_type():
    m = MatchResult(decision="QUALIFIED", education_required="B.Tech", salary="6-8 LPA", employment_type="full-time")
    assert (m.education_required, m.salary, m.employment_type) == ("B.Tech", "6-8 LPA", "full-time")


def test_score_prefers_official_boards_and_full_time(profile):
    m = MatchResult(decision="QUALIFIED", must_have_skills=["python"], matched_skills=["python"], employment_type="full-time")
    official, why = score_job(make_job(source="greenhouse"), m)
    agg, _ = score_job(make_job(source="remotive"), m)
    intern, _ = score_job(make_job(source="greenhouse"), m.model_copy(update={"employment_type": "internship"}))
    assert official > agg and official > intern and any("official company board" in w for w in why)


# ---------------------------------------------------------------- section 8/11: resume selection
def test_resume_variant_selection(settings, tmp_path):
    s = settings.model_copy(update={"resume_path": str(tmp_path / "resume.pdf")})
    assert select_resume(s, "AI Engineer").name == "resume.pdf"  # no variants present -> default
    (tmp_path / "resume_ai.pdf").write_bytes(b"%PDF")
    (tmp_path / "resume_fullstack.pdf").write_bytes(b"%PDF")
    assert select_resume(s, "Agentic AI Developer").name == "resume_ai.pdf"
    assert select_resume(s, "Full Stack Developer").name == "resume_fullstack.pdf"
    assert select_resume(s, "Full Stack AI Developer").name == "resume_ai.pdf"
    assert select_resume(s, "Backend Python Developer").name == "resume.pdf"


def test_email_goes_out_with_the_selected_resume(settings, repo, profile, tg, tmp_path):
    live = settings.model_copy(update={"dry_run": False, "resume_path": str(tmp_path / "resume.pdf")})
    (tmp_path / "resume.pdf").write_bytes(b"%PDF default")
    (tmp_path / "resume_fullstack.pdf").write_bytes(b"%PDF fullstack")
    run_pipeline(make_ctx(live, repo, profile, tg, apply_fn=CountingApply("DRY_RUN_STOPPED")))
    app = next(a for a in repo.list_applications(100) if a["route"] == "email" and "Full Stack Developer" in a["role"])
    assert app["resume_version"] == "resume_fullstack.pdf"
    sent = next(m for m in FakeSMTP.instances if "Full Stack Developer" in m["Subject"])
    assert next(sent.iter_attachments()).get_content().startswith(b"%PDF fullstack")


# ---------------------------------------------------------------- section 12/13: more blockers, no crashes
def test_identity_verification_detected():
    assert detect_identity("<p>Please verify your identity by uploading a government-issued ID</p>").kind == "identity"
    assert "identity" in {d.kind for d in detect_all("<p>Identity verification required</p>")}
    assert detect_identity("<p>Tell us about yourself</p>") is None


def test_unexpected_browser_exception_is_reported_not_fatal(settings, repo, profile, tg):
    def boom(*a, **k):
        raise RuntimeError("website did something weird")

    s = run_pipeline(make_ctx(settings, repo, profile, tg, apply_fn=boom))
    assert s.failed >= 1
    assert any("Application failed" in m and "website did something weird" in m for m in tg.sent)
    failed = repo.list_jobs([S.FAILED], 50)
    assert failed and all(t["status"] == "failed" for t in repo.list_tasks("failed", 50))


# ---------------------------------------------------------------- section 14: profile in DB + audit log
def test_run_stores_verified_profile_without_pii(settings, repo, tg):
    assert run(settings, "full", repo=repo, tg=tg) == 0
    data = repo.get_profile()
    assert data and "Python" in data["skills"] and data["target_roles"]
    blob = str(data)
    assert "candidate@example.com" not in blob and "+91" not in blob and "gmail_app_password" not in blob.lower()


def test_audit_trail_records_key_decisions(settings, repo, profile, tg, resume):
    live = settings.model_copy(update={"dry_run": False})
    run_pipeline(make_ctx(live, repo, profile, tg, apply_fn=CountingApply()))
    actions = {e["action"] for e in repo.list_events(2000)}
    assert {"job_qualified", "job_rejected", "contact_found", "draft_prepared", "email_sent", "application_submitted",
            "run_finished"} <= actions
    sent = next(e for e in repo.list_events(2000) if e["action"] == "email_sent")
    assert sent["detail"]["source_url"].startswith("http")  # provenance of the address is in the audit log


# ---------------------------------------------------------------- section 17: /settings shows preferences
def test_settings_command_shows_preferences(repo, settings):
    out = handle_command("/settings", repo, settings).reply
    assert "TARGET_ROLES" in out and "Agentic AI Developer" in out and "Pune" in out
    assert "10-20 quality applications per day" in out and "0-1 year" in out
    assert "TESTTOKEN" not in out


@pytest.mark.parametrize("cmd", ["/status", "/history", "/report"])
def test_status_history_report_work_on_empty_db(cmd, repo, settings):
    assert handle_command(cmd, repo, settings).reply
