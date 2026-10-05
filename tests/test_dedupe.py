from __future__ import annotations

from conftest import make_job

from jobagent.db.sqlite_repo import SqliteRepository
from jobagent.discovery.dedupe import dedupe


def test_same_key_deduped():
    a = make_job()
    assert len(dedupe([a, a.model_copy()])) == 1


def test_same_company_title_location_on_two_boards_deduped():
    a = make_job(url="https://boards.greenhouse.io/acmeai/jobs/1")
    b = make_job(url="https://jobs.lever.co/acmeai/9")
    assert len(dedupe([a, b])) == 1


def test_different_roles_kept():
    assert len(dedupe([make_job(), make_job(title="AI Engineer", url="https://x.io/2")])) == 2


def test_db_upsert_is_unique_by_job_key():
    repo = SqliteRepository(":memory:")
    row, created = repo.upsert_job(make_job())
    row2, created2 = repo.upsert_job(make_job())
    assert created and not created2 and row["id"] == row2["id"]
    assert len(repo.list_jobs()) == 1
