"""Find *published* recruiting emails only.

Safety properties (covered by tests/test_contact_finder.py):
  * an address is returned only if it literally appears in fetched HTML/text (mailto: or plain text);
  * its local part must match a recruiting role allow-list (careers, jobs, hr, recruitment, hiring, talent ...)
    optionally followed by a region suffix - personal-name addresses never match;
  * there is NO code path that builds an address from a person's name or a domain pattern;
  * robots.txt is honoured, max 4 pages are fetched.
Confidence: HIGH = on the job posting page, MEDIUM = on a careers/jobs page, LOW = elsewhere (never used).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from jobagent.contacts.robots import RobotsChecker
from jobagent.models import Contact, Job
from jobagent.utils.http import HttpClient

log = logging.getLogger(__name__)

MAX_PAGES = 4
ROLES = (r"careers?|jobs?|hr|hiring|recruit(?:ment|ing|er|ers|ing-team)?|talent(?:[-_.]?acquisition)?|"
         r"humanresources|human[-_.]resources|join(?:us)?|applications?|work(?:with)?us|staffing")
REGION = r"india|in|global|us|uk|eu|apac|team|pune|bangalore|bengaluru|hyderabad|remote|early[-_.]?careers?|campus"
LOCAL_RE = re.compile(rf"^(?:{ROLES})(?:[-_.+](?:{REGION}))?$", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
FREEMAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "protonmail.com", "icloud.com",
            "rediffmail.com", "live.com", "aol.com"}
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com")


def is_recruiting_address(email: str) -> bool:
    local, _, domain = email.lower().rpartition("@")
    if not local or domain in FREEMAIL or "." not in domain:
        return False
    if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", domain):
        return False
    return bool(LOCAL_RE.match(local))


def extract_recruiting_emails(html: str) -> list[str]:
    """Emails published in the page (mailto: or visible text) that pass the recruiting allow-list."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if EMAIL_RE.fullmatch(addr):
                found.append(addr)
    for script in soup(["script", "style"]):
        script.decompose()
    found += EMAIL_RE.findall(soup.get_text(" "))
    out: list[str] = []
    for e in found:
        e = e.strip(".,;:()<>[]\"'").lower()
        if e not in out and is_recruiting_address(e):
            out.append(e)
    return out


def _page_kind(url: str, job_url: str) -> str:
    if url == job_url:
        return "job"
    path = urlparse(url).path.lower()
    return "careers" if re.search(r"career|jobs|join|hiring|work-with|opportunit", path) else "other"


def _confidence(kind: str) -> str:
    return {"job": "HIGH", "careers": "MEDIUM"}.get(kind, "LOW")


def _find_links(html: str, base: str) -> dict[str, str]:
    """Same-site careers/contact links on the homepage."""
    links: dict[str, str] = {}
    host = urlparse(base).netloc.removeprefix("www.")
    for a in BeautifulSoup(html or "", "html.parser").find_all("a", href=True):
        full = urljoin(base, a["href"])
        p = urlparse(full)
        if p.scheme not in ("http", "https") or p.netloc.removeprefix("www.") != host:
            continue
        hay = f"{p.path} {a.get_text(' ')}".lower()
        if "careers" not in links and re.search(r"career|jobs|join us|hiring|work with us", hay):
            links["careers"] = full.split("#")[0]
        elif "contact" not in links and re.search(r"contact", hay):
            links["contact"] = full.split("#")[0]
    return links


class ContactFinder:
    def __init__(self, http: HttpClient | None = None, fetcher: Callable[[str], str | None] | None = None,
                 robots: RobotsChecker | None = None):
        self.http = http or HttpClient()
        self.robots = robots or RobotsChecker(self.http)
        self._fetch = fetcher or self.http.get_text

    def _get(self, url: str) -> str | None:
        if not self.robots.allowed(url):
            log.info("robots.txt blocks %s", url)
            return None
        return self._fetch(url)

    def find(self, job: Job) -> Contact | None:
        """Best usable (HIGH/MEDIUM) published recruiting contact for the job, or None."""
        best: Contact | None = None
        visited: list[str] = []

        def visit(url: str) -> str | None:
            nonlocal best
            if url in visited or len(visited) >= MAX_PAGES:
                return None
            visited.append(url)
            html = self._get(url)
            if not html:
                return None
            conf = _confidence(_page_kind(url, job.url))
            for email in extract_recruiting_emails(html):
                if conf in ("HIGH", "MEDIUM") and (best is None or (conf == "HIGH" and best.confidence != "HIGH")):
                    best = Contact(company=job.company, email=email, source_url=url, confidence=conf)  # type: ignore[arg-type]
            return html

        visit(job.url)
        if best and best.confidence == "HIGH":
            return best
        site = job.company_website
        if site and not any(h in urlparse(site).netloc for h in ATS_HOSTS):
            home = visit(site)
            if home and not best:
                for url in _find_links(home, site).values():
                    visit(url)
                    if best:
                        break
        return best
