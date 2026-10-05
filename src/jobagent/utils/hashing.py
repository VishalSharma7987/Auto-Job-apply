from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src", "gh_jid_src",
             "ref", "refid", "source", "src", "lever-source", "lever-origin", "fbclid", "gclid"}
_KEEP_QUERY = {"gh_jid", "id", "jobid", "job_id", "jid"}


def normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragment, trailing slash and tracking params."""
    p = urlparse((url or "").strip())
    host = p.netloc.lower().removeprefix("www.")
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING and k.lower() in _KEEP_QUERY]
    q.sort()
    path = p.path.rstrip("/") or ""
    return urlunparse((p.scheme.lower() or "https", host, path, "", urlencode(q), ""))


def make_job_key(company: str, title: str, url: str) -> str:
    raw = normalize_text(company) + normalize_text(title) + canonical_url(url)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
