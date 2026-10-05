"""Map a form field (by its label) to a value from the verified profile.

Unknown required questions go to the LLM, which may answer ONLY from the profile. Anything consequential
(legal, visa, salary, relocation, demographics, consent) or with confidence < 0.8 becomes WAITING_USER
unless it is pre-approved in profile.yaml legal_prefs or approved by the user via /approve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobagent.llm.client import LLM
from jobagent.llm.prompts import form_prompt
from jobagent.models import FormAnswer
from jobagent.profile import Profile

CONFIDENCE_MIN = 0.8

CONSEQUENTIAL_RE = re.compile(
    r"sponsor|visa|work authori[sz]ation|authori[sz]ed to work|legally (allowed|authori)|right to work|salary|compensation|"
    r"ctc|expected pay|relocat|notice period|criminal|convicted|felony|disabilit|veteran|gender|race|ethnic|hispanic|"
    r"pronoun|sexual orientation|transgender|background check|drug|certify|i agree|i accept|consent|terms|privacy|"
    r"non[- ]?compete|security clearance|citizenship", re.I)


@dataclass
class FieldSpec:
    selector: str
    tag: str  # input | textarea | select
    type: str  # text, email, tel, file, checkbox, radio, select-one, textarea ...
    label: str
    required: bool = False
    options: list[str] = field(default_factory=list)
    name: str = ""


@dataclass
class Decision:
    action: str  # fill | select | check | upload | skip | ask_user
    value: str = ""
    reason: str = ""
    proposed: str = ""  # for ask_user: what we would have answered


def _has(label: str, pattern: str) -> bool:
    return re.search(pattern, label, re.I) is not None


def _pick_option(options: list[str], answer: str) -> str | None:
    a = answer.strip().lower()
    for o in options:
        if o.strip().lower() == a:
            return o
    for o in options:
        if a and (a in o.lower() or o.lower() in a) and o.strip():
            return o
    return None


def _yes_no(options: list[str], yes: bool) -> str | None:
    want = "yes" if yes else "no"
    for o in options:
        if o.strip().lower().startswith(want):
            return o
    return None


def _legal_decision(f: FieldSpec, profile: Profile, approved: dict[str, str]) -> Decision | None:
    """Decide consequential questions strictly from legal_prefs / explicit approval. None = not legal."""
    label = f.label
    if not CONSEQUENTIAL_RE.search(label):
        return None
    if label in approved:
        return Decision("select" if f.options else "fill", approved[label], "approved by user")
    lp = profile.legal_prefs
    if _has(label, r"authori[sz]ed to work|right to work|work authori[sz]ation|legally (allowed|authori)") \
            and _has(label, r"india|\bin the country\b|this country") and lp.work_authorization_india is True:
        v = _yes_no(f.options, True) if f.options else "Yes"
        if v:
            return Decision("select" if f.options else "fill", v, "legal_prefs.work_authorization_india")
    if _has(label, r"sponsor|visa") and lp.needs_sponsorship in (True, False):
        v = _yes_no(f.options, bool(lp.needs_sponsorship)) if f.options else ("Yes" if lp.needs_sponsorship else "No")
        if v:
            return Decision("select" if f.options else "fill", v, "legal_prefs.needs_sponsorship")
    if _has(label, r"relocat") and lp.relocation in (True, False):
        v = _yes_no(f.options, bool(lp.relocation)) if f.options else ("Yes" if lp.relocation else "No")
        if v:
            return Decision("select" if f.options else "fill", v, "legal_prefs.relocation")
    if _has(label, r"gender|race|ethnic|hispanic|veteran|disabilit|pronoun|sexual|transgender") and f.options:
        opt = next((o for o in f.options if re.search(r"decline|prefer not|do not wish|don.t wish", o, re.I)), None)
        if opt:
            return Decision("select", opt, "demographic: declined to self-identify")
    if f.type == "checkbox" and _has(label, r"agree|accept|consent|terms|privacy|certify|acknowledge") \
            and getattr(lp, "accept_terms", False) is True:
        return Decision("check", "true", "legal_prefs.accept_terms")
    return Decision("ask_user", reason=f"consequential question: {label[:120]}")


def decide(f: FieldSpec, profile: Profile, cover_letter: str, llm: LLM | None,
           approved: dict[str, str] | None = None, resume_available: bool = True) -> Decision:
    approved = approved or {}
    label = (f.label or f.name or "").strip()
    if f.type in ("hidden", "submit", "button", "image", "reset"):
        return Decision("skip")

    if f.type == "file":
        if _has(label, r"resume|cv|curriculum"):
            return Decision("upload", "resume") if resume_available else Decision("ask_user", reason="resume PDF missing")
        if _has(label, r"cover"):
            return Decision("skip", reason="cover letter file optional")
        return Decision("ask_user", reason=f"unknown file upload: {label[:100]}") if f.required else Decision("skip")

    # legal / consequential questions are never answered by guessing
    legal = _legal_decision(f, profile, approved)
    if legal is not None:
        return legal

    known = [
        (r"^(first|given) name|first name", profile.first_name),
        (r"^(last|family|sur) ?name|last name", profile.last_name),
        (r"full name|^name\b|your name|legal name", profile.name),
        (r"e-?mail", profile.email),
        (r"phone|mobile|contact number|telephone", profile.phone),
        (r"linkedin", profile.linkedin),
        (r"github", profile.github),
        (r"portfolio|personal (web)?site|website|^url$|other (web)?site", profile.portfolio or profile.github),
        (r"current (location|city)|^location|city|where are you (based|located)", profile.current_location),
        (r"cover letter|why (do you want|are you interested)|additional information|anything else|message to",
         cover_letter),
    ]
    for pat, value in known:
        if _has(label, pat):
            if not value:
                return (Decision("ask_user", reason=f"profile has no value for '{label[:60]}'")
                        if f.required else Decision("skip", reason="optional and no value"))
            if f.options:
                opt = _pick_option(f.options, value)
                return Decision("select", opt, "profile") if opt else Decision("skip", reason="no matching option")
            return Decision("fill", value, "profile")

    if _has(label, r"how did you (hear|find)|source"):
        opt = _pick_option(f.options, "job board") or _pick_option(f.options, "other") if f.options else None
        return Decision("select", opt, "process fact") if opt else (
            Decision("fill", "Company careers page / job board", "process fact") if not f.options else Decision("skip"))

    if not f.required:
        return Decision("skip", reason="optional unknown field")
    if f.type == "checkbox":
        return Decision("ask_user", reason=f"required checkbox: {label[:100]}")

    # required + unknown: LLM, profile-only
    if llm is None:
        return Decision("ask_user", reason=f"required question, no LLM: {label[:100]}")
    system, user = form_prompt(label, f.options or None, profile)
    try:
        ans = llm.complete_json(system, user, FormAnswer, temperature=0.0)
    except Exception as e:  # noqa: BLE001 - includes QuotaExceeded handled upstream? re-raise quota only
        from jobagent.llm.client import QuotaExceeded

        if isinstance(e, QuotaExceeded):
            raise
        return Decision("ask_user", reason=f"LLM failed on '{label[:80]}': {e}")
    if ans.consequential or ans.confidence < CONFIDENCE_MIN or not ans.answer.strip():
        return Decision("ask_user", reason=f"low confidence/consequential: {label[:100]}", proposed=ans.answer)
    if f.options:
        opt = _pick_option(f.options, ans.answer)
        if not opt:
            return Decision("ask_user", reason=f"answer not among options: {label[:100]}", proposed=ans.answer)
        return Decision("select", opt, f"llm conf={ans.confidence:.2f}")
    return Decision("fill", ans.answer.strip(), f"llm conf={ans.confidence:.2f}")
