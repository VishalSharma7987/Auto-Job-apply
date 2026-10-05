"""Generic adapter for company career pages listed in config/companies.yaml under `career_pages`.

Only reads public listing pages (robots.txt respected) and extracts JSON-LD JobPosting entries, which
many sites publish for SEO. No scraping of login-gated pages.
"""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup

from jobagent.contacts.robots import RobotsChecker
from jobagent.discovery.base import SourceAdapter
from jobagent.models import RawJob
from jobagent.utils.dates import parse_dt

log = logging.getLogger(__name__)


def _iter_jobposting(node):
    if isinstance(node, list):
        for n in node:
            yield from _iter_jobposting(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield node
        for v in node.values():
            if isinstance(v, (list, dict)):
                yield from _iter_jobposting(v)


class CareerPagesAdapter(SourceAdapter):
    """params: pages=[{name, url, website?}]"""

    name = "career_pages"

    def fetch(self) -> list[RawJob]:
        robots = RobotsChecker(self.http)
        out: list[RawJob] = []
        for p in self.params.get("pages", []):
            url = p["url"]
            if not robots.allowed(url):
                log.info("robots.txt disallows %s - skipped", url)
                continue
            html = self.http.get_text(url)
            if not html:
                continue
            for s in BeautifulSoup(html, "html.parser").find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(s.string or "")
                except ValueError:
                    continue
                for jp in _iter_jobposting(data):
                    loc = jp.get("jobLocation")
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
                    out.append(RawJob(
                        company=p.get("name", ""), title=jp.get("title", ""), url=jp.get("url") or url,
                        location=", ".join(x for x in [addr.get("addressLocality"), addr.get("addressCountry")]
                                           if isinstance(x, str)),
                        remote=jp.get("jobLocationType") == "TELECOMMUTE" or None,
                        description=jp.get("description", ""), source=self.name,
                        posted_at=parse_dt(jp.get("datePosted")), company_website=p.get("website")))
        return out
