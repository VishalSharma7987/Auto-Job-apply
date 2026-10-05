from __future__ import annotations

from datetime import date

from jobagent.telegram.reports import format_report

STATS = {"scanned": 80, "qualified": 12, "selected": 10, "emails_sent": 4, "browser_submitted": 5,
         "waiting_user": 1, "failed": 0, "skipped": 68, "skip_reasons": {"title_not_relevant": 40, "requires_3plus_years": 20}}
JOBS = [{"company": "Acme AI", "title": "AI Developer", "status": "EMAIL_SENT", "url": "https://x.io/1"},
        {"company": "Orbit", "title": "Agentic AI Developer", "status": "WAITING_USER", "url": "https://x.io/2"}]


def test_report_has_all_metrics_in_order():
    text = format_report(date(2026, 10, 5), STATS, JOBS, dry_run=False, cap=15)
    labels = ["Jobs scanned: 80", "Qualified jobs: 12", "Selected for application: 10 (cap 15/day)", "Emails sent: 4",
              "Browser applications submitted: 5", "Waiting for you: 1", "Failed: 0", "Skipped: 68"]
    pos = [text.index(x) for x in labels]
    assert pos == sorted(pos)
    assert "2026-10-05" in text and "[DRY RUN]" not in text
    assert "title_not_relevant=40" in text


def test_report_lists_selected_jobs_company_role_status_url():
    text = format_report(date(2026, 10, 5), STATS, JOBS, dry_run=True)
    assert "• Acme AI | AI Developer | EMAIL_SENT | https://x.io/1" in text
    assert "• Orbit | Agentic AI Developer | WAITING_USER | https://x.io/2" in text
    assert "[DRY RUN]" in text


def test_report_quota_warning_and_empty_list():
    text = format_report(date(2026, 10, 5), {}, [], dry_run=True, quota_limited=True, quota_detail="openrouter.ai — HTTP 429")
    assert "⚠️ Free-tier limit reached: openrouter.ai — HTTP 429" in text and "• none" in text
    assert "Jobs scanned: 0" in text
