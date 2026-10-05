"""Pipeline context + entry point. The node logic lives in graph.py."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jobagent.apply.playwright_runner import DRY_RUN_STOPPED, WAITING_USER, ApplyOutcome, apply_to_job
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
              mode: str = "full", profile: Profile | None = None) -> Ctx:
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
        tg=tg, apply_fn=apply_fn, mode=mode)


def run_pipeline(ctx: Ctx) -> RunSummary:
    from jobagent.pipeline.graph import build_graph

    graph = build_graph(ctx)
    graph.invoke({})
    return ctx.summary
