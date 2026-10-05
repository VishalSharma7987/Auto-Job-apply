from __future__ import annotations

import pytest

from jobagent.apply.detectors import (
    detect_all,
    detect_captcha,
    detect_legal_checkboxes,
    detect_login_wall,
    detect_otp,
)
from jobagent.apply.field_mapper import Decision, FieldSpec, decide
from jobagent.models import FormAnswer


# ---------------------------------------------------------------- detectors
def test_captcha_detected():
    assert detect_captcha('<iframe src="https://www.google.com/recaptcha/api2/bframe"></iframe>').kind == "captcha"
    assert detect_captcha('<div class="h-captcha" data-sitekey="x"></div>')
    assert detect_captcha('<div class="cf-turnstile"></div>')
    assert detect_captcha('<iframe src="https://challenges.cloudflare.com/cdn-cgi/x"></iframe>')
    assert detect_captcha("<form><input name=email></form>") is None


def test_otp_detected():
    assert detect_otp('<input autocomplete="one-time-code">').kind == "otp"
    assert detect_otp("<p>Enter the verification code we sent</p><input type=text>")
    assert detect_otp("<p>Your name</p><input type=text>") is None


def test_login_wall_detected():
    assert detect_login_wall("<h1>Sign in</h1><input type=password>").kind == "login"
    assert detect_login_wall("<p>hi</p>", "https://x.com/login?next=/apply")
    assert detect_login_wall("<form><input type=file><textarea></textarea></form>", "https://x.com/apply") is None


def test_detect_all_blocked_page():
    kinds = {d.kind for d in detect_all("<h1>Access denied</h1><p>unusual traffic</p>")}
    assert "blocked" in kinds


def test_legal_checkboxes():
    html = '<label><input type="checkbox"> I agree to the Terms of Service and Privacy Policy</label>'
    found = detect_legal_checkboxes(html)
    assert found and found[0].kind == "legal"
    assert detect_legal_checkboxes('<label><input type="checkbox"> Subscribe to newsletter</label>') == []


# ---------------------------------------------------------------- field mapper
class LLM:
    def __init__(self, ans: FormAnswer):
        self.ans, self.calls = ans, 0

    def complete_json(self, *a, **k):
        self.calls += 1
        return self.ans


def f(label, type="text", required=True, options=None):
    return FieldSpec(selector="1", tag="input", type=type, label=label, required=required, options=options or [])


@pytest.mark.parametrize("label,expected", [
    ("First Name", "Vishal"), ("Last name", "Sharma"), ("Full name", "Vishal Sharma"),
    ("Email *", "candidate@example.com"), ("Phone", "+910000000000"), ("GitHub URL", "https://github.com/example"),
    ("Current location", "Pune, India"),
])
def test_known_fields_filled_from_profile(profile, label, expected):
    d = decide(f(label), profile, "COVER", None)
    assert d.action == "fill" and d.value == expected


def test_cover_letter_filled_with_email_body(profile):
    assert decide(f("Cover letter", "textarea"), profile, "MY COVER", None).value == "MY COVER"


def test_resume_upload(profile):
    assert decide(f("Resume/CV", "file"), profile, "", None).action == "upload"
    assert decide(f("Resume/CV", "file"), profile, "", None, resume_available=False).action == "ask_user"


def test_missing_profile_value_for_required_field_asks_user(profile):
    profile.linkedin = ""
    assert decide(f("LinkedIn Profile", required=True), profile, "", None).action == "ask_user"
    assert decide(f("LinkedIn Profile", required=False), profile, "", None).action == "skip"


def test_work_authorization_india_pre_approved(profile):
    d = decide(f("Are you legally authorized to work in India?", "select-one", options=["Yes", "No"]), profile, "", None)
    assert d.action == "select" and d.value == "Yes"


@pytest.mark.parametrize("label", ["Will you now or in the future require visa sponsorship?", "Expected salary",
                                   "Are you willing to relocate?", "Notice period", "Have you been convicted of a crime?"])
def test_consequential_questions_wait_for_user(profile, label):
    llm = LLM(FormAnswer(answer="Yes", confidence=0.99, consequential=False))
    d = decide(f(label), profile, "", llm)
    assert d.action == "ask_user" and llm.calls == 0  # never even asks the LLM


def test_user_approval_overrides(profile):
    label = "Expected salary"
    d = decide(f(label), profile, "", None, approved={label: "6 LPA"})
    assert d.action == "fill" and d.value == "6 LPA"


def test_demographic_declines_when_option_exists(profile):
    d = decide(f("Gender", "select-one", options=["Male", "Female", "Decline to self-identify"]), profile, "", None)
    assert d.action == "select" and d.value == "Decline to self-identify"


def test_legal_checkbox_needs_user_unless_pre_approved(profile):
    box = f("I agree to the privacy policy", "checkbox")
    assert decide(box, profile, "", None).action == "ask_user"
    profile.legal_prefs.accept_terms = True
    assert decide(box, profile, "", None).action == "check"


def test_unknown_required_question_uses_llm_only_when_confident(profile):
    q = f("How many years of Python experience do you have?")
    ok = decide(q, profile, "", LLM(FormAnswer(answer="1", confidence=0.9)))
    assert ok.action == "fill" and ok.value == "1"
    low = decide(q, profile, "", LLM(FormAnswer(answer="5", confidence=0.5)))
    assert low.action == "ask_user" and low.proposed == "5"
    conseq = decide(q, profile, "", LLM(FormAnswer(answer="1", confidence=0.99, consequential=True)))
    assert conseq.action == "ask_user"


def test_llm_answer_must_be_one_of_options(profile):
    q = f("Primary language?", "select-one", options=["Java", "Python"])
    assert decide(q, profile, "", LLM(FormAnswer(answer="Rust", confidence=0.95))).action == "ask_user"
    d = decide(q, profile, "", LLM(FormAnswer(answer="python", confidence=0.95)))
    assert d.action == "select" and d.value == "Python"


def test_optional_unknown_fields_skipped_without_llm(profile):
    llm = LLM(FormAnswer(answer="x", confidence=1))
    assert decide(f("Favourite colour", required=False), profile, "", llm).action == "skip" and llm.calls == 0


def test_decision_type():
    assert isinstance(Decision("skip"), Decision)
