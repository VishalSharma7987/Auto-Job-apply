from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


class GreenhouseAdapter(SourceAdapter):
    """params: boards=[{slug, name?, website?}]"""

    name = "greenhouse"

    def fetch(self) -> list[RawJob]:
        out: list[RawJob] = []
        for b in self.params.get("boards", []):
            slug = b["slug"]
            try:
                data = self.http.get_json(API.format(board=slug))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("greenhouse %s failed: %s", slug, e)
                continue
            for j in data.get("jobs", []):
                out.append(RawJob(
                    company=b.get("name") or slug, title=j.get("title", ""), url=j.get("absolute_url", ""),
                    location=(j.get("location") or {}).get("name", ""), description=j.get("content", ""),
                    source=self.name, posted_at=parse_dt(j.get("updated_at") or j.get("first_published")),
                    company_website=b.get("website")))
        return out
