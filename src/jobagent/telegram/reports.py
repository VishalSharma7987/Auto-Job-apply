"""Daily report formatter - layout from section 18 of the requirements doc:

    Daily Job Report

    Jobs scanned: 87
    Qualified: 26
    Selected: 18

    Email applications sent: 5
    Browser applications submitted: 12
    Waiting for manual action: 1
    Failed/retry queue: 0

    Skipped: 61
    Main reasons: experience mismatch, senior role, duplicate, irrelevant technology, or no suitable application route.

    + company, role, application URL and status for the selected opportunities.
"""

from __future__ import annotations

from datetime import date

from jobagent.pipeline.state import RunSummary

# internal skip codes -> wording used in the report
REASON_LABELS = {
    "requires_3plus_years": "experience mismatch",
    "title_seniority_or_unrelated": "senior role",
    "title_not_relevant": "irrelevant role",
    "location_not_allowed": "location mismatch",
    "ai_rejected": "irrelevant technology",
    "duplicate": "duplicate",
    "no_apply_route": "no suitable application route",
    "already_contacted": "already contacted",
    "user_skip": "skipped by you",
}


def main_reasons(skip_reasons: dict, top: int = 5) -> str:
    merged: dict[str, int] = {}
    for code, n in (skip_reasons or {}).items():
        label = REASON_LABELS.get(code, code.replace("_", " "))
        merged[label] = merged.get(label, 0) + int(n)
    ranked = [k for k, _ in sorted(merged.items(), key=lambda kv: -kv[1])][:top]
    if not ranked:
        return "none"
    return ranked[0] if len(ranked) == 1 else ", ".join(ranked[:-1]) + ", or " + ranked[-1]


def format_report(day: date, stats: dict, selected_jobs: list[dict], dry_run: bool,
                  quota_limited: bool = False, quota_detail: str = "", cap: int | None = None) -> str:
    g = lambda k: int(stats.get(k, 0) or 0)  # noqa: E731
    lines = [
        "Daily Job Report",
        "",
        f"Jobs scanned: {g('scanned')}",
        f"Qualified: {g('qualified')}",
        f"Selected: {g('selected')}",
        "",
        f"Email applications sent: {g('emails_sent')}",
        f"Browser applications submitted: {g('browser_submitted')}",
        f"Waiting for manual action: {g('waiting_user')}",
        f"Failed/retry queue: {g('failed')}",
        "",
        f"Skipped: {g('skipped')}",
        f"Main reasons: {main_reasons(stats.get('skip_reasons') or {})}.",
        "",
        "Selected opportunities (company | role | status | application URL):",
    ]
    if selected_jobs:
        for j in selected_jobs:
            lines.append(f"• {j['company']} | {j['title']} | {j['status']} | {j['url']}")
    else:
        lines.append("• none")
    notes = []
    if dry_run:
        notes.append("ℹ️ DRY RUN – nothing was sent or submitted.")
    if cap:
        notes.append(f"ℹ️ Daily cap: {cap} applications.")
    if quota_limited:
        notes.append(f"⚠️ Free-tier limit reached: {quota_detail}")
    if notes:
        lines += [""] + notes
    return "\n".join(lines)


def todays_selected(repo) -> list[dict]:
    """Jobs for which an application row was created today (= selected today)."""
    from jobagent.utils.dates import today_utc

    day = today_utc().isoformat()
    out = []
    for a in repo.list_applications(200):
        if str(a.get("created_at", ""))[:10] != day:
            continue
        job = repo.get_job(a["job_id"])
        if job:
            out.append({"company": job["company"], "title": job["title"], "status": job["status"], "url": job["url"]})
    return out


def format_summary(s: RunSummary, day: date, stats: dict, dry_run: bool, cap: int | None = None) -> str:
    return format_report(day, stats, s.selected_jobs, dry_run, s.quota_limited, s.quota_detail, cap)
