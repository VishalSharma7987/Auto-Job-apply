from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://jobicy.com/api/v2/remote-jobs"


class JobicyAdapter(SourceAdapter):
    """params: tags=[str]"""

    name = "jobicy"

    def fetch(self) -> list[RawJob]:
        out: list[RawJob] = []
        for tag in self.params.get("tags", ["python"]):
            try:
                data = self.http.get_json(API, params={"count": 50, "tag": tag})
            except (httpx.HTTPError, ValueError) as e:
                log.warning("jobicy %r failed: %s", tag, e)
                continue
            for j in data.get("jobs", []):
                out.append(RawJob(
                    company=j.get("companyName", ""), title=j.get("jobTitle", ""), url=j.get("url", ""),
                    location=j.get("jobGeo", ""), remote=True,
                    description=j.get("jobDescription") or j.get("jobExcerpt", ""), source=self.name,
                    posted_at=parse_dt(j.get("pubDate"))))
        return out
