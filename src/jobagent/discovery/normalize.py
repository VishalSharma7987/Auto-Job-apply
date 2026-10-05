from __future__ import annotations

import re

from jobagent.llm.sanitize import sanitize
from jobagent.models import Job, RawJob
from jobagent.utils.hashing import canonical_url, make_job_key

_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed)\b", re.I)


def _clean(s: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def normalize(raw: RawJob) -> Job | None:
    """RawJob -> Job. Returns None for records without a usable company/title/url."""
    company, title, url = _clean(raw.company, 120), _clean(raw.title, 200), (raw.url or "").strip()
    if not (company and title and url.startswith("http")):
        return None
    location = _clean(raw.location, 200)
    desc = sanitize(raw.description, 12000)
    remote = raw.remote if raw.remote is not None else bool(_REMOTE_RE.search(f"{location} {title}"))
    if remote is False and _REMOTE_RE.search(location):
        remote = True
    return Job(
        job_key=make_job_key(company, title, url), company=company, title=title, url=canonical_url(url) or url,
        source=raw.source, location=location, remote=bool(remote), description=desc, posted_at=raw.posted_at,
        company_website=raw.company_website)
