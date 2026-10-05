from __future__ import annotations

from datetime import date

from jobagent.telegram.reports import format_report, main_reasons

# Section 18 of the requirements doc (the example report) - the first 13 lines must match exactly.
DOC_EXAMPLE_STATS = {"scanned": 87, "qualified": 26, "selected": 18, "emails_sent": 5, "browser_submitted": 12,
                     "waiting_user": 1, "failed": 0, "skipped": 61,
                     "skip_reasons": {"requires_3plus_years": 30, "title_seniority_or_unrelated": 14, "duplicate": 8,
                                      "ai_rejected": 6, "no_apply_route": 3}}
DOC_EXAMPLE = """Daily Job Report

Jobs scanned: 87
Qualified: 26
Selected: 18

Email applications sent: 5
Browser applications submitted: 12
Waiting for manual action: 1
Failed/retry queue: 0

Skipped: 61
Main reasons: experience mismatch, senior role, duplicate, irrelevant technology, or no suitable application route."""

JOBS = [{"company": "Acme AI", "title": "AI Developer", "status": "EMAIL_SENT", "url": "https://x.io/1"},
        {"company": "Orbit", "title": "Agentic AI Developer", "status": "WAITING_USER", "url": "https://x.io/2"}]


def test_report_matches_section_18_exactly():
    text = format_report(date(2026, 10, 5), DOC_EXAMPLE_STATS, JOBS, dry_run=False)
    assert text.startswith(DOC_EXAMPLE + "\n")
    assert "[DRY RUN]" not in text and "DRY RUN" not in text


def test_report_lists_company_role_url_status_for_selected():
    text = format_report(date(2026, 10, 5), DOC_EXAMPLE_STATS, JOBS, dry_run=False)
    assert "• Acme AI | AI Developer | EMAIL_SENT | https://x.io/1" in text
    assert "• Orbit | Agentic AI Developer | WAITING_USER | https://x.io/2" in text


def test_dry_run_note_goes_after_the_doc_layout():
    text = format_report(date(2026, 10, 5), DOC_EXAMPLE_STATS, JOBS, dry_run=True, cap=15)
    assert text.startswith(DOC_EXAMPLE) and "DRY RUN – nothing was sent" in text and "Daily cap: 15" in text


def test_reasons_are_humanised_merged_and_ranked():
    assert main_reasons({"requires_3plus_years": 5, "title_seniority_or_unrelated": 2}) == "experience mismatch, or senior role"
    assert main_reasons({"title_not_relevant": 1}) == "irrelevant role"
    assert main_reasons({}) == "none"
    assert main_reasons({"some_new_code": 2}) == "some new code"


def test_quota_warning_and_empty_list():
    text = format_report(date(2026, 10, 5), {}, [], dry_run=True, quota_limited=True, quota_detail="openrouter.ai — HTTP 429")
    assert "⚠️ Free-tier limit reached: openrouter.ai — HTTP 429" in text and "• none" in text
    assert "Jobs scanned: 0" in text and "Main reasons: none." in text
