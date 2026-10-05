"""One behavioural contract, run against every Repository implementation.

  sqlite          - always
  fake_supabase   - SupabaseRepository over an in-memory PostgREST fake (always)
  (real Supabase  - see tests/test_supabase_integration.py, opt-in via SUPABASE_URL/SUPABASE_KEY)
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from conftest import make_job
from fake_supabase import FakeClient

from jobagent.db.sqlite_repo import SqliteRepository
from jobagent.db.supabase_repo import SupabaseRepository
from jobagent.utils.dates import today_utc


@pytest.fixture(params=["sqlite", "fake_supabase"])
def any_repo(request):
    if request.param == "sqlite":
        return SqliteRepository(":memory:")
    return SupabaseRepository("http://fake", "fake-key", client=FakeClient())


def uid() -> str:
    return uuid.uuid4().hex[:10]


def new_job(repo, **kw):
    tag = uid()
    job = make_job(company=f"ContractCo {tag}", title="AI Developer", url=f"https://x.io/{tag}", **kw)
    row, created = repo.upsert_job(job)
    assert created
    return row


def run_contract(repo, uid=uid, stat_day: date = date(2001, 1, 1)):
    """Shared by the fake-backed and the real-database test."""
    # ---- jobs: unique job_key, never overwritten
    row = new_job(repo)
    again, created = repo.upsert_job(make_job(company=row["company"], title=row["title"], url=row["url"],
                                              description="DIFFERENT"))
    assert not created and again["id"] == row["id"] and again["description"] != "DIFFERENT"
    assert repo.get_job(row["id"])["job_key"] == row["job_key"] == repo.get_job_by_key(row["job_key"])["job_key"]
    assert row["status"] == "DISCOVERED"

    # ---- jobs: updates incl. json + array columns round-trip
    repo.update_job(row["id"], status="QUALIFIED", score=77, match_reasons=["RAG + LangChain + 0-2 yrs match"],
                    match_json={"decision": "QUALIFIED", "reasons": ["x"]}, requirements_json={"must_have": ["python"]})
    j = repo.get_job(row["id"])
    assert j["status"] == "QUALIFIED" and j["score"] == 77
    assert j["match_reasons"] == ["RAG + LangChain + 0-2 yrs match"]
    assert j["match_json"]["decision"] == "QUALIFIED" and j["requirements_json"]["must_have"] == ["python"]
    assert row["id"] in [x["id"] for x in repo.list_jobs(["QUALIFIED"], 500)]
    assert row["id"] not in [x["id"] for x in repo.list_jobs(["REJECTED"], 500)]

    # ---- contacts: provenance stored, no duplicates
    company = f"ContractCo-{uid()}"
    c1 = repo.save_contact(company, "careers@contract.example", "https://contract.example/careers", "MEDIUM")
    c2 = repo.save_contact(company, "careers@contract.example", "https://other.example", "HIGH")
    assert c1["id"] == c2["id"] and c1["source_url"] == "https://contract.example/careers" and c1["confidence"] == "MEDIUM"
    assert c1["verified_at"] and repo.get_contact(c1["id"])["email"] == "careers@contract.example"

    # ---- applications: UNIQUE(job_id, route) = the "never send twice" backbone
    app, created = repo.claim_application(row["id"], "email", row["company"], row["title"])
    app2, created2 = repo.claim_application(row["id"], "email", row["company"], row["title"])
    assert created and not created2 and app["id"] == app2["id"] and app["status"] == "READY"
    assert repo.claim_application(row["id"], "browser", row["company"], row["title"])[1] is True
    repo.update_application(app["id"], status="SENT", email_sent_at="2026-01-01T00:00:00+00:00", contact_id=c1["id"],
                            email_subject="S", email_body="B", resume_version="resume.pdf")
    a = repo.get_application(row["id"], "email")
    assert a["status"] == "SENT" and a["email_sent_at"] and a["contact_id"] == c1["id"] and a["email_body"] == "B"
    assert repo.get_application(row["id"], "nope") is None
    assert row["id"] in [x["job_id"] for x in repo.list_applications(500)]
    assert repo.count_applications_on(today_utc()) >= 2

    # ---- tasks
    t = repo.create_task(row["id"], "browser", {"reason": "captcha"}, status="waiting_user")
    assert t["status"] == "waiting_user" and t["retry_count"] == 0 and repo.get_task(t["id"])["payload"] == {"reason": "captcha"}
    repo.update_task(t["id"], status="failed", last_error="boom", retry_count=1)
    got = repo.get_task(t["id"])
    assert got["status"] == "failed" and got["last_error"] == "boom" and got["retry_count"] == 1
    assert t["id"] in [x["id"] for x in repo.list_tasks("failed", 500)]
    assert t["id"] not in [x["id"] for x in repo.list_tasks("queued", 500)]

    # ---- events
    tag = f"itest_{uid()}"
    repo.add_event("info", tag, row["id"], {"k": "v"})
    ev = [e for e in repo.list_events(200) if e["action"] == tag]
    assert ev and ev[0]["detail"] == {"k": "v"}

    # ---- telegram state
    key = f"itest_{uid()}"
    assert repo.get_state(key) is None and repo.get_state(key, "dflt") == "dflt"
    repo.set_state(key, "1")
    repo.set_state(key, "2")
    assert repo.get_state(key) == "2"

    # ---- daily stats
    assert repo.get_stats(stat_day)["scanned"] == 0
    repo.bump_stat(stat_day, "scanned", 5)
    repo.bump_stat(stat_day, "scanned")
    repo.bump_stat(stat_day, "emails_sent")
    repo.add_skip_reason(stat_day, "senior_role")
    repo.add_skip_reason(stat_day, "senior_role")
    repo.add_skip_reason(stat_day, "duplicate")
    st = repo.get_stats(stat_day)
    assert st["scanned"] == 6 and st["emails_sent"] == 1 and st["skip_reasons"] == {"senior_role": 2, "duplicate": 1}
    with pytest.raises(ValueError):
        repo.bump_stat(stat_day, "not_a_field")

    # ---- verified profile (requirements section 14)
    pkey = f"itest_{uid()}"
    assert repo.get_profile(pkey) is None
    repo.save_profile({"skills": ["Python"]}, pkey)
    repo.save_profile({"skills": ["Python", "RAG"]}, pkey)
    assert repo.get_profile(pkey) == {"skills": ["Python", "RAG"]}
    return {"job_id": row["id"], "company": company, "state_key": key, "profile_key": pkey, "event": tag}


def test_repository_contract(any_repo):
    run_contract(any_repo)


def test_supabase_repo_surfaces_check_constraint_errors():
    repo = SupabaseRepository("http://fake", "k", client=FakeClient())
    with pytest.raises(Exception):  # noqa: B017 - APIError from the fake, same as PostgREST 400/23514
        repo.create_task(None, "x", None, status="bogus")
