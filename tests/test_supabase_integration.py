"""Integration test against the REAL Supabase project. Skipped unless SUPABASE_URL and SUPABASE_KEY are set.

Run it yourself (PowerShell):
    $env:SUPABASE_URL="https://xxxx.supabase.co"; $env:SUPABASE_KEY="<service role / secret key>"
    pytest tests/test_supabase_integration.py -v

It writes rows tagged "ContractCo"/"itest_*" (plus one far-past daily_stats row, 2001-01-01) and deletes them again at
the end. It never touches other rows. Migrations 001 + 002 must be applied first.
"""

from __future__ import annotations

import os

import pytest
from test_repository_contract import run_contract

from jobagent.db.supabase_repo import SupabaseRepository

URL, KEY = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
pytestmark = pytest.mark.skipif(not (URL and KEY), reason="SUPABASE_URL / SUPABASE_KEY not set")

STAT_DAY = __import__("datetime").date(2001, 1, 1)


@pytest.fixture
def real_repo():
    repo = SupabaseRepository(URL, KEY)
    yield repo
    # cleanup: only rows this test creates
    t = repo.c.table
    t("jobs").delete().like("company", "ContractCo %").execute()  # applications/tasks cascade
    t("contacts").delete().like("company", "ContractCo-%").execute()
    t("events").delete().like("action", "itest_%").execute()
    t("telegram_state").delete().like("key", "itest_%").execute()
    t("profile").delete().like("key", "itest_%").execute()
    t("daily_stats").delete().eq("day", STAT_DAY.isoformat()).execute()


def test_schema_tables_exist_and_service_key_works(real_repo):
    for table in ("jobs", "contacts", "applications", "tasks", "events", "telegram_state", "daily_stats", "profile"):
        real_repo.c.table(table).select("*").limit(1).execute()  # raises if the table is missing


def test_real_repository_contract(real_repo):
    run_contract(real_repo, stat_day=STAT_DAY)


def test_real_unique_constraints_reject_duplicates(real_repo):
    from conftest import make_job

    job = make_job(company="ContractCo dup", url="https://x.io/dup-int-test")
    row, created = real_repo.upsert_job(job)
    assert created
    assert real_repo.upsert_job(job)[1] is False
    a, c1 = real_repo.claim_application(row["id"], "email", "ContractCo dup", "AI Developer")
    b, c2 = real_repo.claim_application(row["id"], "email", "ContractCo dup", "AI Developer")
    assert c1 and not c2 and a["id"] == b["id"]
