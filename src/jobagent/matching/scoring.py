"""Deterministic, explainable priority score (0-100). Higher = apply first."""

from __future__ import annotations

from jobagent.models import Job, MatchResult

TARGET_TITLES = ["agentic", "ai developer", "ai engineer", "ml developer", "ml engineer", "full stack ai",
                 "full-stack ai", "generative", "llm", "full stack developer", "fullstack developer"]
INDIA_HUBS = ["pune", "bangalore", "bengaluru", "hyderabad"]


def score_job(job: Job, m: MatchResult) -> tuple[int, list[str]]:
    pts: list[tuple[int, str]] = []
    n_matched, n_must = len(m.matched_skills), max(len(m.must_have_skills), 1)
    pts.append((min(40, round(40 * n_matched / n_must)), f"skill overlap {n_matched}/{n_must}"))
    t = job.title.lower()
    if any(k in t for k in TARGET_TITLES):
        pts.append((20, "title is a target role"))
    ymin = m.required_years_min
    if ymin is None:
        pts.append((8, "years not stated"))
    elif ymin <= 1:
        pts.append((15, "0-1 yrs required"))
    elif ymin <= 2:
        pts.append((8, "up to 2 yrs required"))
    loc = job.location.lower()
    if any(h in loc for h in INDIA_HUBS):
        pts.append((15, "target India hub"))
    elif job.remote:
        pts.append((12, "remote"))
    pts.append((round(10 * max(0.0, min(1.0, m.confidence))), "model confidence"))
    if job.posted_at is not None:
        pts.append((0, "dated posting"))
    total = max(0, min(100, sum(p for p, _ in pts)))
    return total, [f"{p:+d} {why}" for p, why in pts if p]
