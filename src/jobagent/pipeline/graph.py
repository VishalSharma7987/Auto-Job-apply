"""LangGraph pipeline:
discover -> normalize -> dedupe -> cheap_filter -> ai_match -> contact -> prepare -> act
(the record + Telegram report step runs in runner.finalize, in a `finally`, so it happens even on crash/interrupt).

Each node works on job ids + DB rows, so it is idempotent and resumable: a crashed or time-limited run simply
continues from the persisted job statuses on the next run.

Performance rules (docs/PERF_NOTES.md): every stage logs start/end with elapsed seconds and counts; daily stats are
buffered and written once per stage; DB reads/writes are batched; the time budget is checked between items; the LLM is
only called for jobs that passed the cheap filter and the per-source cap, at most MAX_AI_MATCHES_PER_RUN times, and a
circuit breaker stops calling it after 3 consecutive failures.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
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
from jobagent.matching.cheap_filter import cheap_filter, title_ok
from jobagent.matching.scoring import score_job
from jobagent.models import Job
from jobagent.pipeline import state as S
from jobagent.pipeline.runner import Ctx
from jobagent.pipeline.state import PipelineState
from jobagent.profile import resume_dir, select_resume
from jobagent.telegram.commands import is_killed, is_paused
from jobagent.utils.dates import parse_dt, utcnow
from jobagent.utils.hashing import normalize_text

log = logging.getLogger(__name__)
CONTACT_TRY_FACTOR = 3  # look at most 3x cap candidates per run for a route
CONTACT_COOLDOWN_DAYS = 14  # one email per recruiting address per 14 days
LLM_BREAKER = 3  # consecutive LLM failures before the LLM is skipped for the rest of the run
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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


def _posted(j: Job) -> datetime:
    p = j.posted_at
    if p is None:
        return _EPOCH
    return p if p.tzinfo else p.replace(tzinfo=UTC)


def build_graph(ctx: Ctx):
    repo: Repository = ctx.repo
    st = ctx.settings
    stats = ctx.stats
    budget = ctx.budget
    websites = _company_websites(ctx)
    raw_websites: dict[str, str] = {}

    def skip(reason: str, n: int = 1) -> None:
        stats.skip(reason, n)
        ctx.summary.skipped += n

    def out_of_time(what: str) -> bool:
        if budget.exceeded():
            ctx.mark_partial(f"{what} stopped early: time budget of {budget.seconds:.0f}s reached")
            return True
        return False

    def stage(name: str, fn, skippable: bool = True):
        """Log start/end (elapsed + counts), flush buffered stats, skip the stage when the budget is used up."""

        def run(state: PipelineState) -> PipelineState:
            if skippable and budget.exceeded():
                ctx.mark_partial(f"stage '{name}' skipped: time budget of {budget.seconds:.0f}s reached")
                log.warning("stage %s skipped - time budget reached (run elapsed %.0fs)", name, budget.elapsed())
                return {}
            t0 = time.monotonic()
            log.info("stage %s: start (run elapsed %.0fs)", name, budget.elapsed())
            ok = False
            try:
                out = fn(state)
                ok = True
                return out
            finally:
                try:
                    stats.flush()
                except Exception as e:  # noqa: BLE001 - stats must never break a run
                    log.warning("stats flush failed: %s", e)
                counts = " ".join(f"{k}={v}" for k, v in ctx.metrics.get(name, {}).items())
                log.info("stage %s: %s in %.1fs %s", name, "done" if ok else "ABORTED", time.monotonic() - t0, counts)

        return run

    def _history(apps: list[dict]) -> tuple[set[tuple[str, str]], set[str]]:
        """(company, role) pairs already contacted/applied, and contact ids emailed within the cool-down."""
        applied: set[tuple[str, str]] = set()
        recent: set[str] = set()
        cutoff = utcnow() - timedelta(days=CONTACT_COOLDOWN_DAYS)
        for a in apps:
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
            ctx.metrics["discover"] = {"paused": True}
            return {"raw": []}
        raw = []
        failed = 0
        for ad in ctx.adapters:
            if out_of_time("discovery"):
                break
            t0 = time.monotonic()
            try:
                ad.begin(st.source_timeout_seconds)
                got = ad.fetch()
                log.info("source %s: %d jobs in %.1fs", ad.name, len(got), time.monotonic() - t0)
                raw += got
            except Exception as e:  # noqa: BLE001 - one broken source must not stop the run
                failed += 1
                log.warning("source %s failed after %.1fs: %s", ad.name, time.monotonic() - t0, e)
                repo.add_event("error", "source_failed", None, {"source": ad.name, "error": str(e)[:300]})
        ctx.metrics["discover"] = {"discovered": len(raw), "sources": len(ctx.adapters), "sources_failed": failed}
        return {"raw": raw}

    def normalize_node(state: PipelineState) -> PipelineState:
        """Title check first (microseconds) - only titles that could match pay for HTML sanitising."""
        raws = state.get("raw", [])
        jobs: list[Job] = []
        rejected: Counter = Counter()
        for r in raws:
            ok, reason = title_ok(r.title)
            if not ok:
                rejected[reason] += 1
                continue
            j = normalize(r)
            if j:
                jobs.append(j)
                if j.company_website:
                    raw_websites[j.job_key] = j.company_website
        stats.bump("scanned", len(raws))
        ctx.summary.scanned += len(raws)
        for reason, n in rejected.items():
            skip(reason, n)
        ctx.metrics["normalize"] = {"scanned": len(raws), "title_rejected": sum(rejected.values()),
                                    "normalized": len(jobs)}
        return {"jobs": jobs}

    def dedupe_node(state: PipelineState) -> PipelineState:
        before = state.get("jobs", [])
        jobs = dedupe(before)
        skip("duplicate", len(before) - len(jobs))
        ctx.metrics["dedupe"] = {"unique": len(jobs), "duplicates": len(before) - len(jobs)}
        return {"jobs": jobs}

    def cheap_filter_node(state: PipelineState) -> PipelineState:
        """Deterministic filter -> drop jobs already processed -> per-source cap -> store new candidates.
        Only survivors are stored (keeps the free DB small); everything else is just counted."""
        passed: list[Job] = []
        rejected: Counter = Counter()
        for j in state.get("jobs", []):
            ok, reason = cheap_filter(j)
            if ok:
                passed.append(j)
            else:
                rejected[reason] += 1
        for reason, n in rejected.items():
            skip(reason, n)

        known = repo.get_jobs_by_keys([j.job_key for j in passed]) if passed else {}
        fresh: list[Job] = []
        pending: list[dict] = []  # stored by an earlier run but never matched (e.g. budget/quota)
        dup = 0
        for j in passed:
            row = known.get(j.job_key)
            if row is None:
                fresh.append(j)
            elif row["status"] == S.DISCOVERED:
                pending.append(row)
            else:
                dup += 1
        skip("duplicate", dup)

        by_src: dict[str, list[Job]] = defaultdict(list)
        for j in fresh:
            by_src[j.source].append(j)
        kept: list[Job] = []
        capped = 0
        for lst in by_src.values():
            lst.sort(key=_posted, reverse=True)  # newest first
            kept += lst[: st.max_jobs_per_source]
            capped += max(0, len(lst) - st.max_jobs_per_source)
        skip("source_cap", capped)

        stored = repo.upsert_jobs(kept)
        cand = [row for row, _ in stored if row["status"] == S.DISCOVERED] + pending
        ctx.metrics["cheap_filter"] = {"passed": len(passed), "rejected": sum(rejected.values()),
                                       "already_processed": dup, "capped": capped, "new_stored": len(kept),
                                       "candidates": len(cand)}
        return {"candidates": cand}

    # ------------------------------------------------------------------ AI matching
    def ai_match_node(state: PipelineState) -> PipelineState:
        rows_by_id: dict[str, dict] = {r["id"]: r for r in state.get("candidates", [])}
        for r in repo.list_jobs([S.DISCOVERED], 500):  # unmatched jobs from earlier runs too (one query)
            rows_by_id.setdefault(r["id"], r)
        rows = [r for r in rows_by_id.values() if r["status"] == S.DISCOVERED]
        rows.sort(key=lambda r: str(r.get("posted_at") or ""), reverse=True)  # newest first
        rows.sort(key=lambda r: cached_match(r) is None)  # cached results cost nothing: go first
        calls_left = st.max_ai_matches_per_run
        qualified: list[str] = []
        m = Counter()
        fails_in_row = 0
        for r in rows:
            if out_of_time("AI matching"):
                break
            cached = cached_match(r)
            if cached is None and calls_left <= 0:
                m["left_for_next_run"] = len(rows) - m["done"]
                break
            if cached is None and ctx.llm_down:
                break
            job = row_to_job(r)
            t0 = time.monotonic()
            try:
                res, used = ai_match(job, r, ctx.profile, ctx.llm, repo)
            except QuotaExceeded as q:
                ctx.summary.quota_limited, ctx.summary.quota_detail = True, f"{q.provider} — {q.details}"
                repo.add_event("warn", "quota_exceeded", None, {"provider": q.provider, "details": q.details[:300]})
                ctx.notify(f"⚠️ Free-tier limit reached: {q.provider} — {q.details[:300]}")
                break
            except LLMError as e:
                m["errors"] += 1
                calls_left -= 1
                fails_in_row += 1
                log.warning("AI match failed for %s @ %s after %.1fs: %s", job.title, job.company,
                            time.monotonic() - t0, e)
                repo.add_event("error", "ai_match_failed", r["id"], {"error": str(e)[:300]})
                if fails_in_row >= LLM_BREAKER:
                    ctx.llm_down = (f"LLM unavailable ({fails_in_row} failures in a row, last: {str(e)[:120]}) - "
                                    "remaining jobs stay queued for the next run")
                    log.error(ctx.llm_down)
                    break
                continue
            fails_in_row = 0
            m["done"] += 1
            if used:
                calls_left -= 1
                m["llm_calls"] += 1
            else:
                m["cached"] += 1
            if res.decision == "QUALIFIED" and res.location_ok:
                score, why = score_job(job, res)
                S.transition(repo, r["id"], S.QUALIFIED, score=score, match_reasons=res.reasons + [f"score: {'; '.join(why)}"])
                stats.bump("qualified")
                ctx.summary.qualified += 1
                m["qualified"] += 1
                qualified.append(r["id"])
                repo.add_event("info", "job_qualified", r["id"], {"score": score, "reasons": res.reasons[:5]})
            else:
                reasons = res.reasons or ["ai_rejected"]
                S.transition(repo, r["id"], S.REJECTED, skip_reason="ai_rejected", match_reasons=reasons)
                skip("ai_rejected")
                m["rejected"] += 1
                repo.add_event("info", "job_rejected", r["id"], {"reasons": reasons[:5]})
            log.info("AI match %d/%d: %s @ %s -> %s (%.1fs%s)", m["done"], len(rows), job.title[:60], job.company,
                     res.decision, time.monotonic() - t0, "" if used else ", cached")
        ctx.metrics["ai_match"] = {"candidates": len(rows), **dict(m)}
        return {"qualified": qualified}

    # ------------------------------------------------------------------ contact + route selection
    def contact_node(state: PipelineState) -> PipelineState:
        """Pick up to the daily cap of routable jobs (best score first) and decide email vs browser."""
        if is_paused(repo):
            return {"selected": []}
        apps = repo.list_applications(2000)  # one query instead of two per job
        has_app = {a["job_id"] for a in apps}
        remaining = st.max_applications_per_day - repo.count_applications_on(ctx.day)
        pool = [r for r in repo.list_jobs([S.QUALIFIED, S.CONTACT_FOUND], 1000) if r["id"] not in has_app]
        pool.sort(key=lambda r: -(r.get("score") or 0))
        applied_roles, recent_contacts = _history(apps)
        selected: list[dict] = []
        tries = 0
        m = Counter()
        for r in pool:
            if len(selected) >= remaining or tries >= max(remaining, 1) * CONTACT_TRY_FACTOR:
                break
            if out_of_time("contact search"):
                break
            if (normalize_text(r["company"]), normalize_text(r["title"])) in applied_roles:
                # same company + role already contacted/applied (e.g. cross-posted under another URL)
                S.transition(repo, r["id"], S.REJECTED, skip_reason="already_contacted")
                skip("already_contacted")
                continue
            tries += 1
            job = row_to_job(r, websites.get(r["company"]) or raw_websites.get(r["job_key"]))
            route, contact_id = None, None
            t0 = time.monotonic()
            try:
                c = ctx.finder.find(job)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                log.warning("contact lookup failed for %s: %s", job.company, e)
                c = None
            log.info("contact lookup %s @ %s: %s (%.1fs)", job.title[:50], job.company,
                     f"{c.email} [{c.confidence}]" if c else "none", time.monotonic() - t0)
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
                skip("no_apply_route")
                m["no_route"] += 1
                continue
            m[route] += 1
            selected.append({"id": r["id"], "route": route, "contact_id": contact_id})
            applied_roles.add((normalize_text(r["company"]), normalize_text(r["title"])))
        ctx.metrics["contact"] = {"pool": len(pool), "cap_left": max(remaining, 0), "selected": len(selected), **dict(m)}
        return {"selected": selected}

    # ------------------------------------------------------------------ draft preparation
    def prepare_node(state: PipelineState) -> PipelineState:
        m = Counter()
        for sel in state.get("selected", []):
            if out_of_time("draft preparation"):
                break
            r = repo.get_job(sel["id"])
            if r is None:
                continue
            app, created = repo.claim_application(r["id"], sel["route"], r["company"], r["title"])
            if not created and app.get("email_body"):
                continue  # already drafted (resume after crash)
            job = row_to_job(r)
            already_notified = ctx.summary.quota_limited
            t0 = time.monotonic()
            try:
                if ctx.summary.quota_limited:
                    raise QuotaExceeded("llm", ctx.summary.quota_detail)
                if ctx.llm_down:
                    draft, how = template_email(job, ctx.profile, {}), "template(llm unavailable)"
                else:
                    draft, how = generate_email(job, ctx.profile, ctx.llm)
            except QuotaExceeded as q:
                ctx.summary.quota_limited, ctx.summary.quota_detail = True, f"{q.provider} — {q.details}"
                if not already_notified:
                    ctx.notify(f"⚠️ Free-tier limit reached: {q.provider} — {q.details[:300]}")
                draft, how = template_email(job, ctx.profile, {}), "template(quota)"
            log.info("draft %s for %s @ %s via %s (%.1fs)", sel["route"], job.title[:50], job.company, how,
                     time.monotonic() - t0)
            repo.update_application(app["id"], email_subject=draft.subject, email_body=draft.body,
                                    contact_id=sel.get("contact_id"), resume_version=select_resume(st, r["title"]).name,
                                    notes=f"draft:{how}")
            if r["status"] in (S.QUALIFIED, S.CONTACT_FOUND):
                S.transition(repo, r["id"], S.READY)
            repo.add_event("info", "draft_prepared", r["id"], {"route": sel["route"], "how": how,
                                                              "resume": select_resume(st, r["title"]).name})
            m["drafts"] += 1
            if created:
                stats.bump("selected")
                ctx.summary.selected += 1
        ctx.metrics["prepare"] = dict(m) or {"drafts": 0}
        return {}

    # ------------------------------------------------------------------ act
    cache: dict = {}

    def act_node(state: PipelineState) -> PipelineState:
        if ctx.mode == "jobs":
            ctx.metrics["act"] = {"skipped": "mode=jobs"}
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
        apps = {(a["job_id"], a["route"]): a for a in repo.list_applications(2000)}
        cache["tasks"] = repo.list_tasks(None, 500)
        ready = repo.list_jobs([S.READY], 500)
        for r in ready:
            if out_of_time("sending/applying"):
                break
            if ctx.job_id and not str(r["id"]).startswith(ctx.job_id):
                continue
            email_app = apps.get((r["id"], "email"))
            browser_app = apps.get((r["id"], "browser"))
            if email_app and not email_app.get("email_sent_at"):
                _act_email(r, email_app)
            elif browser_app and not browser_app.get("submitted_at"):
                _act_browser(r, browser_app)
        ctx.metrics["act"] = {"ready": len(ready), "emails_sent": ctx.summary.emails_sent,
                              "submitted": ctx.summary.browser_submitted, "waiting": ctx.summary.waiting_user,
                              "failed": ctx.summary.failed}
        return {}

    def _tasks() -> list[dict]:
        if "tasks" not in cache:
            cache["tasks"] = repo.list_tasks(None, 500)
        return cache["tasks"]

    def _task_for(job_id: str, type_: str) -> dict:
        for t in _tasks():
            if t.get("job_id") == job_id and t["type"] == type_ and t["status"] in ("queued", "failed"):
                repo.update_task(t["id"], status="running")
                return repo.get_task(t["id"])  # type: ignore[return-value]
        task = repo.create_task(job_id, type_, None, status="running")
        _tasks().append(task)
        return task

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
            if "|would_send" in (app.get("notes") or ""):
                return  # already rehearsed in an earlier dry run: nothing new to log
            log.info("WOULD SEND email to %s for %s @ %s (contact source %s, %s)", contact["email"], r["title"],
                     r["company"], contact["source_url"], contact["confidence"])
            repo.add_event("info", "would_send_email", r["id"], {"to": contact["email"], "subject": app.get("email_subject")})
            repo.update_application(app["id"], notes=f"{app.get('notes') or ''}|would_send"[:500])
            return
        if is_killed(repo):  # re-checked right before every real send
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
        stats.bump("emails_sent")
        ctx.summary.emails_sent += 1

    def _approved_answers(job_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for t in _tasks():
            p = t.get("payload") or {}
            if t.get("job_id") == job_id and p.get("approved"):
                for q in p.get("questions", []):
                    if q.get("proposed"):
                        out[q["label"]] = q["proposed"]
        return out

    def _approved_answers_flag(job_id: str) -> bool:
        return any(t.get("job_id") == job_id and (t.get("payload") or {}).get("approved") for t in _tasks())

    def _act_browser(r: dict, app: dict) -> None:
        if st.dry_run and (app.get("notes") or "").startswith("dry_run"):
            return  # already rehearsed; nothing new to learn
        if not st.dry_run and is_killed(repo):
            return
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
            stats.bump("browser_submitted")
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
        stats.bump("waiting_user")
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
        stats.bump("failed")
        ctx.summary.failed += 1
        if notify:
            lines = ["⚠️ Application failed (will be in the retry queue)", f"Reason: {err[:300]}",
                     f"Company: {r['company']}", f"Role: {r['title']}", f"URL: {r['url']}", "",
                     f"Retry: /retry  Skip: /skip {str(r['id'])[:8]}"]
            ctx.notify("\n".join(lines))
            ctx.notify_photo(shot, f"{r['company']} – {r['title']}")

    def load_ready(state: PipelineState) -> PipelineState:
        return {}

    def route_start(state: PipelineState) -> str:
        return "load_ready" if ctx.mode == "apply" else "discover"

    g = StateGraph(PipelineState)
    for name, fn in [("discover", discover), ("normalize", normalize_node), ("dedupe", dedupe_node),
                     ("cheap_filter", cheap_filter_node), ("ai_match", ai_match_node), ("contact", contact_node),
                     ("prepare", prepare_node), ("act", act_node)]:
        g.add_node(name, stage(name, fn))
    g.add_node("load_ready", load_ready)
    g.add_conditional_edges(START, route_start, {"load_ready": "load_ready", "discover": "discover"})
    g.add_edge("load_ready", "act")
    for a, b in [("discover", "normalize"), ("normalize", "dedupe"), ("dedupe", "cheap_filter"),
                 ("cheap_filter", "ai_match"), ("ai_match", "contact"), ("contact", "prepare"),
                 ("prepare", "act"), ("act", END)]:
        g.add_edge(a, b)
    return g.compile()
