"""Email generation guard (no unverified claims) + Gmail sender DRY_RUN guard."""

from __future__ import annotations

import smtplib

import pytest
from conftest import make_job

from jobagent.email.generator import MAX_WORDS, generate_email, template_email, unverified_claims
from jobagent.email.gmail_smtp import GmailSender, SendBlocked
from jobagent.models import EmailDraft


class ScriptedLLM:
    """Returns queued drafts; records prompts."""

    def __init__(self, *bodies: str):
        self.bodies = list(bodies)
        self.prompts: list[str] = []

    def complete_json(self, system, user, schema, temperature=0.0):
        self.prompts.append(user)
        return EmailDraft(subject="x", body=self.bodies.pop(0))


GOOD = "Hello,\nI built a RAG chatbot using Python, LangChain and FaISS.\nResume attached.\nVishal Sharma"
BAD = "Hello,\nI have 3 years of Kubernetes and Terraform experience with Rust.\nVishal Sharma"


def test_unverified_claims_detected(profile):
    assert set(unverified_claims(BAD, profile)) >= {"kubernetes", "terraform", "rust"}
    assert unverified_claims(GOOD, profile) == []


def test_aliases_and_substrings_do_not_false_positive(profile):
    assert unverified_claims("I enjoy Go-getter attitude and going deep; Node and Postgres are familiar.", profile) == []


def test_clean_draft_accepted_first_try(profile):
    llm = ScriptedLLM(GOOD)
    d, how = generate_email(make_job(), profile, llm)
    assert how == "llm" and d.subject == "Application – AI Developer | Vishal Sharma"
    assert "https://github.com/example" in d.body  # links only because env provided them


def test_bad_draft_regenerated_once_with_feedback(profile):
    llm = ScriptedLLM(BAD, GOOD)
    d, how = generate_email(make_job(), profile, llm)
    assert how == "llm_retry" and "kubernetes" in llm.prompts[1].lower()  # feedback names the offending claims
    assert unverified_claims(d.body, profile) == []


def test_two_bad_drafts_fall_back_to_template(profile):
    llm = ScriptedLLM(BAD, BAD)
    d, how = generate_email(make_job(), profile, llm)
    assert how == "template" and unverified_claims(d.body, profile) == []


def test_overlong_draft_rejected(profile):
    long = "word " * (MAX_WORDS + 40)
    d, how = generate_email(make_job(), profile, ScriptedLLM(long, long))
    assert how == "template"


def test_template_fallback_only_uses_profile_facts(profile):
    d = template_email(make_job(), profile, {})
    assert unverified_claims(d.body, profile) == []
    assert "Sample RAG Chatbot" in d.body and len(d.body.split()) <= MAX_WORDS


def test_no_links_when_not_configured(profile):
    profile.github = ""
    d = template_email(make_job(), profile, {})
    assert "http" not in d.body


# ---------------------------------------------------------------- gmail sender
def test_dry_run_never_touches_smtp(resume, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SMTP must not be used in DRY_RUN")

    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    s = GmailSender("me@example.com", "pw", dry_run=True)
    assert s.send("careers@acme.com", "S", "B", resume) is False and s.sent_count == 0


class FakeSMTP:
    sent: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_live_send_attaches_resume(resume):
    FakeSMTP.sent = []
    s = GmailSender("me@example.com", "pw", dry_run=False, smtp_factory=FakeSMTP)
    assert s.send("careers@acme.com", "Subject", "Body", resume) is True
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "careers@acme.com" and msg["Subject"] == "Subject"
    atts = [p for p in msg.iter_attachments()]
    assert len(atts) == 1 and atts[0].get_content_type() == "application/pdf" and atts[0].get_content().startswith(b"%PDF")


def test_live_send_refuses_without_resume_or_credentials(tmp_path, resume):
    with pytest.raises(SendBlocked):
        GmailSender("me@example.com", "pw", dry_run=False, smtp_factory=FakeSMTP).send("a@b.com", "s", "b", tmp_path / "nope.pdf")
    with pytest.raises(SendBlocked):
        GmailSender(None, None, dry_run=False, smtp_factory=FakeSMTP).send("a@b.com", "s", "b", resume)
