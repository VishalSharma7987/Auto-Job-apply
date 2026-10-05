"""Blockers we must never try to get around. Pure functions over page HTML so they are unit-testable.

Any detection => stop safely, mark WAITING_USER, screenshot, notify. We never solve CAPTCHAs or OTPs,
never log in, and never tick legal boxes the user has not pre-approved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

CAPTCHA_SRC = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare\.com|turnstile|funcaptcha|arkoselabs", re.I)
OTP_RE = re.compile(r"(one[- ]time (code|password)|verification code|enter (the )?(6|six)[- ]digit|\botp\b|"
                    r"security code we (sent|emailed)|confirm your email address)", re.I)
LEGAL_RE = re.compile(r"(i (agree|accept|consent|certify|acknowledge|confirm|have read)|terms (of (use|service)|and conditions)|"
                      r"privacy (policy|notice)|consent to|background check|data processing|gdpr|"
                      r"i understand that)", re.I)
IDENTITY_RE = re.compile(r"(verify your identity|identity verification|government[- ]issued id|photo id|"
                         r"upload (a |your )?(selfie|photo of your id)|aadhaar|passport number|video interview required)", re.I)
BLOCK_PAGE_RE = re.compile(r"(access denied|unusual traffic|verify you are (a )?human|are you a robot|"
                           r"checking your browser|attention required)", re.I)


@dataclass
class Detection:
    kind: str  # captcha | otp | login | legal | blocked
    detail: str = ""


def detect_captcha(html: str) -> Detection | None:
    soup = BeautifulSoup(html, "html.parser")
    for fr in soup.find_all("iframe"):
        src = fr.get("src", "") + " " + fr.get("title", "")
        if CAPTCHA_SRC.search(src):
            # reCAPTCHA v3 / invisible badge frames are not challenges
            if "anchor" in src and "size=invisible" in src:
                continue
            return Detection("captcha", src[:120])
    if soup.select(".h-captcha, .cf-turnstile, .g-recaptcha:not([data-size='invisible']), [data-hcaptcha-widget-id]"):
        return Detection("captcha", "captcha widget")
    return None


def detect_otp(html: str) -> Detection | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select("input[autocomplete='one-time-code']"):
        return Detection("otp", "one-time-code input")
    text = soup.get_text(" ")
    if OTP_RE.search(text) and soup.select("input[type=text], input[type=tel], input[type=number]"):
        return Detection("otp", "verification code requested")
    return None


def detect_login_wall(html: str, url: str = "") -> Detection | None:
    soup = BeautifulSoup(html, "html.parser")
    has_pw = bool(soup.select("input[type=password]"))
    text = soup.get_text(" ").lower()
    form_fields = soup.select("input[type=file], textarea")
    if has_pw and not form_fields and re.search(r"sign in|log ?in|create an account|continue with (google|linkedin)", text):
        return Detection("login", "login required")
    if re.search(r"/(login|signin|sign-in|sso|auth)(/|\?|$)", url) and not form_fields:
        return Detection("login", f"redirected to {url[:80]}")
    return None


def detect_identity(html: str) -> Detection | None:
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    m = IDENTITY_RE.search(text)
    return Detection("identity", m.group(0)) if m else None


def detect_blocked(html: str) -> Detection | None:
    text = BeautifulSoup(html, "html.parser").get_text(" ")[:3000]
    if BLOCK_PAGE_RE.search(text):
        return Detection("blocked", "anti-bot / access page")
    return None


def detect_legal_checkboxes(html: str) -> list[Detection]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Detection] = []
    for cb in soup.select("input[type=checkbox]"):
        label = ""
        if cb.get("id"):
            lab = soup.find("label", attrs={"for": cb["id"]})
            label = lab.get_text(" ") if lab else ""
        if not label and cb.parent:
            label = cb.parent.get_text(" ")
        if LEGAL_RE.search(label):
            out.append(Detection("legal", re.sub(r"\s+", " ", label).strip()[:160]))
    return out


def detect_all(html: str, url: str = "") -> list[Detection]:
    """Hard blockers only (legal checkboxes are handled by the field mapper using legal_prefs)."""
    found = [d for d in (detect_blocked(html), detect_captcha(html), detect_otp(html),
                         detect_identity(html), detect_login_wall(html, url)) if d]
    return found
