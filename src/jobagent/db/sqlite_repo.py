from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from jobagent.db.base import STAT_FIELDS, Repository
from jobagent.models import Job
from jobagent.utils.dates import utcnow

_SCHEMA = """
create table if not exists jobs (
  id text primary key, job_key text unique not null, company text not null, title text not null,
  url text, source text, location text, remote integer default 0, description text,
  requirements_json text, posted_at text, discovered_at text, status text not null default 'DISCOVERED',
  match_json text, match_reasons text, score integer, skip_reason text);
create table if not exists contacts (
  id text primary key, company text not null, email text not null, source_url text not null,
  confidence text not null, found_at text, verified_at text, unique(company, email));
create table if not exists applications (
  id text primary key, job_id text not null references jobs(id), company text, role text,
  status text not null default 'READY', route text not null, contact_id text, email_sent_at text,
  submitted_at text, application_url text, resume_version text, email_subject text, email_body text,
  notes text, created_at text, updated_at text, unique(job_id, route));
create table if not exists tasks (
  id text primary key, job_id text, type text not null, status text not null default 'queued',
  retry_count integer not null default 0, last_error text, payload text, created_at text, updated_at text);
create table if not exists events (
  id integer primary key autoincrement, ts text, level text, job_id text, action text, detail text);
create table if not exists telegram_state (key text primary key, value text);
create table if not exists daily_stats (
  day text primary key, scanned int default 0, qualified int default 0, selected int default 0,
  emails_sent int default 0, browser_submitted int default 0, waiting_user int default 0,
  failed int default 0, skipped int default 0, skip_reasons text default '{}');
create index if not exists idx_jobs_status on jobs(status);
create index if not exists idx_applications_job_id on applications(job_id);
create index if not exists idx_tasks_status on tasks(status);
"""

_JSON_COLS = {"requirements_json", "match_json", "payload", "detail", "skip_reasons", "match_reasons"}
_JOB_COLS = ["job_key", "company", "title", "url", "source", "location", "remote", "description",
             "requirements_json", "posted_at", "status", "match_json", "match_reasons", "score", "skip_reason"]


