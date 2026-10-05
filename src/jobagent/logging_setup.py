from __future__ import annotations

import json
import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{10,})"),
    re.compile(r"(\d{6,}:[A-Za-z0-9_\-]{30,})"),  # telegram bot token
    re.compile(r"(eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"),  # JWT
    re.compile(r"(sbp_[A-Za-z0-9]{20,})"),
]
_registered_secrets: set[str] = set()


def register_secrets(*values: str | None) -> None:
    for v in values:
        if v and len(v) >= 6:
            _registered_secrets.add(v)


def redact(text: str) -> str:
    for s in _registered_secrets:
        text = text.replace(s, "***")
    for p in _SECRET_PATTERNS:
        text = p.sub("***", text)
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                           "logger": record.name, "msg": record.getMessage()}, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_logs: bool | None = None) -> None:
    import os

    if json_logs is None:
        json_logs = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("LOG_JSON") == "1"
    h = logging.StreamHandler(sys.stdout)
    h.addFilter(_RedactFilter())
    h.setFormatter(_JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
