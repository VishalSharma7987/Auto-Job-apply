"""LangGraph pipeline:
discover -> normalize -> dedupe -> cheap_filter -> ai_match -> contact -> prepare -> act -> record -> report

Each node works on job ids + DB rows, so it is idempotent and resumable: a crashed run simply continues
from the persisted job statuses on the next run.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import yaml
from langgraph.graph import END, START, StateGraph

from jobagent.db.base import Repository
from jobagent.discovery.dedupe import dedupe
from jobagent.discovery.normalize import normalize
from jobagent.email.generator import generate_email, template_email
from jobagent.email.gmail_smtp import SendBlocked
from jobagent.llm.client import LLMError, QuotaExceeded
from jobagent.matching.ai_match import ai_match, cached_match
from jobagent.matching.cheap_filter import cheap_filter
from jobagent.matching.scoring import score_job
from jobagent.models import Job
from jobagent.pipeline import state as S
from jobagent.pipeline.runner import Ctx
from jobagent.pipeline.state import PipelineState
from jobagent.profile import resume_dir, select_resume
from jobagent.telegram.commands import is_killed, is_paused
from jobagent.telegram.reports import format_summary, todays_selected
from jobagent.utils.dates import parse_dt, utcnow
from jobagent.utils.hashing import normalize_text

log = logging.getLogger(__name__)
CONTACT_TRY_FACTOR = 3  # look at most 3x cap candidates per run for a route
CONTACT_COOLDOWN_DAYS = 14  # one email per recruiting address per 14 days


def row_to_job(row: dict, website: str | None = None) -> Job:
    return Job(
        id=row["id"], job_key=row["job_key"], company=row["company"], title=row["title"], url=row.get("url") or "",
        source=row.get("source") or "", location=row.get("location") or "", remote=bool(row.get("remote")),
        description=row.get("description") or "", posted_at=row.get("posted_at"), status=row["status"],
        match_json=row.get("match_json"), match_reasons=row.get("match_reasons") or [], score=row.get("score"),
        skip_reason=row.get("skip_reason"), company_website=website)


def _company_websites(ctx: Ctx) -> dict[str, str]:
    path = ctx.settings.abs_path(ctx.settings.companies_path)
    out: dict[str, str] = {}
    if Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for key in ("greenhouse", "lever", "ashby", "career_pages"):
            for c in data.get(key) or []:
                if c.get("website"):
                    out[c.get("name") or c.get("slug")] = c["website"]
    return out


def _skip(ctx: Ctx, reason: str) -> None:
    ctx.repo.bump_stat(ctx.day, "skipped")
    ctx.repo.add_skip_reason(ctx.day, reason)


def build_graph(ctx: Ctx):
    repo: Repository = ctx.repo
    st = ctx.settings
    websites = _company_websites(ctx)
    raw_websites: dict[str, str] = {}

    def _history() -> tuple[set[tuple[str, str]], set[str]]:
        """(company, role) pairs already contacted/applied, and contact ids emailed within the cool-down."""
        applied: set[tuple[str, str]] = set()
        recent: set[str] = set()
        cutoff = utcnow() - timedelta(days=CONTACT_COOLDOWN_DAYS)
        for a in repo.list_applications(2000):
            sent = a.get("email_sent_at") or a.get("submitted_at")
            if sent or a.get("status") in ("SENDING", "SENT", "SUBMITTED"):
                applied.add((normalize_text(a.get("company") or ""), normalize_text(a.get("role") or "")))
            if a.get("email_sent_at") and a.get("contact_id"):
                when = parse_dt(a["email_sent_at"])
                if when and when >= cutoff:
                    recent.add(a["contact_id"])
        return applied, recent

    # ------------------------------------------------------------------ discovery
    def discover(state: PipelineState) -> PipelineState:
        if is_paused(repo):
            ctx.summary.notes.append("paused: discovery skipped")
            return {"raw": []}
        raw = []
        for ad in ctx.adapters:
            try:
                got = ad.fetch()
                log.info("source %s: %d jobs", ad.name, len(got))
                raw += got
            except Exception as e:  # noqa: BLE001 - one broken source must not stop the run
                log.warning("source %s failed: %s", ad.name, e)
                repo.add_event("error", "source_failed", None, {"source": ad.name, "error": str(e)[:300]})
        return {"raw": raw}

    def normalize_node(state: PipelineState) -> PipelineState:
        jobs = [j for j in (normalize(r) for r in state.get("raw", [])) if j]
        for j in jobs:
            if j.company_website:
                raw_websites[j.job_key] = j.company_website
        return {"jobs": jobs}

    def dedupe_node(state: PipelineState) -> PipelineState:
        before = state.get("jobs", [])
        jobs = dedupe(before)
        dupes = len(before) - len(jobs)
        repo.bump_stat(ctx.day, "scanned", len(before))
        ctx.summary.scanned = len(before)
        for _ in range(dupes):
            _skip(ctx, "duplicate")
        ctx.summary.skipped += dupes
        return {"jobs": jobs}

    def cheap_filter_node(state: PipelineState) -> PipelineState:
        """Deterministic filter. Only survivors are stored (keeps the free DB small); rejects are counted."""
        cand: list[str] = []
        for j in state.get("jobs", []):
            ok, reason = cheap_filter(j)
            if not ok:
                _skip(ctx, reason)
                ctx.summary.skipped += 1
                continue
            row, created = repo.upsert_job(j)
            if created or row["status"] == S.DISCOVERED:
                cand.append(row["id"])
            else:  # seen in an earlier run and already processed: duplicate prevention
                _skip(ctx, "duplicate")
                ctx.summary.skipped += 1
        return {"candidates": cand}

    # ------------------------------------------------------------------ AI matching
    def ai_match_node(state: PipelineState) -> PipelineState:
        # unprocessed DISCOVERED jobs from earlier runs too (e.g. quota-limited yesterday)
        ids = list(dict.fromkeys(state.get("candidates", []) + [r["id"] for r in repo.list_jobs([S.DISCOVERED], 500)]))
        rows = [repo.get_job(i) for i in ids]
        rows = [r for r in rows if r and r["status"] == S.DISCOVERED]
        rows.sort(key=lambda r: str(r.get("posted_at") or ""), reverse=True)  # newest first
        rows.sort(key=lambda r: cached_match(r) is None)  # cached results cost nothing: go first
        budget = st.max_ai_matches_per_run
        qualified: list[str] = []
        for r in rows:
            job = row_to_job(r)
            cached = cached_match(r)
            if cached is None and budget <= 0:
                break
            try:
                res, used = ai_match(job, r, ctx.profile, ctx.llm, repo)
            except QuotaExceeded as q:
                ctx.summary.quota_limited, ctx.summary.quota_detail = True, f"{q.provider} — {q.details}"
                repo.add_event("warn", "quota_exceeded", None, {"provider": q.provider, "details": q.details[:300]})
                ctx.notify(f"⚠️ Free-tier limit reached: {q.provider} — {q.details[:300]}")
                break
            except LLMError as e:
                log.warning("ai_match failed for %s: %s", job.title, e)
                repo.add_event("error", "ai_match_failed", r["id"], {"error": str(e)[:300]})
                budget -= 1
                continue
            if used:
                budget -= 1
            if res.decision == "QUALIFIED" and res.location_ok:
                score, why = score_job(job, res)
                S.transition(repo, r["id"], S.QUALIFIED, score=score, match_reasons=res.reasons + [f"score: {'; '.join(why)}"])
                repo.bump_stat(ctx.day, "qualified")
                ctx.summary.qualified += 1
                qualified.append(r["id"])
                repo.add_event("info", "job_qualified", r["id"], {"score": score, "reasons": res.reasons[:5]})
            else:
                reasons = res.reasons or ["ai_rejected"]
                S.transition(repo, r["id"], S.REJECTED, skip_reason="ai_rejected", match_reasons=reasons)
                _skip(ctx, "ai_rejected")
                ctx.summary.skipped += 1
                repo.add_event("info", "job_rejected", r["id"], {"reasons": reasons[:5]})
        return {"qualified": qualified}

    # ------------------------------------------------------------------ contact + route selection
    def contact_node(state: PipelineState) -> PipelineState:
        """Pick up to the daily cap of routable jobs (best score first) and decide email vs browser."""
        if is_paused(repo):
            return {"selected": []}
        remaining = st.max_applications_per_day - repo.count_applications_on(ctx.day)
        pool = [r for r in repo.list_jobs([S.QUALIFIED, S.CONTACT_FOUND], 1000) if repo.get_application(r["id"], "email") is None
                and repo.get_application(r["id"], "browser") is None]
        pool.sort(key=lambda r: -(r.get("score") or 0))
        applied_roles, recent_contacts = _history()
        selected: list[dict] = []
        tries = 0
        for r in pool:
            if len(selected) >= remaining or tries >= max(remaining, 1) * CONTACT_TRY_FACTOR:
                break
            if (normalize_text(r["company"]), normalize_text(r["title"])) in applied_roles:
                # same company + role already contacted/applied (e.g. cross-posted under another URL)
                S.transition(repo, r["id"], S.REJECTED, skip_reason="already_contacted")
                _skip(ctx, "already_contacted")
                ctx.summary.skipped += 1
                continue
            tries += 1
            job = row_to_job(r, websites.get(r["company"]) or raw_websites.get(r["job_key"]))
            route, contact_id = None, None
            try:
                c = ctx.finder.find(job)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                log.warning("contact lookup failed for %s: %s", job.company, e)
                c = None
            if c and c.confidence in ("HIGH", "MEDIUM"):
                crow = repo.save_contact(c.company, c.email, c.source_url, c.confidence)
                repo.add_event("info", "contact_found", r["id"], {"email": c.email, "source_url": c.source_url,
                                                                  "confidence": c.confidence})
                if crow["id"] in recent_contacts:
                    # never mail the same recruiting address twice within the cool-down window
                    c = None
                else:
                    contact_id, route = crow["id"], "email"
                    if r["status"] == S.QUALIFIED:
                        S.transition(repo, r["id"], S.CONTACT_FOUND)
            if route is None and st.enable_browser_apply:
                from jobagent.apply.strategies import pick_strategy

                if pick_strategy(job.url, st.enable_generic_apply):
                    route = "browser"
            if route is None:
                # qualified, but no automatable route: counted as skipped ("no suitable application route") and
                # parked as a manual_apply task so you can still apply by hand (/skip clears it)
                S.transition(repo, r["id"], S.WAITING_USER, skip_reason="no_apply_route")
                repo.create_task(r["id"], "manual_apply", {"reason": "no published recruiting email or supported form",
                                                           "url": r["url"]}, status="waiting_user")
                _skip(ctx, "no_apply_route")
                ctx.summary.skipped += 1
                continue
            selected.append({"id": r["id"], "route": route, "contact_id": contact_id})
            applied_roles.add((normalize_text(r["company"]), normalize_text(r["title"])))
        return {"selected": selected}

    # ------------------------------------------------------------------ draft preparation
    def prepare_node(state: PipelineState) -> PipelineState:
        for sel in state.get("selected", []):
            r = repo.get_job(sel["id"])
            if r is None:
                continue
            app, created = repo.claim_application(r["id"], sel["route"], r["company"], r["title"])
            if not created and app.get("email_body"):
                continue  # already drafted (resume after crash)
            job = row_to_job(r)
            already_notified = ctx.summary.quota_limited
            try:
                if ctx.summary.quota_limited:
                    raise QuotaExceeded("llm", ctx.summary.quota_detail)
                draft, how = generate_email(job, ctx.profile, ctx.llm)
            except QuotaExceeded as q:
                ctx.summary.quota_limited, ctx.summary.quota_detail = True, f"{q.provider} — {q.details}"
                if not already_notified:
                    ctx.notify(f"⚠️ Free-tier limit reached: {q.provider} — {q.details[:300]}")
                draft, how = template_email(job, ctx.profile, {}), "template(quota)"
            repo.update_application(app["id"], email_subject=draft.subject, email_body=draft.body,
                                    contact_id=sel.get("contact_id"), resume_version=select_resume(st, r["title"]).name,
                                    notes=f"draft:{how}")
            if r["status"] in (S.QUALIFIED, S.CONTACT_FOUND):
                S.transition(repo, r["id"], S.READY)
            repo.add_event("info", "draft_prepared", r["id"], {"route": sel["route"], "how": how,
                                                              "resume": select_resume(st, r["title"]).name})
            if created:
                repo.bump_stat(ctx.day, "selected")
                ctx.summary.selected += 1
        return {}

    # ------------------------------------------------------------------ act
    def act_node(state: PipelineState) -> PipelineState:
        if ctx.mode == "jobs":
            return {}
        if is_paused(repo):
            ctx.summary.notes.append("paused/killswitch: no actions taken")
            return {}
        if not st.fake_mode and not ctx.resume.exists():
            # no resume anywhere (Storage / env / local): discovery + matching + drafts still ran, but we must not
            # email or apply without it. The worker already told you on Telegram to send the PDF.
            ctx.summary.notes.append("no resume: email/apply steps skipped")
            repo.add_event("warn", "no_resume_actions_skipped", None, None)
            return {}
        for r in repo.list_jobs([S.READY], 500):
            if is_killed(repo):
                break
            if ctx.job_id and not str(r["id"]).startswith(ctx.job_id):
                continue
            email_app = repo.get_application(r["id"], "email")
            browser_app = repo.get_application(r["id"], "browser")
            if email_app and not email_app.get("email_sent_at"):
                _act_email(r, email_app)
            elif browser_app and not browser_app.get("submitted_at"):
                _act_browser(r, browser_app)
        return {}

    def _task_for(job_id: str, type_: str) -> dict:
        for t in repo.list_tasks(None, 500):
            if t.get("job_id") == job_id and t["type"] == type_ and t["status"] in ("queued", "failed"):
                repo.update_task(t["id"], status="running")
                return repo.get_task(t["id"])  # type: ignore[return-value]
        return repo.create_task(job_id, type_, None, status="running")

    def _act_email(r: dict, app: dict) -> None:
        if app.get("status") == "SENDING" and _approved_answers_flag(r["id"]):
            repo.update_application(app["id"], status="READY")  # user confirmed it was NOT sent
            app = repo.get_application(r["id"], "email")  # type: ignore[assignment]
        if app.get("status") == "SENDING":
            # a previous run died between "about to send" and "recorded sent": we cannot know. Never re-send.
            _wait_user(r, "email", "previous send attempt state unknown - check Sent folder, then /skip or /approve",
                       None, app_note="ambiguous_send")
            return
        if not app.get("contact_id"):
            _fail(r, None, "email route without contact")
            return
        contact = repo.get_contact(app["contact_id"])
        if contact is None or contact["confidence"] not in ("HIGH", "MEDIUM"):
            _fail(r, None, "contact missing or low confidence")
            return
        if st.dry_run:
            log.info("WOULD SEND email to %s for %s @ %s (contact source %s, %s)", contact["email"], r["title"],
                     r["company"], contact["source_url"], contact["confidence"])
            repo.add_event("info", "would_send_email", r["id"], {"to": contact["email"], "subject": app.get("email_subject")})
            return
        task = _task_for(r["id"], "email")
        repo.update_application(app["id"], status="SENDING")  # at-most-once marker, written BEFORE sending
        try:
            ctx.sender.send(contact["email"], app["email_subject"], app["email_body"], _resume_for(app))
        except SendBlocked as e:
            repo.update_application(app["id"], status="READY")
            _fail(r, task, str(e))
            return
        except Exception as e:  # noqa: BLE001
            repo.update_application(app["id"], status="READY")
            _fail(r, task, f"smtp error: {str(e)[:200]}")
            return
        repo.update_application(app["id"], status="SENT", email_sent_at=utcnow().isoformat())
        S.transition(repo, r["id"], S.EMAIL_SENT)
        repo.add_event("info", "email_sent", r["id"], {"to": contact["email"], "source_url": contact["source_url"],
                                                       "subject": app.get("email_subject")})
        repo.update_task(task["id"], status="completed")
        repo.bump_stat(ctx.day, "emails_sent")
        ctx.summary.emails_sent += 1

    def _approved_answers(job_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for t in repo.list_tasks(None, 500):
            p = t.get("payload") or {}
            if t.get("job_id") == job_id and p.get("approved"):
                for q in p.get("questions", []):
                    if q.get("proposed"):
                        out[q["label"]] = q["proposed"]
        return out

    def _approved_answers_flag(job_id: str) -> bool:
        return any(t.get("job_id") == job_id and (t.get("payload") or {}).get("approved") for t in repo.list_tasks(None, 500))

    def _act_browser(r: dict, app: dict) -> None:
        if st.dry_run and (app.get("notes") or "").startswith("dry_run"):
            return  # already rehearsed; nothing new to learn
        task = _task_for(r["id"], "browser")
        S.transition(repo, r["id"], S.APPLICATION_STARTED)
        job_settings = st.model_copy(update={"resume_path": str(_resume_for(app))})
        try:
            out = ctx.apply_fn(r, app.get("email_body") or "", ctx.profile, job_settings, ctx.llm,
                               _approved_answers(r["id"]))
        except QuotaExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - unexpected website behaviour: save state, report, never crash the run
            log.exception("browser apply crashed")
            repo.update_application(app["id"], notes=f"crashed: {type(e).__name__}: {str(e)[:300]}")
            S.transition(repo, r["id"], S.FAILED)
            _fail(r, task, f"unexpected error: {type(e).__name__}: {str(e)[:200]}", already_transitioned=True,
                  notify=True)
            return
        if out.status == "SUBMITTED":
            repo.update_application(app["id"], status="SUBMITTED", submitted_at=utcnow().isoformat(),
                                    application_url=out.application_url)
            S.transition(repo, r["id"], S.SUBMITTED)
            repo.update_task(task["id"], status="completed")
            repo.bump_stat(ctx.day, "browser_submitted")
            ctx.summary.browser_submitted += 1
            repo.add_event("info", "application_submitted", r["id"], {"url": out.application_url})
        elif out.status == "DRY_RUN_STOPPED":
            log.info("WOULD SUBMIT application for %s @ %s", r["title"], r["company"])
            repo.update_application(app["id"], notes="dry_run: " + out.reason, application_url=out.application_url)
            repo.update_task(task["id"], status="completed", last_error="dry run")
            S.transition(repo, r["id"], S.READY)
        elif out.status == "WAITING_USER":
            _wait_user(r, "browser", out.reason, out.screenshot, out.questions, task, out.application_url)
        else:
            repo.update_application(app["id"], notes=f"failed: {out.reason}"[:500])
            S.transition(repo, r["id"], S.FAILED)
            _fail(r, task, out.reason, already_transitioned=True, notify=True, shot=out.screenshot)

    def _wait_user(r: dict, route: str, reason: str, shot: str | None, questions: list | None = None,
                   task: dict | None = None, url: str = "", app_note: str | None = None) -> None:
        payload = {"reason": reason, "questions": questions or [], "url": url or r["url"], "screenshot": shot}
        if task is None:
            task = repo.create_task(r["id"], route, payload, status="waiting_user")
        else:
            repo.update_task(task["id"], status="waiting_user", payload=payload)
        if r["status"] != S.WAITING_USER:
            S.transition(repo, r["id"], S.WAITING_USER)
        repo.bump_stat(ctx.day, "waiting_user")
        ctx.summary.waiting_user += 1
        tid, jid = str(task["id"])[:8], str(r["id"])[:8]
        ctx.notify(f"⏳ Waiting for you\nReason: {reason}\nCompany: {r['company']}\nRole: {r['title']}\n"
                   f"URL: {url or r['url']}\n\nContinue: /approve {tid}\nSkip: /skip {jid}")
        ctx.notify_photo(shot, f"{r['company']} – {r['title']}")

    def _resume_for(app: dict) -> Path:
        name = app.get("resume_version")
        cand = resume_dir(st) / name if name else None
        return cand if cand is not None and cand.exists() else ctx.resume

    def _fail(r: dict, task: dict | None, err: str, already_transitioned: bool = False, notify: bool = False,
              shot: str | None = None) -> None:
        if task is None:
            task = repo.create_task(r["id"], "email", None, status="running")
        repo.update_task(task["id"], status="failed", last_error=err[:500])
        if not already_transitioned:
            S.transition(repo, r["id"], S.FAILED)
        repo.add_event("error", "action_failed", r["id"], {"error": err[:300]})
        repo.bump_stat(ctx.day, "failed")
        ctx.summary.failed += 1
        if notify:
            lines = ["⚠️ Application failed (will be in the retry queue)", f"Reason: {err[:300]}",
                     f"Company: {r['company']}", f"Role: {r['title']}", f"URL: {r['url']}", "",
                     f"Retry: /retry  Skip: /skip {str(r['id'])[:8]}"]
            ctx.notify("\n".join(lines))
            ctx.notify_photo(shot, f"{r['company']} – {r['title']}")

    # ------------------------------------------------------------------ record + report
    def record_node(state: PipelineState) -> PipelineState:
        ctx.summary.selected_jobs = todays_selected(repo)
        repo.add_event("info", "run_finished", None, {"mode": ctx.mode, "summary": {
            k: getattr(ctx.summary, k) for k in ("scanned", "qualified", "selected", "emails_sent",
                                                 "browser_submitted", "waiting_user", "failed", "skipped")}})
        return {}

    def report_node(state: PipelineState) -> PipelineState:
        text = format_summary(ctx.summary, ctx.day, repo.get_stats(ctx.day), st.dry_run, st.max_applications_per_day)
        print(text)
        ctx.notify(text)
        return {}

    def load_ready(state: PipelineState) -> PipelineState:
        return {}

    def route_start(state: PipelineState) -> str:
        return "load_ready" if ctx.mode == "apply" else "discover"

    g = StateGraph(PipelineState)
    for name, fn in [("discover", discover), ("normalize", normalize_node), ("dedupe", dedupe_node),
                     ("cheap_filter", cheap_filter_node), ("ai_match", ai_match_node), ("contact", contact_node),
                     ("prepare", prepare_node), ("act", act_node), ("record", record_node),
                     ("report", report_node), ("load_ready", load_ready)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, route_start, {"load_ready": "load_ready", "discover": "discover"})
    g.add_edge("load_ready", "act")
    for a, b in [("discover", "normalize"), ("normalize", "dedupe"), ("dedupe", "cheap_filter"),
                 ("cheap_filter", "ai_match"), ("ai_match", "contact"), ("contact", "prepare"),
                 ("prepare", "act"), ("act", "record"), ("record", "report"), ("report", END)]:
        g.add_edge(a, b)
    return g.compile()