def _enc(k: str, v: Any) -> Any:
    if k in _JSON_COLS and v is not None and not isinstance(v, str):
        return json.dumps(v, default=str)
    if isinstance(v, bool):
        return int(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _dec(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in _JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    if "remote" in d and d["remote"] is not None:
        d["remote"] = bool(d["remote"])
    return d


class SqliteRepository(Repository):
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.executescript(_SCHEMA)

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        with self._lock:
            return _dec(self._db.execute(sql, args).fetchone())

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [_dec(r) for r in self._db.execute(sql, args).fetchall()]  # type: ignore[misc]

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    def _update(self, table: str, id_: Any, fields: dict, key: str = "id") -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"update {table} set {cols} where {key}=?", (*[_enc(k, v) for k, v in fields.items()], id_))

    # ---- jobs
    def upsert_job(self, job: Job) -> tuple[dict, bool]:
        existing = self.get_job_by_key(job.job_key)
        if existing:
            return existing, False
        jid = job.id or str(uuid.uuid4())
        d = job.model_dump()
        vals = [_enc(c, d.get(c)) for c in _JOB_COLS]
        with self._lock:
            self._db.execute(
                f"insert into jobs (id, discovered_at, {','.join(_JOB_COLS)}) values (?,?,{','.join('?' * len(_JOB_COLS))})",
                (jid, utcnow().isoformat(), *vals),
            )
            self._db.commit()
        return self.get_job(jid), True  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict | None:
        return self._one("select * from jobs where id=?", (job_id,))

    def get_job_by_key(self, job_key: str) -> dict | None:
        return self._one("select * from jobs where job_key=?", (job_key,))

    def update_job(self, job_id: str, **fields: Any) -> None:
        self._update("jobs", job_id, fields)

    def list_jobs(self, statuses: list[str] | None = None, limit: int = 200) -> list[dict]:
        if statuses:
            q = ",".join("?" * len(statuses))
            return self._all(f"select * from jobs where status in ({q}) order by discovered_at desc limit ?",
                             (*statuses, limit))
        return self._all("select * from jobs order by discovered_at desc limit ?", (limit,))

    # ---- contacts
    def save_contact(self, company: str, email: str, source_url: str, confidence: str) -> dict:
        row = self._one("select * from contacts where company=? and email=?", (company, email))
        if row:
            return row
        cid = str(uuid.uuid4())
        self._exec("insert into contacts (id, company, email, source_url, confidence, found_at) values (?,?,?,?,?,?)",
                   (cid, company, email, source_url, confidence, utcnow().isoformat()))
        return self.get_contact(cid)  # type: ignore[return-value]

    def get_contact(self, contact_id: str) -> dict | None:
        return self._one("select * from contacts where id=?", (contact_id,))

    # ---- applications
    def claim_application(self, job_id: str, route: str, company: str, role: str) -> tuple[dict, bool]:
        aid = str(uuid.uuid4())
        now = utcnow().isoformat()
        with self._lock:
            cur = self._db.execute(
                "insert or ignore into applications (id, job_id, company, role, status, route, created_at, updated_at)"
                " values (?,?,?,?,?,?,?,?)", (aid, job_id, company, role, "READY", route, now, now))
            self._db.commit()
            created = cur.rowcount == 1
        return self.get_application(job_id, route), created  # type: ignore[return-value]

    def get_application(self, job_id: str, route: str) -> dict | None:
        return self._one("select * from applications where job_id=? and route=?", (job_id, route))

    def update_application(self, app_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow().isoformat()
        self._update("applications", app_id, fields)

    def list_applications(self, limit: int = 20) -> list[dict]:
        return self._all("select * from applications order by created_at desc limit ?", (limit,))

    def count_applications_on(self, day: date) -> int:
        r = self._one("select count(*) c from applications where substr(created_at,1,10)=?", (day.isoformat(),))
        return int(r["c"]) if r else 0

    # ---- tasks
    def create_task(self, job_id: str | None, type_: str, payload: dict | None = None, status: str = "queued") -> dict:
        tid = str(uuid.uuid4())
        now = utcnow().isoformat()
        self._exec("insert into tasks (id, job_id, type, status, payload, created_at, updated_at) values (?,?,?,?,?,?,?)",
                   (tid, job_id, type_, status, _enc("payload", payload), now, now))
        return self.get_task(tid)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict | None:
        return self._one("select * from tasks where id=?", (task_id,))

    def update_task(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow().isoformat()
        self._update("tasks", task_id, fields)

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if status:
            return self._all("select * from tasks where status=? order by created_at desc limit ?", (status, limit))
        return self._all("select * from tasks order by created_at desc limit ?", (limit,))

    # ---- events / state / stats
    def add_event(self, level: str, action: str, job_id: str | None = None, detail: dict | None = None) -> None:
        self._exec("insert into events (ts, level, job_id, action, detail) values (?,?,?,?,?)",
                   (utcnow().isoformat(), level, job_id, action, _enc("detail", detail)))

    def list_events(self, limit: int = 50) -> list[dict]:
        return self._all("select * from events order by id desc limit ?", (limit,))

    def get_state(self, key: str, default: str | None = None) -> str | None:
        r = self._one("select value from telegram_state where key=?", (key,))
        return r["value"] if r else default

    def set_state(self, key: str, value: str) -> None:
        self._exec("insert into telegram_state (key, value) values (?,?) on conflict(key) do update set value=excluded.value",
                   (key, value))

    def _ensure_stat(self, day: date) -> None:
        self._exec("insert or ignore into daily_stats (day) values (?)", (day.isoformat(),))

    def bump_stat(self, day: date, field: str, n: int = 1) -> None:
        if field not in STAT_FIELDS:
            raise ValueError(field)
        self._ensure_stat(day)
        self._exec(f"update daily_stats set {field}={field}+? where day=?", (n, day.isoformat()))

    def add_skip_reason(self, day: date, reason: str) -> None:
        self._ensure_stat(day)
        with self._lock:
            r = self._one("select skip_reasons from daily_stats where day=?", (day.isoformat(),))
            d = (r or {}).get("skip_reasons") or {}
            d[reason] = d.get(reason, 0) + 1
            self._exec("update daily_stats set skip_reasons=? where day=?", (json.dumps(d), day.isoformat()))

    def get_stats(self, day: date) -> dict:
        self._ensure_stat(day)
        r = self._one("select * from daily_stats where day=?", (day.isoformat(),)) or {}
        r["skip_reasons"] = r.get("skip_reasons") or {}
        return r
