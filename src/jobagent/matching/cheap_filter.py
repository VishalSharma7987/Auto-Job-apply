"""Pure, deterministic pre-filter. No LLM, no network. Returns (ok, reason_code)."""

from __future__ import annotations

import re

from jobagent.models import Job

TITLE_ALLOW = [
    r"\bai\b", r"\bml\b", r"\bllm\b", r"machine learning", r"artificial intelligence", r"generative",
    r"\bgenai\b", r"agentic", r"\bagents?\b", r"prompt", r"full[\s-]?stack", r"software (engineer|developer)",
    r"\bdeveloper\b", r"python", r"nlp", r"data scientist", r"applied scientist", r"\brag\b",
]
TITLE_DENY = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b", r"\bmanager\b", r"\barchitect\b",
    r"\bdirector\b", r"\bhead of\b", r"\bvp\b", r"\bchief\b", r"\bintern(ship)?\b", r"\bsales\b", r"\bmarketing\b",
    r"\brecruit", r"\bdesigner\b", r"\baccount\b", r"\bsupport\b", r"\bcounsel\b", r"\bfinance\b", r"\bpartner",
]
YEARS_RE = re.compile(r"(\d{1,2})\s*(\+|(?:-|–|to)\s*\d{1,2}\s*\+?)?\s*(?:years|yrs)\b", re.I)
MAX_MIN_YEARS = 2  # a posting whose MINIMUM required experience exceeds this is dropped
INDIA_CITIES = ["india", "pune", "bangalore", "bengaluru", "hyderabad", "mumbai", "delhi", "gurgaon", "gurugram",
                "noida", "chennai", "remote - india", "anywhere"]
REMOTE_BAD = re.compile(r"\b(us only|usa only|u\.s\. only|must (?:reside|be located) in the (?:us|u\.s\.|united states|uk|eu)|"
                        r"eu only|uk only|canada only|emea only|north america only)\b", re.I)


def _title_ok(title: str) -> tuple[bool, str]:
    t = title.lower()
    for p in TITLE_DENY:
        if re.search(p, t):
            return False, "title_seniority_or_unrelated"
    if not any(re.search(p, t) for p in TITLE_ALLOW):
        return False, "title_not_relevant"
    return True, ""


def _years_too_high(text: str) -> bool:
    for m in YEARS_RE.finditer(text):
        if int(m.group(1)) <= MAX_MIN_YEARS:
            continue
        window = text[max(0, m.start() - 70): m.end() + 50].lower()
        if re.search(r"experience|exp|background|working|professional|industry|minimum|at least|required", window):
            return True
    return False


def location_ok(job: Job) -> bool:
    loc = f"{job.location}".lower()
    if job.remote:
        return not REMOTE_BAD.search(f"{job.location} {job.description[:1500]}")
    return any(c in loc for c in INDIA_CITIES)


def cheap_filter(job: Job) -> tuple[bool, str]:
    ok, reason = _title_ok(job.title)
    if not ok:
        return False, reason
    if _years_too_high(f"{job.title} {job.description}"):
        return False, "requires_3plus_years"
    if not location_ok(job):
        return False, "location_not_allowed"
    return True, ""
