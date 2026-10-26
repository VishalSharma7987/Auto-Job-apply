"""Pipeline context + entry point. The node logic lives in graph.py."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jobagent.apply.playwright_runner import DRY_RUN_STOPPED, WAITING_USER, ApplyOutcome, apply_to_job
from jobagent.budget import RunBudget, RunInterrupted
from jobagent.config import Settings
from jobagent.contacts.finder import ContactFinder
from jobagent.db.base import Repository
from jobagent.discovery.base import SourceAdapter
from jobagent.discovery.fake import FAKE_RECRUITING_EMAILS
from jobagent.discovery.registry import build_adapters
from jobagent.email.gmail_smtp import GmailSender
from jobagent.llm.client import LLM, make_llm
from jobagent.models import Contact, Job
from jobagent.pipeline.state import RunSummary
from jobagent.profile import Profile, load_profile, resume_path
from jobagent.telegram.client import TelegramClient
from jobagent.utils.dates import today_utc

log = logging.getLogger(__name__)

ApplyFn = Callable[..., ApplyOutcome]


class FakeContactFinder:
    """Offline stand-in: returns the 'published' recruiting emails from the fake dataset."""

    def find(self, job: Job) -> Contact | None:
        hit = FAKE_RECRUITING_EMAILS.get(job.company)
        if not hit:
            return None
        email, src, conf = hit
        return Contact(company=job.company, email=email, source_url=src, confidence=conf)  # type: ignore[arg-type]


def fake_apply(job: dict, cover_letter: str, profile: Profile, settings: Settings, llm, approved=None) -> ApplyOutcome:
    if job["company"] == "Tessellate":
        return ApplyOutcome(WAITING_USER, "captcha detected (simulated)", None, application_url=job["url"])
    return ApplyOutcome(DRY_RUN_STOPPED, "fake mode: form not opened", None, application_url=job["url"])


class StatsBuffer:
    """Daily-stats counters kept in memory and written in ONE round trip per stage.

    Writing them per job (2-4 Supabase calls each, ~0.5 s) made a run over ~7,600 discovered jobs take ~2 hours -
    the reason the GitHub run hit its 30-minute timeout (docs/PERF_NOTES.md)."""

    def __init__(self, repo: Repository, day: date):
        self.repo, self.day = repo, day
        self.counts: Counter = Counter()
        self.reasons: Counter = Counter()

    def bump(self, field_name: str, n: int = 1) -> None:
        if n:
            self.counts[field_name] += n

    def skip(self, reason: str, n: int = 1) -> None:
        if n:
            self.counts["skipped"] += n
            self.reasons[reason] += n

    def flush(self) -> None:
        if self.counts:
            counts, self.counts = dict(self.counts), Counter()
            self.repo.bump_stats(self.day, counts)
        if self.reasons:
            reasons, self.reasons = dict(self.reasons), Counter()
            self.repo.add_skip_reasons(self.day, reasons)


@dataclass
class Ctx:
    settings: Settings
    repo: Repository
    profile: Profile
    llm: LLM
    adapters: list[SourceAdapter]
    finder: object
    sender: GmailSender
    tg: TelegramClient
    apply_fn: ApplyFn = apply_to_job
    mode: str = "full"
    job_id: str | None = None
    day: date = field(default_factory=today_utc)
    summary: RunSummary = field(default_factory=RunSummary)
    budget: RunBudget = field(default_factory=lambda: RunBudget(1200))
    stats: StatsBuffer | None = None
    metrics: dict = field(default_factory=dict)  # stage -> counts, logged at the end of each stage
    llm_down: str = ""  # reason, once the LLM circuit breaker tripped for this run

    def __post_init__(self) -> None:
        if self.stats is None:
            self.stats = StatsBuffer(self.repo, self.day)

    def mark_partial(self, note: str) -> None:
        self.summary.partial = True
        if note not in self.summary.notes:
            self.summary.notes.append(note)

    @property
    def resume(self) -> Path:
        return resume_path(self.settings)

    def notify(self, text: str) -> None:
        try:
            self.tg.send_message(text)
        except Exception as e:  # noqa: BLE001 - notifications must never crash the run
            log.warning("telegram notify failed: %s", e)

    def notify_photo(self, path: str | None, caption: str) -> None:
        if not path:
            return
        try:
            self.tg.send_photo(path, caption)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram photo failed: %s", e)


def build_ctx(settings: Settings, repo: Repository, tg: TelegramClient | None = None, llm: LLM | None = None,
              mode: str = "full", profile: Profile | None = None, budget: RunBudget | None = None) -> Ctx:
    if settings.fake_mode and not settings.dry_run:
        log.warning("FAKE_MODE forces DRY_RUN=true")
        settings = settings.model_copy(update={"dry_run": True})
    profile = profile or load_profile(settings)
    tg = tg or TelegramClient(settings.telegram_bot_token, settings.telegram_allowed_chat_id)
    if settings.fake_mode:
        finder: object = FakeContactFinder()
        apply_fn: ApplyFn = fake_apply
    else:
        finder, apply_fn = ContactFinder(), apply_to_job
    return Ctx(
        settings=settings, repo=repo, profile=profile, llm=llm or make_llm(settings),
        adapters=build_adapters(settings), finder=finder,
        sender=GmailSender(settings.gmail_address, settings.gmail_app_password, dry_run=settings.dry_run),
        tg=tg, apply_fn=apply_fn, mode=mode, budget=budget or RunBudget(settings.run_time_budget_seconds))


def run_pipeline(ctx: Ctx) -> RunSummary:
    """Run the graph; whatever happens (success, time budget, SIGTERM, crash) the report is sent in `finally`."""
    from jobagent.pipeline.graph import build_graph

    try:
        build_graph(ctx).invoke({})
    except RunInterrupted as e:
        ctx.summary.interrupted = e.signal_name
        e.reported = True
        raise
    except KeyboardInterrupt:
        ctx.summary.interrupted = "SIGINT"
        raise
    except BaseException as e:
        ctx.summary.error = f"{type(e).__name__}: {str(e)[:200]}"
        raise
    finally:
        finalize(ctx)
    return ctx.summary


def finalize(ctx: Ctx) -> None:
    """Flush stats, record the run, print + send the report. Every step is guarded: this must never raise."""
    from jobagent.telegram.reports import format_summary, todays_selected

    s = ctx.summary
    ctx.budget.finishing = True  # the hard-deadline backstop must not cut the report short
    s.duration = ctx.budget.elapsed()
    if ctx.budget.exceeded():
        s.partial = True
    if getattr(ctx.llm, "switched_from", None):
        s.notes.append(f"LLM model {ctx.llm.switched_from} unavailable - used {ctx.llm.model} (update LLM_MODEL)")
    if ctx.llm_down:
        s.notes.append(ctx.llm_down)
    t0 = time.monotonic()
    try:
        ctx.stats.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("could not flush stats: %s", e)
    try:
        s.selected_jobs = todays_selected(ctx.repo)
    except Exception as e:  # noqa: BLE001
        log.warning("could not list today's selected jobs: %s", e)
    try:
        ctx.repo.add_event("info", "run_finished", None, {"mode": ctx.mode, "duration_s": round(s.duration), "summary": {
            k: getattr(s, k) for k in ("scanned", "qualified", "selected", "emails_sent", "browser_submitted",
                                       "waiting_user", "failed", "skipped", "partial", "interrupted", "error")}})
    except Exception as e:  # noqa: BLE001
        log.warning("could not record run event: %s", e)
    try:
        try:
            stats = ctx.repo.get_stats(ctx.day)
        except Exception:  # noqa: BLE001 - DB unreachable: report this run's own counters
            stats = {k: getattr(s, k) for k in ("scanned", "qualified", "selected", "emails_sent",
                                                "browser_submitted", "waiting_user", "failed", "skipped")}
        text = format_summary(s, ctx.day, stats, ctx.settings.dry_run, ctx.settings.max_applications_per_day)
    except Exception as e:  # noqa: BLE001
        text = (f"❌ Run {'crashed: ' + s.error if s.error else 'ended'} - the report could not be built "
                f"({type(e).__name__}: {str(e)[:150]}).")
    if s.interrupted:
        ctx.notify(f"🛑 Run interrupted ({s.interrupted}) after {s.duration:.0f}s - partial results are saved; "
                   "the report follows.")
    print(text, flush=True)
    ctx.notify(text)
    log.info("run finished in %.0fs (report sent in %.1fs) partial=%s interrupted=%s error=%s", s.duration,
             time.monotonic() - t0, s.partial, s.interrupted or "-", s.error or "-")
