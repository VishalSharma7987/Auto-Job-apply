"""Test helpers: assemble a pipeline Ctx with injected fakes (no network)."""

from __future__ import annotations

from jobagent.apply.playwright_runner import DRY_RUN_STOPPED, SUBMITTED, ApplyOutcome
from jobagent.discovery.fake import FakeAdapter
from jobagent.email.gmail_smtp import GmailSender
from jobagent.llm.fake import FakeLLM
from jobagent.pipeline.runner import Ctx, FakeContactFinder


class FakeSMTP:
    instances: list = []

    def __init__(self, fail_times_box: list | None = None):
        self.box = fail_times_box

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, msg):
        if self.box and self.box[0] > 0:
            self.box[0] -= 1
            raise OSError("simulated smtp outage")
        FakeSMTP.instances.append(msg)


class CountingApply:
    def __init__(self, status: str = SUBMITTED):
        self.status, self.calls = status, []

    def __call__(self, job, cover_letter, profile, settings, llm, approved=None):
        self.calls.append(job["id"])
        if self.status == DRY_RUN_STOPPED:
            return ApplyOutcome(DRY_RUN_STOPPED, "dry", None)
        return ApplyOutcome(self.status, "ok", None, application_url=job["url"])


def make_ctx(settings, repo, profile, tg, mode="full", smtp_fail_box=None, apply_fn=None, llm=None) -> Ctx:
    FakeSMTP.instances = []
    sender = GmailSender(settings.gmail_address, settings.gmail_app_password, dry_run=settings.dry_run,
                         smtp_factory=lambda: FakeSMTP(smtp_fail_box))
    return Ctx(settings=settings, repo=repo, profile=profile, llm=llm or FakeLLM(), adapters=[FakeAdapter()],
               finder=FakeContactFinder(), sender=sender, tg=tg, apply_fn=apply_fn or CountingApply(), mode=mode)
