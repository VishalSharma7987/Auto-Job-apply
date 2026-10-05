"""Job status machine + shared pipeline state types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

DISCOVERED = "DISCOVERED"
QUALIFIED = "QUALIFIED"
CONTACT_FOUND = "CONTACT_FOUND"
READY = "READY"
EMAIL_SENT = "EMAIL_SENT"
APPLICATION_STARTED = "APPLICATION_STARTED"
SUBMITTED = "SUBMITTED"
WAITING_USER = "WAITING_USER"
FAILED = "FAILED"
REJECTED = "REJECTED"
INTERVIEW = "INTERVIEW"

ALL = [DISCOVERED, QUALIFIED, CONTACT_FOUND, READY, EMAIL_SENT, APPLICATION_STARTED, SUBMITTED,
       WAITING_USER, FAILED, REJECTED, INTERVIEW]

# statuses a later run may pick up again
RESUMABLE = [DISCOVERED, QUALIFIED, CONTACT_FOUND, READY]
TERMINAL = {EMAIL_SENT, SUBMITTED, REJECTED, INTERVIEW}

ALLOWED: dict[str, set[str]] = {
    DISCOVERED: {QUALIFIED, REJECTED, FAILED},
    QUALIFIED: {CONTACT_FOUND, READY, REJECTED, WAITING_USER, FAILED},
    CONTACT_FOUND: {READY, REJECTED, FAILED},
    READY: {EMAIL_SENT, APPLICATION_STARTED, WAITING_USER, FAILED, REJECTED},
    APPLICATION_STARTED: {SUBMITTED, WAITING_USER, FAILED, READY},
    WAITING_USER: {READY, REJECTED, FAILED, APPLICATION_STARTED},
    FAILED: {READY, QUALIFIED, DISCOVERED, REJECTED},
    EMAIL_SENT: {INTERVIEW, REJECTED},
    SUBMITTED: {INTERVIEW, REJECTED},
    REJECTED: set(),
    INTERVIEW: set(),
}


class IllegalTransition(Exception):
    pass


def transition(repo, job_id: str, new: str, **fields) -> None:
    """Guarded status change. EMAIL_SENT additionally requires an email application row (never re-sent)."""
    job = repo.get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    cur = job["status"]
    if cur == new:
        if fields:
            repo.update_job(job_id, **fields)
        return
    if new not in ALLOWED.get(cur, set()):
        raise IllegalTransition(f"{cur} -> {new}")
    if new == EMAIL_SENT:
        app = repo.get_application(job_id, "email")
        if app is None or not app.get("email_sent_at"):
            raise IllegalTransition("EMAIL_SENT requires a recorded email application with email_sent_at")
    if new == SUBMITTED:
        app = repo.get_application(job_id, "browser")
        if app is None or not app.get("submitted_at"):
            raise IllegalTransition("SUBMITTED requires a recorded browser application with submitted_at")
    repo.update_job(job_id, status=new, **fields)


class PipelineState(TypedDict, total=False):
    raw: list
    jobs: list  # list[Job] new this run (with .id set after upsert)
    candidates: list  # job ids that passed cheap filter and still need AI match
    qualified: list  # job ids (ranked)
    selected: list  # job ids chosen to act on today
    quota_limited: bool
    quota_detail: str
    acted: list


@dataclass
class RunSummary:
    scanned: int = 0
    qualified: int = 0
    selected: int = 0
    emails_sent: int = 0
    browser_submitted: int = 0
    waiting_user: int = 0
    failed: int = 0
    skipped: int = 0
    quota_limited: bool = False
    quota_detail: str = ""
    selected_jobs: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
