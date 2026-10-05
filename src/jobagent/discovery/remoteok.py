from __future__ import annotations

import logging

import httpx

from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)
API = "https://remoteok.com/api"
# RemoteOK terms: identify yourself with a UA and link back to remoteok.com as the source (we keep the
# original remoteok.com job URL on every record and never strip it).
WANTED_TAGS = {"ai", "ml", "machine learning", "python", "llm", "full stack", "fullstack", "developer", "dev"}


class RemoteOKAdapter(SourceAdapter):
    name = "remoteok"

    def fetch(self) -> list[RawJob]:
        try:
            data = self.http.get_json(API, headers={"Accept": "application/json"})
        except (httpx.HTTPError, ValueError) as e:
            log.warning("remoteok failed: %s", e)
            return []
        out: list[RawJob] = []
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or "position" not in j:  # first element is the legal notice
                continue
            tags = {str(t).lower() for t in j.get("tags") or []}
            if tags and not (tags & WANTED_TAGS):
                continue
            out.append(RawJob(
                company=j.get("company", ""), title=j.get("position", ""), url=j.get("url", ""),
                location=j.get("location") or "Remote", remote=True, description=j.get("description", ""),
                source=self.name, posted_at=parse_dt(j.get("date") or j.get("epoch"))))
        return out
