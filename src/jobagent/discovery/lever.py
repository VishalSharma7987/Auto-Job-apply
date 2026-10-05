from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://api.lever.co/v0/postings/{company}?mode=json"


class LeverAdapter(SourceAdapter):
    """params: boards=[{slug, name?, website?}]"""

    name = "lever"

    def fetch(self) -> list[RawJob]:
        out: list[RawJob] = []
        for b in self.params.get("boards", []):
            slug = b["slug"]
            try:
                data = self.http.get_json(API.format(company=slug))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("lever %s failed: %s", slug, e)
                continue
            if not isinstance(data, list):
                continue
            for j in data:
                cats = j.get("categories") or {}
                loc = cats.get("location") or ""
                out.append(RawJob(
                    company=b.get("name") or slug, title=j.get("text", ""), url=j.get("hostedUrl", ""),
                    location=loc, remote=(j.get("workplaceType") == "remote") or None,
                    description=j.get("descriptionPlain") or j.get("description", ""),
                    source=self.name, posted_at=parse_dt(j.get("createdAt")), company_website=b.get("website")))
        return out
