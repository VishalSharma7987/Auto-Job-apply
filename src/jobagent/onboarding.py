"""Telegram onboarding (/setup): collects personal details one message at a time.

Stateless between worker runs: the whole state lives in profile.onboarding_state (jsonb), so a reply is handled correctly
whichever run (cron or relay-triggered) picks it up. Phone numbers are never logged.

State:  {"step": "phone|linkedin|github|portfolio|location|confirm", "answers": {...}, "current": {...}}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from jobagent.config import Settings
from jobagent.db.base import Repository

STEPS = ["phone", "linkedin", "github", "portfolio", "location", "confirm"]
FIELDS = ["phone", "linkedin", "github", "portfolio", "location"]
DB_COLUMNS = {"phone": "phone", "linkedin": "linkedin_url", "github": "github_url", "portfolio": "portfolio_url",
              "location": "location"}
SKIPPABLE = {"linkedin", "github", "portfolio"}

PROMPTS = {
    "phone": "1/5 - Your phone number with country code (e.g. +919876543210):",
    "linkedin": "2/5 - Your LinkedIn profile URL (e.g. https://www.linkedin.com/in/yourname) or 'skip':",
    "github": "3/5 - Your GitHub profile URL (e.g. https://github.com/yourname) or 'skip':",
    "portfolio": "4/5 - Your portfolio / website URL, or 'skip':",
    "location": "5/5 - Your current location (city, e.g. Pune):",
}

PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
INDIA_MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")
LOCATION_RE = re.compile(r"^[A-Za-z][A-Za-z .,'-]{1,58}$")
KEEP_WORDS = {"keep", "same", "ok", "okay", "current"}
YES = {"yes", "y", "confirm", "ok", "okay"}
NO = {"no", "n"}


@dataclass
class Step:
    state: dict | None  # new state to persist (None = clear)
    reply: str
    save: dict | None = None  # DB columns to write when the flow completes


# ---------------------------------------------------------------- validators (return normalised value or None)
def normalize_phone(text: str) -> str | None:
    t = re.sub(r"[\s\-().]", "", text.strip())
    if PHONE_RE.match(t):
        return t
    t2 = t[2:] if t.startswith("91") and len(t) == 12 else t.lstrip("0") if len(t) == 11 and t.startswith("0") else t
    if INDIA_MOBILE_RE.match(t2):
        return "+91" + t2
    return None


def _url(text: str) -> tuple[str, str] | None:
    t = text.strip().strip("<>")
    if not re.match(r"^https?://", t, re.I):
        t = "https://" + t
    p = urlparse(t)
    host = (p.hostname or "").lower()
    if not host or "." not in host or " " in t:
        return None
    return host.removeprefix("www."), p.path.strip("/")


def normalize_linkedin(text: str) -> str | None:
    u = _url(text)
    if not u:
        return None
    host, path = u
    parts = path.split("/")
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and len(parts) >= 2 and parts[0] in ("in", "pub") and parts[1]:
        return f"https://www.linkedin.com/{parts[0]}/{parts[1]}"
    return None


def normalize_github(text: str) -> str | None:
    u = _url(text)
    if not u:
        return None
    host, path = u
    parts = [x for x in path.split("/") if x]
    if host == "github.com" and len(parts) == 1 and re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", parts[0]):
        return f"https://github.com/{parts[0]}"
    return None


def normalize_portfolio(text: str) -> str | None:
    u = _url(text)
    if not u:
        return None
    t = text.strip().strip("<>")
    return t if re.match(r"^https?://", t, re.I) else "https://" + t


def normalize_location(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text.strip())
    return t if LOCATION_RE.match(t) else None


VALIDATORS = {"phone": normalize_phone, "linkedin": normalize_linkedin, "github": normalize_github,
              "portfolio": normalize_portfolio, "location": normalize_location}
ERRORS = {
    "phone": "That doesn't look like a phone number. Use the international format, e.g. +919876543210.",
    "linkedin": "That doesn't look like a LinkedIn profile URL (https://www.linkedin.com/in/yourname). Try again or 'skip'.",
    "github": "That doesn't look like a GitHub profile URL (https://github.com/yourname). Try again or 'skip'.",
    "portfolio": "That doesn't look like a URL (https://...). Try again or 'skip'.",
    "location": "Please send a city name (letters only), e.g. Pune.",
}


# ---------------------------------------------------------------- state machine
def _hint(step: str, current: dict) -> str:
    cur = current.get(step)
    return f"\n(current: {cur} - reply 'keep' to keep it)" if cur else ""


def start(current: dict) -> Step:
    state = {"step": "phone", "answers": {}, "current": {k: v for k, v in current.items() if v}}
    return Step(state, "Let's set up your details. Send 'cancel' at any time to stop.\n\n" + PROMPTS["phone"]
                + _hint("phone", state["current"]))


def summary(answers: dict) -> str:
    def show(k: str) -> str:
        return answers.get(k) or "(skipped)"

    return ("Please check your details:\n"
            f"Phone: {show('phone')}\nLinkedIn: {show('linkedin')}\nGitHub: {show('github')}\n"
            f"Portfolio: {show('portfolio')}\nLocation: {show('location')}\n\nConfirm? yes / no")


def advance(state: dict, text: str) -> Step:
    t = (text or "").strip()
    low = t.lower()
    if low in ("cancel", "/cancel", "stop", "abort"):
        return Step(None, "Setup cancelled. Nothing was changed. Send /setup to start again.")
    step = state.get("step")
    answers = dict(state.get("answers") or {})
    current = state.get("current") or {}

    if step == "confirm":
        if low in YES:
            save = {DB_COLUMNS[k]: (answers.get(k) or None) for k in FIELDS}
            return Step(None, "✅ Details saved. Send /profile to review them.", save)
        if low in NO:
            fresh = start(current)
            fresh.reply = "OK, let's go through them again.\n\n" + PROMPTS["phone"] + _hint("phone", current)
            return fresh
        return Step(state, "Please answer yes or no.\n\n" + summary(answers))

    if step not in FIELDS:
        return Step(None, "Setup state was invalid and has been reset. Send /setup to start again.")

    if low in KEEP_WORDS and current.get(step):
        value: str | None = current[step]
    elif low == "skip" and step in SKIPPABLE:
        value = ""
    else:
        value = VALIDATORS[step](t)
        if value is None:
            return Step(state, ERRORS[step])
    answers[step] = value
    nxt = STEPS[STEPS.index(step) + 1]
    new_state = {"step": nxt, "answers": answers, "current": current}
    if nxt == "confirm":
        return Step(new_state, summary(answers))
    return Step(new_state, PROMPTS[nxt] + _hint(nxt, current))


# ---------------------------------------------------------------- persistence glue
def current_details(row: dict | None, settings: Settings) -> dict:
    """DB first, env as fallback."""
    row = row or {}
    return {
        "phone": row.get("phone") or settings.candidate_phone or "",
        "linkedin": row.get("linkedin_url") or settings.linkedin_url or "",
        "github": row.get("github_url") or settings.github_url or "",
        "portfolio": row.get("portfolio_url") or settings.portfolio_url or "",
        "location": row.get("location") or "",
    }


def begin(repo: Repository, settings: Settings) -> str:
    step = start(current_details(repo.get_profile_row(), settings))
    repo.update_profile_fields(onboarding_state=step.state)
    return step.reply


def is_active(repo: Repository) -> bool:
    row = repo.get_profile_row()
    return bool(row and row.get("onboarding_state"))


def handle_text(text: str, repo: Repository, settings: Settings) -> str | None:
    """Reply for a plain (non-command) message if /setup is in progress, else None."""
    row = repo.get_profile_row()
    state = (row or {}).get("onboarding_state")
    if not state:
        return None
    res = advance(state, text)
    fields: dict = {"onboarding_state": res.state}
    if res.save:
        fields.update(res.save)
    repo.update_profile_fields(**fields)
    return res.reply


def cancel(repo: Repository) -> str:
    if is_active(repo):
        repo.update_profile_fields(onboarding_state=None)
        return "Setup cancelled. Nothing was changed."
    return "Nothing to cancel."
