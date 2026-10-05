from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


class AshbyAdapter(SourceAdapter):
    """params: boards=[{slug, name?, website?}]"""

    name = "ashby"

    def fetch(self) -> list[RawJob]:
        out: list[RawJob] = []
        for b in self.params.get("boards", []):
            slug = b["slug"]
            try:
                data = self.http.get_json(API.format(org=slug))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("ashby %s failed: %s", slug, e)
                continue
            for j in data.get("jobs", []):
                if j.get("isListed") is False:
                    continue
                out.append(RawJob(
                    company=b.get("name") or slug, title=j.get("title", ""), url=j.get("jobUrl", ""),
                    location=j.get("location") or "", remote=j.get("isRemote"),
                    description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
                    source=self.name, posted_at=parse_dt(j.get("publishedAt")), company_website=b.get("website")))
        return out
