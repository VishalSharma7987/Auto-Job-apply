"""Headless Chromium application runner (Greenhouse / Lever / Ashby hosted forms).

Hard rules: no CAPTCHA/OTP/login/anti-bot bypass, robots.txt honoured, DRY_RUN stops before submit,
consequential questions never guessed. Failure => screenshot + html in artifacts/{job_key}/.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from jobagent.apply.detectors import detect_all, detect_captcha
from jobagent.apply.field_mapper import Decision, FieldSpec, decide
from jobagent.apply.strategies import Strategy, pick_strategy
from jobagent.config import Settings
from jobagent.contacts.robots import RobotsChecker
from jobagent.llm.client import LLM
from jobagent.profile import Profile, resume_path

log = logging.getLogger(__name__)

SUBMITTED = "SUBMITTED"
DRY_RUN_STOPPED = "DRY_RUN_STOPPED"
WAITING_USER = "WAITING_USER"
FAILED = "FAILED"

COLLECT_JS = """
() => {
  const out = [];
  const seenRadio = new Set();
  const labelFor = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    if (!t && el.getAttribute('aria-labelledby')) {
      const n = document.getElementById(el.getAttribute('aria-labelledby')); if (n) t = n.innerText;
    }
    if (!t) {
      const wrap = el.closest('.field, .application-field, .application-question, [class*=field], [class*=question], fieldset, li, div');
      if (wrap) { const l = wrap.querySelector('label, legend'); if (l) t = l.innerText; }
    }
    if (!t) t = el.getAttribute('placeholder') || el.name || '';
    return t.replace(/\\s+/g, ' ').trim();
  };
  document.querySelectorAll('input, textarea, select').forEach((el, i) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (type === 'hidden' || el.disabled || el.getAttribute('aria-hidden') === 'true') return;
    if (['submit', 'button', 'image', 'reset', 'search'].includes(type)) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0) && !['file', 'checkbox', 'radio'].includes(type)) return;
    el.setAttribute('data-ja-idx', String(i));
    const label = labelFor(el);
    let options = [];
    let tag = el.tagName.toLowerCase();
    if (tag === 'select') options = Array.from(el.options).map(o => o.text.trim()).filter(t => t);
    if (type === 'radio') {
      if (seenRadio.has(el.name)) return; seenRadio.add(el.name);
      const group = Array.from(document.querySelectorAll('input[type=radio]')).filter(x => x.name === el.name);
      options = group.map(x => (x.labels && x.labels[0] ? x.labels[0].innerText : x.value).trim());
      const legend = el.closest('fieldset'); const lg = legend && legend.querySelector('legend');
      if (lg) { /* group question text */ }
    }
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\\*|required/i.test(label);
    out.push({idx: i, tag, type, label, required, options, name: el.name || ''});
  });
  return out;
}
"""


@dataclass
class ApplyOutcome:
    status: str  # SUBMITTED | DRY_RUN_STOPPED | WAITING_USER | FAILED
    reason: str = ""
    screenshot: str | None = None
    questions: list[dict] = field(default_factory=list)
    application_url: str = ""


def _artifact_dir(settings: Settings, job_key: str) -> Path:
    d = settings.abs_path(settings.artifacts_dir) / job_key[:16]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snap(page, settings: Settings, job_key: str, name: str) -> str | None:
    try:
        p = _artifact_dir(settings, job_key) / f"{name}.png"
        page.screenshot(path=str(p), full_page=True)
        return str(p)
    except Exception as e:  # noqa: BLE001
        log.warning("screenshot failed: %s", e)
        return None


def _save_failure(page, settings: Settings, job_key: str) -> str | None:
    shot = _snap(page, settings, job_key, "failure")
    try:
        (_artifact_dir(settings, job_key) / "failure.html").write_text(page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return shot


def _fill_field(page, f: FieldSpec, d: Decision, resume: Path) -> None:
    loc = page.locator(f'[data-ja-idx="{f.selector}"]').first
    if d.action == "upload":
        loc.set_input_files(str(resume))
    elif d.action == "fill":
        loc.fill(d.value)
    elif d.action == "select":
        if f.type == "radio":
            group = page.locator(f'input[type=radio][name="{f.name}"]')
            for i in range(group.count()):
                el = group.nth(i)
                lab = el.evaluate("x => (x.labels && x.labels[0] ? x.labels[0].innerText : x.value).trim()")
                if lab == d.value:
                    el.check()
                    return
        else:
            loc.select_option(label=d.value)
    elif d.action == "check":
        loc.check()


def run_form(page, strategy: Strategy, job: dict, cover_letter: str, profile: Profile, settings: Settings,
             llm: LLM | None, approved: dict[str, str]) -> ApplyOutcome:
    """Everything after navigation. Separated so tests can drive it on a local fixture page."""
    key = job["job_key"]
    url = page.url
    for det in detect_all(page.content(), url):
        shot = _snap(page, settings, key, f"blocked_{det.kind}")
        return ApplyOutcome(WAITING_USER, f"{det.kind} detected: {det.detail}", shot, application_url=url)

    for text in strategy.apply_button_texts:
        if page.locator("input[type=file]").count() == 0:
            btn = page.get_by_role("link", name=re.compile(rf"^{re.escape(text)}$", re.I))
            if btn.count() == 0:
                btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(text)}$", re.I))
            if btn.count():
                btn.first.click()
                page.wait_for_load_state("domcontentloaded")
                break

    raw = page.evaluate(COLLECT_JS)
    if not raw:
        shot = _snap(page, settings, key, "no_form")
        return ApplyOutcome(FAILED, "application form not found", shot, application_url=url)

    resume = resume_path(settings)
    plan: list[tuple[FieldSpec, Decision]] = []
    questions: list[dict] = []
    for r in raw:
        f = FieldSpec(selector=str(r["idx"]), tag=r["tag"], type=r["type"], label=r["label"],
                      required=bool(r["required"]), options=r["options"], name=r["name"])
        d = decide(f, profile, cover_letter, llm, approved, resume_available=resume.exists())
        if d.action == "ask_user":
            questions.append({"label": f.label, "reason": d.reason, "proposed": d.proposed})
        plan.append((f, d))
    if questions:
        shot = _snap(page, settings, key, "needs_user")
        return ApplyOutcome(WAITING_USER, questions[0]["reason"], shot, questions, url)

    for f, d in plan:
        if d.action in ("fill", "select", "check", "upload"):
            _fill_field(page, f, d, resume)

    # legal checkboxes that remain unticked are handled by field_mapper (ask_user) - nothing else to do here.
    shot = _snap(page, settings, key, "pre_submit")
    if settings.dry_run:
        log.info("WOULD SUBMIT application for %s (%s) - screenshot %s", job.get("title"), job.get("company"), shot)
        return ApplyOutcome(DRY_RUN_STOPPED, "dry run: stopped before submit", shot, application_url=url)

    submit = None
    for sel in strategy.submit_selectors:
        loc = page.locator(sel)
        if loc.count():
            submit = loc.first
            break
    if submit is None:
        return ApplyOutcome(FAILED, "submit button not found", shot, application_url=url)
    submit.click()
    page.wait_for_load_state("networkidle", timeout=20000)

    post = page.content()
    if detect_captcha(post):
        shot2 = _snap(page, settings, key, "post_submit_captcha")
        return ApplyOutcome(WAITING_USER, "captcha challenge after submit", shot2, application_url=page.url)
    low = page.inner_text("body").lower() if page.locator("body").count() else ""
    if any(m in low for m in strategy.success_markers) or any(m in page.url.lower() for m in strategy.success_url_markers):
        return ApplyOutcome(SUBMITTED, "confirmation detected", _snap(page, settings, key, "submitted"),
                            application_url=page.url)
    shot3 = _snap(page, settings, key, "unconfirmed")
    # The click happened, so the application may well have been received: never auto-retry (no double submit).
    return ApplyOutcome(WAITING_USER, "submit clicked but no confirmation seen - verify manually (not retried)",
                        shot3, application_url=page.url)


def apply_to_job(job: dict, cover_letter: str, profile: Profile, settings: Settings, llm: LLM | None,
                 approved: dict[str, str] | None = None, max_retries: int = 2) -> ApplyOutcome:
    strategy = pick_strategy(job["url"], settings.enable_generic_apply)
    if strategy is None:
        return ApplyOutcome(WAITING_USER, "no automated strategy for this site (apply manually)",
                            application_url=job["url"])
    url = strategy.apply_url(job["url"])
    if not RobotsChecker().allowed(url):
        return ApplyOutcome(WAITING_USER, "robots.txt disallows automated access to the form", application_url=url)

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    last = ""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for attempt in range(max_retries + 1):
                ctx = browser.new_context(viewport={"width": 1280, "height": 1800})
                page = ctx.new_page()
                page.set_default_timeout(20000)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    return run_form(page, strategy, job, cover_letter, profile, settings, llm, approved or {})
                except PWTimeout as e:
                    last = f"timeout: {str(e)[:150]}"
                    log.warning("apply attempt %d timed out for %s", attempt + 1, job.get("title"))
                    if attempt == max_retries:
                        shot = _save_failure(page, settings, job["job_key"])
                        return ApplyOutcome(FAILED, last, shot, application_url=url)
                except PWError as e:
                    shot = _save_failure(page, settings, job["job_key"])
                    return ApplyOutcome(FAILED, f"browser error: {str(e)[:200]}", shot, application_url=url)
                finally:
                    ctx.close()
        finally:
            browser.close()
    return ApplyOutcome(FAILED, last or "unknown", None, application_url=url)
