from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RawJob(BaseModel):
    company: str
    title: str
    url: str
    location: str = ""
    remote: bool | None = None
    description: str = ""  # html or text
    source: str
    posted_at: datetime | None = None
    company_website: str | None = None


class Job(BaseModel):
    id: str | None = None
    job_key: str
    company: str
    title: str
    url: str
    source: str
    location: str = ""
    remote: bool = False
    description: str = ""
    requirements_json: dict | None = None
    posted_at: datetime | None = None
    status: str = "DISCOVERED"
    match_json: dict | None = None
    match_reasons: list[str] = Field(default_factory=list)
    score: int | None = None
    skip_reason: str | None = None
    company_website: str | None = None  # transient, not stored


class Contact(BaseModel):
    id: str | None = None
    company: str
    email: str
    source_url: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]


class Application(BaseModel):
    id: str | None = None
    job_id: str
    company: str = ""
    role: str = ""
    status: str = "READY"
    route: Literal["email", "browser"]
    contact_id: str | None = None
    email_subject: str | None = None
    email_body: str | None = None
    notes: str | None = None


class Task(BaseModel):
    id: str | None = None
    job_id: str | None = None
    type: str
    status: Literal["queued", "running", "waiting_user", "completed", "failed"] = "queued"
    retry_count: int = 0
    last_error: str | None = None
    payload: dict | None = None


class Event(BaseModel):
    level: str = "info"
    job_id: str | None = None
    action: str
    detail: dict | None = None


class MatchResult(BaseModel):
    decision: Literal["QUALIFIED", "REJECTED"]
    required_years_min: float | None = None
    required_years_max: float | None = None
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    seniority: str = "unknown"
    location_ok: bool = True
    reasons: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class EmailDraft(BaseModel):
    subject: str
    body: str


class FormAnswer(BaseModel):
    answer: str
    confidence: float = 0.0
    consequential: bool = False
