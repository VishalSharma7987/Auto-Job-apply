from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://remotive.com/api/remote-jobs"


class RemotiveAdapter(SourceAdapter):
    """params: searches=[str] (Remotive asks for few calls/day - keep the list short)"""

    name = "remotive"

    def fetch(self) -> list[RawJob]:
        out: list[RawJob] = []
        for q in self.params.get("searches", ["ai engineer"]):
            try:
                data = self.http.get_json(API, params={"search": q, "limit": 50})
            except (httpx.HTTPError, ValueError) as e:
                log.warning("remotive %r failed: %s", q, e)
                continue
            for j in data.get("jobs", []):
                out.append(RawJob(
                    company=j.get("company_name", ""), title=j.get("title", ""), url=j.get("url", ""),
                    location=j.get("candidate_required_location", ""), remote=True,
                    description=j.get("description", ""), source=self.name,
                    posted_at=parse_dt(j.get("publication_date"))))
        return out
