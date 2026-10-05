"""Minimal in-memory stand-in for the supabase-py query builder (PostgREST semantics we rely on).

It lets SupabaseRepository run in unit tests without a network. It enforces the same UNIQUE constraints and
column defaults as src/jobagent/db/migrations/001_init.sql, so the repository's upsert / ignore_duplicates logic
is genuinely exercised. The real database is covered by tests/test_supabase_integration.py (opt-in).
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

UNIQUE = {
    "jobs": ["job_key"], "contacts": ["company", "email"], "applications": ["job_id", "route"],
    "daily_stats": ["day"], "telegram_state": ["key"], "profile": ["key"],
}
UUID_PK = {"jobs", "contacts", "applications", "tasks"}
NOW_COLS = {"jobs": ["discovered_at"], "contacts": ["found_at", "verified_at"], "applications": ["created_at", "updated_at"],
            "tasks": ["created_at", "updated_at"], "events": ["ts"], "profile": ["updated_at"]}
DEFAULTS = {
    "jobs": {"status": "DISCOVERED", "remote": False},
    "applications": {"status": "READY"},
    "tasks": {"status": "queued", "retry_count": 0},
    "daily_stats": {"scanned": 0, "qualified": 0, "selected": 0, "emails_sent": 0, "browser_submitted": 0,
                    "waiting_user": 0, "failed": 0, "skipped": 0, "skip_reasons": {}},
}
NOT_NULL = {"jobs": ["job_key", "company", "title"], "applications": ["job_id", "route"], "tasks": ["type"],
            "contacts": ["company", "email", "source_url", "confidence"]}


class APIError(Exception):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeClient:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._events_seq = 0

    def table(self, name: str) -> Query:
        return Query(self, name)


class Query:
    def __init__(self, client: FakeClient, name: str):
        self.c, self.name = client, name
        self.rows = client.tables.setdefault(name, [])
        self._op = "select"
        self._payload: dict = {}
        self._filters: list = []
        self._order = None
        self._limit = None
        self._count = None
        self._upsert_opts: dict = {}

    # ---- builders
    def select(self, cols="*", count=None):
        self._op, self._count = "select", count
        return self

    def insert(self, row):
        self._op, self._payload = "insert", row
        return self

    def upsert(self, row, on_conflict=None, ignore_duplicates=False):
        self._op, self._payload = "upsert", row
        self._upsert_opts = {"on_conflict": on_conflict, "ignore": ignore_duplicates}
        return self

    def update(self, row):
        self._op, self._payload = "update", row
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def gte(self, col, val):
        self._filters.append(lambda r: str(r.get(col)) >= str(val))
        return self

    def lt(self, col, val):
        self._filters.append(lambda r: str(r.get(col)) < str(val))
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # ---- execution
    def _match(self):
        out = [r for r in self.rows if all(f(r) for f in self._filters)]
        if self._order:
            col, desc = self._order
            out.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return out

    def _prepare(self, row: dict) -> dict:
        row = copy.deepcopy(row)
        t = self.name
        for k, v in DEFAULTS.get(t, {}).items():
            row.setdefault(k, copy.deepcopy(v))
        now = datetime.now(UTC).isoformat()
        for col in NOW_COLS.get(t, []):
            row.setdefault(col, now)
        if t in UUID_PK:
            row.setdefault("id", str(uuid.uuid4()))
        if t == "events":
            self.c._events_seq += 1
            row.setdefault("id", self.c._events_seq)
        for col in NOT_NULL.get(t, []):
            if row.get(col) is None:
                raise APIError(f"null value in column {col}", "23502")
        if t == "tasks" and row["status"] not in ("queued", "running", "waiting_user", "completed", "failed"):
            raise APIError("tasks_status_check", "23514")
        if t == "applications" and row["route"] not in ("email", "browser"):
            raise APIError("applications route check", "23514")
        return row

    def _conflict(self, row: dict):
        cols = UNIQUE.get(self.name)
        if not cols:
            return None
        for r in self.rows:
            if all(r.get(c) == row.get(c) for c in cols):
                return r
        return None

    def execute(self) -> Resp:
        if self._op == "select":
            rows = self._match()
            total = len(rows)
            if self._limit is not None:
                rows = rows[: self._limit]
            return Resp(copy.deepcopy(rows), total if self._count == "exact" else None)
        if self._op == "insert":
            row = self._prepare(self._payload)
            if self._conflict(row):
                raise APIError("duplicate key value violates unique constraint", "23505")
            self.rows.append(row)
            return Resp([copy.deepcopy(row)])
        if self._op == "upsert":
            row = self._prepare(self._payload)
            hit = self._conflict(row)
            if hit is not None:
                if self._upsert_opts["ignore"]:
                    return Resp([])
                hit.update({k: v for k, v in copy.deepcopy(self._payload).items()})
                return Resp([copy.deepcopy(hit)])
            self.rows.append(row)
            return Resp([copy.deepcopy(row)])
        if self._op == "update":
            hit = self._match()
            for r in hit:
                r.update(copy.deepcopy(self._payload))
            return Resp(copy.deepcopy(hit))
        if self._op == "delete":
            doomed = self._match()
            for r in doomed:
                self.rows.remove(r)
            return Resp(doomed)
        raise AssertionError(self._op)
