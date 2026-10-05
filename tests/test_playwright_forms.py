"""Drives the real Playwright runner against local HTML fixtures shaped like Greenhouse/Lever/Ashby forms.
Skipped automatically if Chromium is not installed (`playwright install chromium`)."""

from __future__ import annotations

import pytest

from jobagent.apply import playwright_runner as pr
from jobagent.apply.strategies import pick_strategy
from jobagent.apply.strategies.ashby import STRATEGY as ASHBY
from jobagent.apply.strategies.greenhouse import STRATEGY as GH
from jobagent.apply.strategies.lever import STRATEGY as LEVER

sync_api = pytest.importorskip("playwright.sync_api")

GH_FORM = """<html><body><form id="application_form" onsubmit="document.body.innerHTML='<h1>Thank you for applying</h1>';return false">
<div class="field"><label for="first_name">First Name *</label><input id="first_name" name="first_name" required></div>
<div class="field"><label for="last_name">Last Name *</label><input id="last_name" name="last_name" required></div>
<div class="field"><label for="email">Email *</label><input id="email" name="email" type="email" required></div>
<div class="field"><label for="phone">Phone</label><input id="phone" name="phone"></div>
<div class="field"><label for="resume">Resume/CV *</label><input id="resume" type="file" required></div>
<div class="field"><label for="cl">Cover Letter</label><textarea id="cl" name="cover_letter"></textarea></div>
<div class="field"><label for="li">LinkedIn Profile</label><input id="li" name="li"></div>
{extra}
<button id="submit_app" type="submit">Submit Application</button></form></body></html>"""

SPONSOR = '<div class="field"><label for="sp">Will you require visa sponsorship? *</label><select id="sp" required><option>Yes</option><option>No</option></select></div>'
CAPTCHA = '<iframe src="https://www.google.com/recaptcha/api2/bframe" title="recaptcha"></iframe>'


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium not installed: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    p = ctx.new_page()
    yield p
    ctx.close()


def job():
    return {"job_key": "k" * 64, "title": "AI Developer", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1"}


@pytest.fixture
def resume_settings(settings, resume):
    return settings  # resume_path points at the tmp resume created by the fixture


def test_strategies_picked_by_url_and_urls_rewritten():
    assert pick_strategy("https://boards.greenhouse.io/a/jobs/1") is GH
    assert pick_strategy("https://jobs.lever.co/a/123") is LEVER and LEVER.apply_url("https://jobs.lever.co/a/123") == "https://jobs.lever.co/a/123/apply"
    assert pick_strategy("https://jobs.ashbyhq.com/a/123") is ASHBY and ASHBY.apply_url("https://jobs.ashbyhq.com/a/123").endswith("/application")
    assert pick_strategy("https://careers.random.com/job/1") is None  # generic disabled by default
    assert pick_strategy("https://careers.random.com/job/1", allow_generic=True).experimental


def test_dry_run_fills_form_and_stops_before_submit(page, profile, resume_settings, resume):
    page.set_content(GH_FORM.format(extra=""))
    out = pr.run_form(page, GH, job(), "My cover letter", profile, resume_settings, None, {})
    assert out.status == pr.DRY_RUN_STOPPED and out.screenshot
    assert page.input_value("#first_name") == "Vishal" and page.input_value("#last_name") == "Sharma"
    assert page.input_value("#email") == "candidate@example.com" and page.input_value("#cl") == "My cover letter"
    assert page.locator("#resume").evaluate("e => e.files.length") == 1
    assert "Thank you" not in page.inner_text("body")  # not submitted


def test_live_submit_detects_confirmation(page, profile, settings, resume):
    live = settings.model_copy(update={"dry_run": False})
    page.set_content(GH_FORM.format(extra=""))
    out = pr.run_form(page, GH, job(), "cl", profile, live, None, {})
    assert out.status == pr.SUBMITTED


def test_consequential_required_question_waits_for_user(page, profile, settings, resume):
    live = settings.model_copy(update={"dry_run": False})
    page.set_content(GH_FORM.format(extra=SPONSOR))
    out = pr.run_form(page, GH, job(), "cl", profile, live, None, {})
    assert out.status == pr.WAITING_USER and "sponsor" in out.reason.lower()
    assert "Thank you" not in page.inner_text("body") and page.input_value("#first_name") == ""  # nothing was filled/submitted


def test_captcha_stops_safely(page, profile, settings, resume):
    live = settings.model_copy(update={"dry_run": False})
    page.set_content(GH_FORM.format(extra=CAPTCHA))
    out = pr.run_form(page, GH, job(), "cl", profile, live, None, {})
    assert out.status == pr.WAITING_USER and "captcha" in out.reason.lower() and out.screenshot


def test_approved_answer_lets_run_continue(page, profile, settings, resume):
    page.set_content(GH_FORM.format(extra=SPONSOR))
    out = pr.run_form(page, GH, job(), "cl", profile, settings, None, {"Will you require visa sponsorship? *": "No"})
    assert out.status == pr.DRY_RUN_STOPPED


def test_missing_form_fails_with_artifacts(page, profile, settings, resume):
    page.set_content("<html><body><p>Job closed</p></body></html>")
    out = pr.run_form(page, GH, job(), "cl", profile, settings, None, {})
    assert out.status == pr.FAILED and out.screenshot


def test_unsupported_site_asks_user_instead_of_guessing(profile, settings):
    out = pr.apply_to_job({**job(), "url": "https://careers.random.com/jobs/1"}, "cl", profile, settings, None)
    assert out.status == pr.WAITING_USER and "no automated strategy" in out.reason
