from __future__ import annotations

import pytest
from conftest import make_job

from jobagent.matching.cheap_filter import cheap_filter


@pytest.mark.parametrize("title", ["AI Developer", "Junior AI/ML Engineer", "Agentic AI Developer",
                                   "Full Stack Developer", "Generative AI Engineer", "Prompt Engineer"])
def test_relevant_titles_pass(title):
    assert cheap_filter(make_job(title=title))[0]


@pytest.mark.parametrize("title", ["Senior ML Engineer", "Staff AI Engineer", "Principal AI Architect", "Lead Developer",
                                   "Engineering Manager, AI", "Director of AI", "Sales Executive", "Product Designer"])
def test_senior_or_unrelated_titles_dropped(title):
    ok, reason = cheap_filter(make_job(title=title))
    assert not ok and reason in ("title_seniority_or_unrelated", "title_not_relevant")


@pytest.mark.parametrize("desc", ["Requires 5+ years of experience in ML", "7+ years experience with Python",
                                  "Minimum 3 years of professional experience", "at least 4 years experience"])
def test_experience_requirements_dropped(desc):
    ok, reason = cheap_filter(make_job(description=desc))
    assert not ok and reason == "requires_3plus_years"


@pytest.mark.parametrize("desc", ["0-2 years of experience", "0-1 years", "1-3 years experience", "freshers welcome",
                                  "We are 10 years old and growing", "2+ years experience"])
def test_junior_ranges_pass(desc):
    assert cheap_filter(make_job(description=desc))[0]


def test_location_rules():
    assert cheap_filter(make_job(location="Pune, India", remote=False))[0]
    assert cheap_filter(make_job(location="Bengaluru", remote=False))[0]
    assert cheap_filter(make_job(location="Remote", remote=True))[0]
    ok, reason = cheap_filter(make_job(location="Berlin, Germany", remote=False))
    assert not ok and reason == "location_not_allowed"
    ok, reason = cheap_filter(make_job(location="Remote (US only)", remote=True))
    assert not ok and reason == "location_not_allowed"
