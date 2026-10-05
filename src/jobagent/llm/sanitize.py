"""Untrusted text (job descriptions, web pages) must be cleaned before it reaches an LLM."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

_INJECTION = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|messages?)",
    r"disregard (all |any )?(the )?(previous|prior|above|earlier)[^.\n]{0,40}",
    r"forget (all |everything|your) (previous |prior )?(instructions|rules)",
    r"you are now [^.\n]{0,80}",
    r"(new|updated) (system )?instructions?:",
    r"system prompt",
    r"act as (an?|the) [^.\n]{0,60}",
    r"(reveal|print|output|send|email) (your|the) (system prompt|api key|secrets?|password|resume)[^.\n]{0,60}",
    r"<\s*/?\s*(system|assistant|user)\s*>",
    r"\[\s*/?\s*(INST|SYS)\s*\]",
    r"(do not|don't) (follow|obey) (the )?(above|previous|prior)",
]
_INJ_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION), re.I)
_DELIM_RE = re.compile(r"<<<|>>>|UNTRUSTED_[A-Z_]+")


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(raw) if "&lt;" in raw or "&amp;" in raw else raw
    soup = BeautifulSoup(text, "html.parser")
    for t in soup(["script", "style", "noscript", "iframe", "template"]):
        t.decompose()
    for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
        br.append("\n")
    return soup.get_text(" ")


def sanitize(raw: str, max_chars: int = 6000) -> str:
    text = strip_html(raw)
    text = _DELIM_RE.sub(" ", text)
    text = _INJ_RE.sub("[removed]", text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:max_chars]


def wrap_untrusted(label: str, text: str) -> str:
    """Delimit untrusted data; the system prompt tells the model to treat it as data only."""
    return f"<<<UNTRUSTED_{label.upper()}_START>>>\n{text}\n<<<UNTRUSTED_{label.upper()}_END>>>"
