"""Supabase (Postgres via PostgREST) implementation. Uses the service key; RLS is bypassed."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from jobagent.db.base import STAT_FIELDS, Repository
from jobagent.models import Job
from jobagent.utils.dates import utcnow

_JOB_COLS = ["job_key", "company", "title", "url", "source", "location", "remote", "description",
             "requirements_json", "posted_at", "status", "match_json", "match_reasons", "score", "skip_reason"]


def _clean(d: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


class SupabaseRepository(Repository):
    def __init__(self, url: str, key: str, client: Any = None):
        if client is None:
            from supabase import create_client

            client = create_client(url, key)
        self.c = client

    def _t(self, name: str):
        return self.c.table(name)

    @staticmethod
    def _first(res) -> dict | None:
        data = res.data
        return data[0] if data else None

    # ---- jobs
    def upsert_job(self, job: Job) -> tuple[dict, bool]:
        existing = self.get_job_by_key(job.job_key)
        if existing:
            return existing, False
        d = job.model_dump()
        row = _clean({c: d.get(c) for c in _JOB_COLS})
        res = self._t("jobs").upsert(row, on_conflict="job_key", ignore_duplicates=True).execute()
        created = bool(res.data)
        return (self._first(res) if created else self.get_job_by_key(job.job_key)), created  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict | None:
        return self._first(self._t("jobs").select("*").eq("id", job_id).limit(1).execute())

    def get_job_by_key(self, job_key: str) -> dict | None:
        return self._first(self._t("jobs").select("*").eq("job_key", job_key).limit(1).execute())

    def update_job(self, job_id: str, **fields: Any) -> None:
        if fields:
            self._t("jobs").update(_clean(fields)).eq("id", job_id).execute()

    def list_jobs(self, statuses: list[str] | None = None, limit: int = 200) -> list[dict]:
        q = self._t("jobs").select("*")
        if statuses:
            q = q.in_("status", statuses)
        return q.order("discovered_at", desc=True).limit(limit).execute().data or []

    # ---- contacts
    def save_contact(self, company: str, email: str, source_url: str, confidence: str) -> dict:
        res = self._t("contacts").select("*").eq("company", company).eq("email", email).limit(1).execute()
        row = self._first(res)
        if row:
            return row
        return self._first(self._t("contacts").insert(
            {"company": company, "email": email, "source_url": source_url, "confidence": confidence}).execute())  # type: ignore[return-value]

    def get_contact(self, contact_id: str) -> dict | None:
        return self._first(self._t("contacts").select("*").eq("id", contact_id).limit(1).execute())

    # ---- applications
    def claim_application(self, job_id: str, route: str, company: str, role: str) -> tuple[dict, bool]:
        res = self._t("applications").upsert(
            {"job_id": job_id, "route": route, "company": company, "role": role, "status": "READY"},
            on_conflict="job_id,route", ignore_duplicates=True).execute()
        created = bool(res.data)
        return (self._first(res) if created else self.get_application(job_id, route)), created  # type: ignore[return-value]

    def get_application(self, job_id: str, route: str) -> dict | None:
        return self._first(self._t("applications").select("*").eq("job_id", job_id).eq("route", route).limit(1).execute())

    def update_application(self, app_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow().isoformat()
        self._t("applications").update(_clean(fields)).eq("id", app_id).execute()

    def list_applications(self, limit: int = 20) -> list[dict]:
        return self._t("applications").select("*").order("created_at", desc=True).limit(limit).execute().data or []

    def count_applications_on(self, day: date) -> int:
        res = (self._t("applications").select("id", count="exact")
               .gte("created_at", day.isoformat()).lt("created_at", (day + timedelta(days=1)).isoformat()).execute())
        return int(res.count or 0)

    # ---- tasks
    def create_task(self, job_id: str | None, type_: str, payload: dict | None = None, status: str = "queued") -> dict:
        return self._first(self._t("tasks").insert(
            {"job_id": job_id, "type": type_, "status": status, "payload": payload}).execute())  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict | None:
        return self._first(self._t("tasks").select("*").eq("id", task_id).limit(1).execute())

    def update_task(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow().isoformat()
        self._t("tasks").update(_clean(fields)).eq("id", task_id).execute()

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q = self._t("tasks").select("*")
        if status:
            q = q.eq("status", status)
        return q.order("created_at", desc=True).limit(limit).execute().data or []

    # ---- events / state / stats
    def add_event(self, level: str, action: str, job_id: str | None = None, detail: dict | None = None) -> None:
        self._t("events").insert({"level": level, "action": action, "job_id": job_id, "detail": detail}).execute()

    def list_events(self, limit: int = 50) -> list[dict]:
        return self._t("events").select("*").order("id", desc=True).limit(limit).execute().data or []

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._first(self._t("telegram_state").select("value").eq("key", key).limit(1).execute())
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._t("telegram_state").upsert({"key": key, "value": value}, on_conflict="key").execute()

    def _stat_row(self, day: date) -> dict:
        res = self._t("daily_stats").select("*").eq("day", day.isoformat()).limit(1).execute()
        row = self._first(res)
        if row is None:
            self._t("daily_stats").upsert({"day": day.isoformat()}, on_conflict="day", ignore_duplicates=True).execute()
            row = self._first(self._t("daily_stats").select("*").eq("day", day.isoformat()).limit(1).execute())
        return row  # type: ignore[return-value]

    def bump_stat(self, day: date, field: str, n: int = 1) -> None:
        if field not in STAT_FIELDS:
            raise ValueError(field)
        row = self._stat_row(day)
        self._t("daily_stats").update({field: int(row.get(field) or 0) + n}).eq("day", day.isoformat()).execute()

    def add_skip_reason(self, day: date, reason: str) -> None:
        row = self._stat_row(day)
        sr = dict(row.get("skip_reasons") or {})
        sr[reason] = sr.get(reason, 0) + 1
        self._t("daily_stats").update({"skip_reasons": sr}).eq("day", day.isoformat()).execute()

    def get_stats(self, day: date) -> dict:
        row = self._stat_row(day)
        row["skip_reasons"] = row.get("skip_reasons") or {}
        return row
