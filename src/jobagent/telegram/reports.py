"""Daily report formatter.

NOTE: the original requirements doc (section 18) was not available when this was written; the layout
below follows the metrics the project brief names (see docs/DECISIONS.md D-001). Adjust here if needed.
"""

from __future__ import annotations

from datetime import date

from jobagent.pipeline.state import RunSummary


def format_report(day: date, stats: dict, selected_jobs: list[dict], dry_run: bool,
                  quota_limited: bool = False, quota_detail: str = "", cap: int | None = None) -> str:
    lines = [
        f"📊 Daily Job Agent Report — {day.isoformat()}" + ("  [DRY RUN]" if dry_run else ""),
        "",
        f"Jobs scanned: {stats.get('scanned', 0)}",
        f"Qualified jobs: {stats.get('qualified', 0)}",
        f"Selected for application: {stats.get('selected', 0)}" + (f" (cap {cap}/day)" if cap else ""),
        f"Emails sent: {stats.get('emails_sent', 0)}",
        f"Browser applications submitted: {stats.get('browser_submitted', 0)}",
        f"Waiting for you: {stats.get('waiting_user', 0)}",
        f"Failed: {stats.get('failed', 0)}",
        f"Skipped: {stats.get('skipped', 0)}",
    ]
    reasons = stats.get("skip_reasons") or {}
    if reasons:
        lines.append("Skip reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))
    if quota_limited:
        lines += ["", f"⚠️ Free-tier limit reached: {quota_detail}"]
    lines += ["", "Selected jobs (company | role | status | url):"]
    if selected_jobs:
        for j in selected_jobs:
            lines.append(f"• {j['company']} | {j['title']} | {j['status']} | {j['url']}")
    else:
        lines.append("• none")
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
