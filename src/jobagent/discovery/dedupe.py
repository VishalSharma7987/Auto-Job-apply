from __future__ import annotations

from jobagent.models import Job
from jobagent.utils.hashing import normalize_text


def dedupe(jobs: list[Job]) -> list[Job]:
    """Drop duplicates inside a batch: same job_key, or same company+title posted on several boards."""
    seen_keys: set[str] = set()
    seen_ct: set[tuple[str, str, str]] = set()
    out: list[Job] = []
    for j in jobs:
        ct = (normalize_text(j.company), normalize_text(j.title), normalize_text(j.location))
        if j.job_key in seen_keys or ct in seen_ct:
            continue
        seen_keys.add(j.job_key)
        seen_ct.add(ct)
        out.append(j)
    return out
