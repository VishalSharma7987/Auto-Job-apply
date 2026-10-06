"""Telegram onboarding for personal details.

Three ways in, all validated the same way and all stored in the profile row:
  * ONE message:   /setup  -> a template to copy; reply with the whole block (or `/setup phone=+91... location=Pune`).
  * single field:  /set phone +919876543210
  * step by step:  fallback when a block is partial (asks only for the MISSING fields), or `/setup steps` (all fields).

Stateless between worker runs: the state lives in profile.onboarding_state (jsonb). Phone numbers are never logged.

State: {"step": "block|phone|linkedin|github|portfolio|location|confirm",
        "queue": [fields still to ask], "answers": {field: value ("" = skipped)}, "current": {...}, "confirm": bool}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from jobagent.config import Settings
from jobagent.db.base import Repository

FIELDS = ["phone", "linkedin", "github", "portfolio", "location"]
REQUIRED = ["phone", "linkedin", "github", "location"]  # portfolio is optional; linkedin/github may be `skip`
DB_COLUMNS = {"phone": "phone", "linkedin": "linkedin_url", "github": "github_url", "portfolio": "portfolio_url",
              "location": "location"}
SKIPPABLE = {"linkedin", "github", "portfolio"}
LABELS = {"phone": "Phone", "linkedin": "LinkedIn", "github": "GitHub", "portfolio": "Portfolio", "location": "Location"}

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
SKIP_WORDS = {"skip", "-", "none", "n/a", "na", "no"}
YES = {"yes", "y", "confirm", "ok", "okay"}
NO = {"no", "n"}

# field aliases accepted as keys in a block / single line / /set
ALIASES = {"phone": "phone", "mobile": "phone", "mob": "phone", "contact": "phone", "linkedin": "linkedin",
           "github": "github", "portfolio": "portfolio", "website": "portfolio", "site": "portfolio",
           "location": "location", "city": "location"}
KEY_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(ALIASES) + r")\s*[:=]\s*", re.I)
PLACEHOLDER_RE = re.compile(r"^(\+?\d*\.{2,}|https?://\.{2,}|city|\(optional\)|optional|\.{2,}|<.*>)$", re.I)


@dataclass
class Step:
    state: dict | None  # new state to persist (None = clear)
    reply: str
    save: dict | None = None  # DB columns to write when the flow completes


@dataclass
class Parsed:
    values: dict[str, str] = field(default_factory=dict)  # normalised; "" = skipped
    errors: dict[str, str] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)  # keys found (even if left as the template placeholder)


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


def validate_value(name: str, raw: str, current: dict) -> tuple[str | None, str | None]:
    """-> (normalised value or None, error or None). '' means skipped (only for optional-ish fields)."""
    t = (raw or "").strip()
    low = t.lower()
    if low in KEEP_WORDS and current.get(name):
        return current[name], None
    if low in SKIP_WORDS and name in SKIPPABLE:
        return "", None
    if low in KEEP_WORDS | SKIP_WORDS:  # control words are never data (e.g. a city called "skip" / "keep")
        return None, ERRORS[name]
    value = VALIDATORS[name](t)
    return (value, None) if value is not None else (None, ERRORS[name])


# ---------------------------------------------------------------- one-message parsing
def parse_block(text: str, current: dict | None = None) -> Parsed:
    """Parse 'phone: ...\nlinkedin: ...' blocks and 'phone=... linkedin=... location=New Delhi' one-liners."""
    current = current or {}
    out = Parsed()
    matches = list(KEY_RE.finditer(text or ""))
    for i, m in enumerate(matches):
        name = ALIASES[m.group(1).lower()]
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[m.end():end].strip().strip(",;")
        out.seen.append(name)
        if PLACEHOLDER_RE.match(raw) or raw == "":
            continue  # left as the template placeholder: "not provided"
        value, err = validate_value(name, raw, current)
        if err:
            out.errors[name] = err
        else:
            out.values[name] = value  # type: ignore[assignment]
    return out


def template(current: dict) -> str:
    def line(k: str, placeholder: str) -> str:
        return f"{k}: {current.get(k) or placeholder}"

    return ("Copy this, fill it in and send it back in ONE message "
            "(lines you leave unchanged are kept; write 'skip' for linkedin/github/portfolio you don't have):\n\n"
            + "\n".join([line("phone", "+91..."), line("linkedin", "https://..."), line("github", "https://..."),
                         line("portfolio", "(optional)"), line("location", "City")])
            + "\n\nOr use /set <field> <value> for a single field, /setup steps for questions one at a time.")


def summary(effective: dict, header: str = "Please check your details:", skipped: set | None = None) -> str:
    skipped = skipped or set()

    def show(k: str) -> str:
        return effective.get(k) or ("(skipped)" if k in skipped else "(not set)")

    return (f"{header}\nPhone: {show('phone')}\nLinkedIn: {show('linkedin')}\nGitHub: {show('github')}\n"
            f"Portfolio: {show('portfolio')}\nLocation: {show('location')}")


def _plain_prompt(step: str, current: dict) -> str:
    return re.sub(r"^\d/\d - ", "", PROMPTS[step]) + _hint(step, current)


def _hint(step: str, current: dict) -> str:
    cur = current.get(step)
    return f"\n(current: {cur} - reply 'keep' to keep it)" if cur else ""


def _skipped(answers: dict) -> set:
    return {k for k, v in answers.items() if v == ""}


def _save_columns(answers: dict) -> dict:
    return {DB_COLUMNS[k]: (v or None) for k, v in answers.items() if k in DB_COLUMNS}


def _missing(answers: dict, current: dict) -> list[str]:
    return [f for f in REQUIRED if f not in answers and not current.get(f)]


# ---------------------------------------------------------------- block / fallback logic
def apply_block(text: str, state: dict | None, current: dict) -> Step:
    """Merge a (possibly partial) block into the answers so far and decide: save, report errors, or ask for the rest."""
    answers = dict((state or {}).get("answers") or {})
    parsed = parse_block(text, current)
    if not parsed.seen:
        return Step(state or {"step": "block", "answers": answers, "current": current, "queue": [], "confirm": False},
                    "I couldn't find any `field: value` lines in that message.\n\n" + template({**current, **answers}))
    if parsed.errors:
        answers.update(parsed.values)  # keep what was valid so only the bad lines need re-sending
        problems = "\n".join(f"• {k}: {v}" for k, v in parsed.errors.items())
        return Step({"step": "block", "answers": answers, "current": current, "queue": [], "confirm": False},
                    f"I could not accept:\n{problems}\n\nSend the corrected line(s) - the valid ones were kept.")
    answers.update(parsed.values)
    missing = _missing(answers, current)
    if not missing:
        effective = {**current, **{k: v for k, v in answers.items()}}
        return Step(None, summary(effective, "✅ Details saved:", _skipped(answers)), _save_columns(answers))
    # fallback: step-by-step for ONLY the missing fields
    nxt = missing[0]
    got = ", ".join(LABELS[k] for k in answers) or "nothing yet"
    state2 = {"step": nxt, "queue": missing, "answers": answers, "current": current, "confirm": False}
    return Step(state2, f"Got: {got}. Still missing: {', '.join(LABELS[k] for k in missing)}.\n\n" + _plain_prompt(nxt, current))


# ---------------------------------------------------------------- step flow (full, or only the missing fields)
def start(current: dict) -> Step:
    """The full 5-question flow with a final confirmation (`/setup steps`)."""
    state = {"step": "phone", "queue": list(FIELDS), "answers": {}, "current": {k: v for k, v in current.items() if v},
             "confirm": True}
    return Step(state, "Let's set up your details. Send 'cancel' at any time to stop.\n\n" + PROMPTS["phone"]
                + _hint("phone", state["current"]))


def advance(state: dict, text: str) -> Step:
    t = (text or "").strip()
    low = t.lower()
    if low in ("cancel", "/cancel", "stop", "abort"):
        return Step(None, "Setup cancelled. Nothing was changed. Send /setup to start again.")
    step = state.get("step")
    answers = dict(state.get("answers") or {})
    current = state.get("current") or {}

    if step == "block":
        return apply_block(t, state, current)

    if step == "confirm":
        if low in YES:
            return Step(None, "✅ Details saved. Send /profile to review them.", _save_columns(answers))
        if low in NO:
            fresh = start(current)
            fresh.reply = "OK, let's go through them again.\n\n" + PROMPTS["phone"] + _hint("phone", current)
            return fresh
        return Step(state, "Please answer yes or no.\n\n" + summary({**current, **answers}, skipped=_skipped(answers)))

    if step not in FIELDS:
        return Step(None, "Setup state was invalid and has been reset. Send /setup to start again.")

    if KEY_RE.search(t):  # the user switched to `field: value` lines mid-flow: accept them
        return apply_block(t, state, current)

    value, err = validate_value(step, t, current)
    if err:
        return Step(state, ERRORS[step])
    answers[step] = value  # type: ignore[assignment]
    queue = [q for q in (state.get("queue") or FIELDS) if q != step and q not in answers]
    if queue:
        nxt = queue[0]
        prompt = PROMPTS[nxt] if state.get("confirm") else _plain_prompt(nxt, current)
        if state.get("confirm"):
            prompt += _hint(nxt, current)
        return Step({"step": nxt, "queue": queue, "answers": answers, "current": current,
                     "confirm": bool(state.get("confirm"))}, prompt)
    if state.get("confirm"):
        return Step({"step": "confirm", "queue": [], "answers": answers, "current": current, "confirm": True},
                    summary({**current, **answers}, skipped=_skipped(answers)) + "\n\nConfirm? yes / no")
    return Step(None, summary({**current, **answers}, "✅ Details saved:", _skipped(answers)), _save_columns(answers))


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


def _persist(repo: Repository, step: Step) -> str:
    fields: dict = {"onboarding_state": step.state}
    if step.save:
        fields.update(step.save)
    repo.update_profile_fields(**fields)
    return step.reply


def begin(repo: Repository, settings: Settings, args_text: str = "") -> str:
    """/setup: no args -> template; `steps` -> full question flow; anything else -> parse it as a block / one-liner."""
    current = current_details(repo.get_profile_row(), settings)
    arg = (args_text or "").strip()
    if arg.lower() == "steps":
        return _persist(repo, start(current))
    block_state = {"step": "block", "queue": [], "answers": {}, "current": current, "confirm": False}
    if not arg:
        repo.update_profile_fields(onboarding_state=block_state)
        return template(current)
    return _persist(repo, apply_block(arg, block_state, current))


def set_field(args: list[str], repo: Repository, settings: Settings) -> str:
    """/set <field> <value> - update one field immediately."""
    usage = "Usage: /set <phone|linkedin|github|portfolio|location> <value>   e.g. /set phone +919876543210"
    if len(args) < 2 or args[0].lower() not in ALIASES:
        return usage
    name = ALIASES[args[0].lower()]
    current = current_details(repo.get_profile_row(), settings)
    value, err = validate_value(name, " ".join(args[1:]), current)
    if err:
        return err
    repo.update_profile_fields(**{DB_COLUMNS[name]: value or None})
    return f"✅ {LABELS[name]} updated: {value or '(cleared)'}"


def is_active(repo: Repository) -> bool:
    row = repo.get_profile_row()
    return bool(row and row.get("onboarding_state"))


def handle_text(text: str, repo: Repository, settings: Settings) -> str | None:
    """Reply for a plain (non-command) message if /setup is in progress, else None."""
    row = repo.get_profile_row()
    state = (row or {}).get("onboarding_state")
    if not state:
        return None
    return _persist(repo, advance(state, text))


def cancel(repo: Repository) -> str:
    if is_active(repo):
        repo.update_profile_fields(onboarding_state=None)
        return "Setup cancelled. Nothing was changed."
    return "Nothing to cancel."
