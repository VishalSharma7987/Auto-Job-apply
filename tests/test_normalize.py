from __future__ import annotations

import httpx
import respx
from conftest import load_fixture

from jobagent.discovery.arbeitnow import ArbeitnowAdapter
from jobagent.discovery.ashby import AshbyAdapter
from jobagent.discovery.greenhouse import GreenhouseAdapter
from jobagent.discovery.jobicy import JobicyAdapter
from jobagent.discovery.lever import LeverAdapter
from jobagent.discovery.normalize import normalize
from jobagent.discovery.remoteok import RemoteOKAdapter
from jobagent.discovery.remotive import RemotiveAdapter
from jobagent.models import RawJob
from jobagent.utils.hashing import canonical_url, make_job_key
from jobagent.utils.http import HttpClient


def http() -> HttpClient:
    return HttpClient(min_interval=0, retries=0)


def test_normalize_basic_and_html_stripped():
    raw = RawJob(company=" Acme  AI ", title="AI Developer", url="https://x.io/jobs/1?utm_source=a",
                 location="Remote", description="<p>Build <b>RAG</b> apps</p><script>x()</script>", source="t")
    j = normalize(raw)
    assert j and j.company == "Acme AI"
    assert "RAG" in j.description and "<" not in j.description and "x()" not in j.description
    assert j.remote is True
    assert "utm_source" not in j.url


def test_normalize_rejects_incomplete():
    assert normalize(RawJob(company="", title="x", url="https://a.b", source="t")) is None
    assert normalize(RawJob(company="c", title="x", url="not-a-url", source="t")) is None


def test_job_key_is_stable_and_sha256():
    k1 = make_job_key("Acme AI", "AI Developer", "https://www.x.io/jobs/1/?utm_source=z#frag")
    k2 = make_job_key("acme  ai", "ai developer", "https://x.io/jobs/1")
    assert k1 == k2 and len(k1) == 64
    assert k1 != make_job_key("Acme AI", "AI Engineer", "https://x.io/jobs/1")
    assert canonical_url("https://X.io/a/?gh_jid=5&utm_medium=m") == "https://x.io/a?gh_jid=5"


@respx.mock
def test_greenhouse_adapter_real_shape():
    respx.get("https://boards-api.greenhouse.io/v1/boards/scaleai/jobs").mock(
        return_value=httpx.Response(200, json=load_fixture("greenhouse.json")))
    jobs = GreenhouseAdapter(http(), boards=[{"slug": "scaleai", "name": "Scale AI"}]).fetch()
    assert jobs and all(j.company == "Scale AI" and j.url.startswith("http") and j.title for j in jobs)
    n = normalize(jobs[0])
    assert n and n.description  # content (html-escaped in the API) was unescaped + stripped
    assert "&lt;" not in n.description


@respx.mock
def test_lever_adapter_real_shape():
    respx.get("https://api.lever.co/v0/postings/meesho").mock(
        return_value=httpx.Response(200, json=load_fixture("lever.json")))
    jobs = LeverAdapter(http(), boards=[{"slug": "meesho", "name": "Meesho"}]).fetch()
    assert jobs and jobs[0].url.startswith("https://jobs.lever.co/")


@respx.mock
def test_ashby_adapter_real_shape():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/supabase").mock(
        return_value=httpx.Response(200, json=load_fixture("ashby.json")))
    jobs = AshbyAdapter(http(), boards=[{"slug": "supabase", "name": "Supabase"}]).fetch()
    assert jobs and jobs[0].url.startswith("https://jobs.ashbyhq.com/")


@respx.mock
def test_remotive_adapter_real_shape():
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json=load_fixture("remotive.json")))
    jobs = RemotiveAdapter(http(), searches=["python"]).fetch()
    assert jobs and jobs[0].remote is True and jobs[0].company


@respx.mock
def test_remoteok_skips_legal_notice_and_keeps_source_url():
    respx.get("https://remoteok.com/api").mock(return_value=httpx.Response(200, json=load_fixture("remoteok.json")))
    jobs = RemoteOKAdapter(http()).fetch()
    assert all("remoteok.com" in j.url for j in jobs)  # attribution: original remoteok link preserved


@respx.mock
def test_arbeitnow_adapter_real_shape():
    respx.get("https://www.arbeitnow.com/api/job-board-api").mock(
        return_value=httpx.Response(200, json=load_fixture("arbeitnow.json")))
    jobs = ArbeitnowAdapter(http()).fetch()
    assert jobs and jobs[0].url.startswith("http")


@respx.mock
def test_jobicy_adapter_real_shape():
    respx.get("https://jobicy.com/api/v2/remote-jobs").mock(
        return_value=httpx.Response(200, json=load_fixture("jobicy.json")))
    jobs = JobicyAdapter(http(), tags=["python"]).fetch()
    assert jobs and jobs[0].company and jobs[0].url.startswith("http")


@respx.mock
def test_adapter_survives_http_errors():
    respx.get("https://boards-api.greenhouse.io/v1/boards/dead/jobs").mock(return_value=httpx.Response(404))
    assert GreenhouseAdapter(http(), boards=[{"slug": "dead"}]).fetch() == []
