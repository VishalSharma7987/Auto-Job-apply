"""Contact finder: only *published* recruiting addresses, with source_url + confidence. No name-pattern guessing."""

from __future__ import annotations

import inspect
import re

import pytest
from conftest import make_job

from jobagent.contacts import finder as finder_mod
from jobagent.contacts.finder import ContactFinder, extract_recruiting_emails, is_recruiting_address
from jobagent.contacts.robots import RobotsChecker
from jobagent.utils.http import HttpClient

JOB_URL = "https://careers.acme.com/jobs/42"

JOB_PAGE_WITH_EMAIL = """<html><body><h1>AI Developer</h1><p>Send CVs to
<a href="mailto:Careers@acme.com?subject=AI%20Dev">careers@acme.com</a></p></body></html>"""
HOME = """<html><body><a href="/about">About</a><a href="/careers">Careers</a><a href="/contact">Contact</a>
<p>Our team: Jane Doe (CEO), John Smith (CTO)</p></body></html>"""
CAREERS = "<html><body><h2>Join us</h2><p>Write to hr@acme.com for openings.</p></body></html>"
CONTACT = "<html><body><p>Reach us at hello@acme.com or support@acme.com. Press: press@acme.com</p></body></html>"
TEAM_PAGE = """<html><body><p>John Doe - john.doe@acme.com</p><p>Jane Smith jane@acme.com</p>
<p>Mail hr.john@acme.com, careers.jane@acme.com, jdoe@acme.com</p></body></html>"""


class NoRobots(RobotsChecker):
    def __init__(self):
        pass

    def allowed(self, url: str) -> bool:
        return True


def finder_with(pages: dict[str, str]) -> tuple[ContactFinder, list[str]]:
    fetched: list[str] = []

    def fetch(url: str):
        fetched.append(url)
        return pages.get(url)

    return ContactFinder(HttpClient(min_interval=0), fetcher=fetch, robots=NoRobots()), fetched


def job(**kw):
    return make_job(url=JOB_URL, company="Acme", company_website="https://acme.com", **kw)


def test_high_confidence_on_job_page():
    f, fetched = finder_with({JOB_URL: JOB_PAGE_WITH_EMAIL})
    c = f.find(job())
    assert c and c.email == "careers@acme.com" and c.confidence == "HIGH" and c.source_url == JOB_URL
    assert fetched == [JOB_URL]  # stops early


def test_medium_confidence_on_careers_page():
    f, _ = finder_with({JOB_URL: "<html>nothing</html>", "https://acme.com": HOME,
                        "https://acme.com/careers": CAREERS, "https://acme.com/contact": CONTACT})
    c = f.find(job())
    assert c and c.email == "hr@acme.com" and c.confidence == "MEDIUM" and c.source_url == "https://acme.com/careers"


def test_generic_contact_page_is_never_used():
    f, _ = finder_with({JOB_URL: "<html></html>", "https://acme.com": HOME, "https://acme.com/contact": CONTACT})
    assert f.find(job()) is None  # hello@/support@/press@ are not recruiting addresses and contact page is LOW


def test_homepage_email_alone_is_low_confidence_and_unused():
    f, _ = finder_with({JOB_URL: "<html></html>", "https://acme.com": "<p>jobs@acme.com</p>"})
    assert f.find(job()) is None


def test_max_four_pages():
    pages = {JOB_URL: "<html></html>", "https://acme.com": HOME, "https://acme.com/careers": "<html></html>",
             "https://acme.com/contact": "<html></html>"}
    f, fetched = finder_with(pages)
    f.find(job())
    assert len(fetched) <= 4


def test_robots_blocked_pages_are_not_fetched():
    class Block(NoRobots):
        def allowed(self, url):
            return False

    fetched = []
    f = ContactFinder(HttpClient(min_interval=0), fetcher=lambda u: fetched.append(u) or JOB_PAGE_WITH_EMAIL,
                      robots=Block())
    assert f.find(job()) is None and fetched == []


def test_robots_txt_parsing_respects_disallow():
    class H(HttpClient):
        def get_text(self, url, **kw):
            return "User-agent: *\nDisallow: /private/" if url.endswith("/robots.txt") else None

    rc = RobotsChecker(H(min_interval=0))
    assert rc.allowed("https://acme.com/careers") and not rc.allowed("https://acme.com/private/x")


# ---------------------------------------------------------------- no name-pattern guessing
def test_personal_name_addresses_are_rejected_even_when_published():
    assert extract_recruiting_emails(TEAM_PAGE) == []


@pytest.mark.parametrize("addr", ["john.doe@acme.com", "jane@acme.com", "jdoe@acme.com", "hr.john@acme.com",
                                  "careers.jane@acme.com", "ceo@acme.com", "hello@acme.com", "support@acme.com"])
def test_non_recruiting_local_parts_rejected(addr):
    assert not is_recruiting_address(addr)


@pytest.mark.parametrize("addr", ["careers@acme.com", "jobs@acme.com", "hr@acme.com", "recruitment@acme.com",
                                  "hiring@acme.com", "talent@acme.com", "careers-india@acme.com", "talent.acquisition@acme.com"])
def test_recruiting_local_parts_accepted(addr):
    assert is_recruiting_address(addr)


def test_freemail_rejected():
    assert not is_recruiting_address("careers.acme@gmail.com") and not is_recruiting_address("hr@gmail.com")


def test_names_on_page_never_produce_an_address():
    """A page that names people but publishes no recruiting address must yield nothing - not a guessed one."""
    f, _ = finder_with({JOB_URL: "<html><p>Recruiter: Priya Nair. Hiring manager: Rahul Verma.</p></html>",
                        "https://acme.com": HOME, "https://acme.com/careers": "<p>Priya Nair leads talent</p>"})
    assert f.find(job()) is None


def test_every_returned_address_literally_appears_in_the_source_html():
    pages = {JOB_URL: JOB_PAGE_WITH_EMAIL, "https://acme.com": HOME, "https://acme.com/careers": CAREERS}
    for html in pages.values():
        for e in extract_recruiting_emails(html):
            assert e in html.lower()


def test_finder_source_has_no_address_construction():
    src = inspect.getsource(finder_mod)
    # the only place an '@' address can come from is regex extraction; nothing formats one from parts
    assert not re.search(r"f['\"][^'\"]*\{[^}]*\}@", src)
    assert not re.search(r"\+\s*['\"]@", src) and "format(" not in src.split("def _find_links")[0].split("EMAIL_RE")[0]
    forbidden = ["first_name", "last_name", "firstname", "lastname", "pattern_guess", "guess_email"]
    assert not any(w in src.lower() for w in forbidden)
