from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowAdapter(SourceAdapter):
    name = "arbeitnow"

    def fetch(self) -> list[RawJob]:
        try:
            data = self.http.get_json(API)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("arbeitnow failed: %s", e)
            return []
        return [
            RawJob(company=j.get("company_name", ""), title=j.get("title", ""), url=j.get("url", ""),
                   location=j.get("location", ""), remote=j.get("remote"), description=j.get("description", ""),
                   source=self.name, posted_at=parse_dt(j.get("created_at")))
            for j in data.get("data", [])
        ]
