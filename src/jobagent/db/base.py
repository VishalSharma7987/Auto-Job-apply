"""Repository interface. Rows are plain dicts (JSON fields already decoded)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from jobagent.models import Job

STAT_FIELDS = ("scanned", "qualified", "selected", "emails_sent", "browser_submitted",
               "waiting_user", "failed", "skipped")


class Repository(ABC):
    # ---- jobs
    @abstractmethod
    def upsert_job(self, job: Job) -> tuple[dict, bool]:
        """Insert if job_key is new. Returns (row, created). Never overwrites an existing row."""

    @abstractmethod
    def get_job(self, job_id: str) -> dict | None: ...

    @abstractmethod
    def get_job_by_key(self, job_key: str) -> dict | None: ...

    @abstractmethod
    def update_job(self, job_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def list_jobs(self, statuses: list[str] | None = None, limit: int = 200) -> list[dict]: ...

    # ---- contacts
    @abstractmethod
    def save_contact(self, company: str, email: str, source_url: str, confidence: str) -> dict: ...

    @abstractmethod
    def get_contact(self, contact_id: str) -> dict | None: ...

    # ---- applications
    @abstractmethod
    def claim_application(self, job_id: str, route: str, company: str, role: str) -> tuple[dict, bool]:
        """Atomically create (job_id, route) row. Returns (row, created). UNIQUE(job_id, route)."""

    @abstractmethod
    def get_application(self, job_id: str, route: str) -> dict | None: ...

    @abstractmethod
    def update_application(self, app_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def list_applications(self, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def count_applications_on(self, day: date) -> int:
        """Applications claimed (created) on the given UTC day - used for the daily cap."""

    # ---- tasks
    @abstractmethod
    def create_task(self, job_id: str | None, type_: str, payload: dict | None = None,
                    status: str = "queued") -> dict: ...

    @abstractmethod
    def get_task(self, task_id: str) -> dict | None: ...

    @abstractmethod
    def update_task(self, task_id: str, **fields: Any) -> None: ...

    @abstractmethod
    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]: ...

    # ---- events / state / stats
    @abstractmethod
    def add_event(self, level: str, action: str, job_id: str | None = None, detail: dict | None = None) -> None: ...

    @abstractmethod
    def list_events(self, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    def get_state(self, key: str, default: str | None = None) -> str | None: ...

    @abstractmethod
    def set_state(self, key: str, value: str) -> None: ...

    @abstractmethod
    def bump_stat(self, day: date, field: str, n: int = 1) -> None: ...

    @abstractmethod
    def add_skip_reason(self, day: date, reason: str) -> None: ...

    @abstractmethod
    def get_stats(self, day: date) -> dict: ...


def make_repo(settings) -> Repository:
    if settings.db_backend == "supabase":
        from jobagent.db.supabase_repo import SupabaseRepository

        if not (settings.supabase_url and settings.supabase_key):
            raise RuntimeError("DB_BACKEND=supabase requires SUPABASE_URL and SUPABASE_KEY")
        return SupabaseRepository(settings.supabase_url, settings.supabase_key)
    from jobagent.db.sqlite_repo import SqliteRepository

    return SqliteRepository(settings.abs_path(settings.sqlite_path))
