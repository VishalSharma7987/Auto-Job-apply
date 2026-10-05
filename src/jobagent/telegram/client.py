"""Telegram Bot API client. Only the allowed chat is ever served."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on newlines where possible so each part is <= limit chars."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str | None, allowed_chat_id: str | None, client: httpx.Client | None = None):
        self.token = token
        self.chat_id = str(allowed_chat_id) if allowed_chat_id else None
        self._c = client or httpx.Client(timeout=30)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, **kw) -> dict:
        r = self._c.post(API.format(token=self.token, method=method), **kw)
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code} non-JSON") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', r.status_code)}")
        return data

    def is_allowed(self, chat_id) -> bool:
        return self.chat_id is not None and str(chat_id) == self.chat_id

    def send_message(self, text: str) -> int:
        """Returns number of message parts sent (0 if Telegram is not configured)."""
        if not self.enabled:
            log.info("telegram not configured; message not sent: %s", text[:80].replace("\n", " "))
            return 0
        parts = split_message(text)
        for p in parts:
            self._call("sendMessage", json={"chat_id": self.chat_id, "text": p, "disable_web_page_preview": True})
        return len(parts)

    def send_photo(self, path: str | Path, caption: str = "") -> bool:
        if not self.enabled or not Path(path).exists():
            return False
        with open(path, "rb") as f:
            self._call("sendPhoto", data={"chat_id": self.chat_id, "caption": caption[:1000]}, files={"photo": f})
        return True

    def send_document(self, data: bytes, filename: str, caption: str = "") -> bool:
        if not self.enabled:
            return False
        self._call("sendDocument", data={"chat_id": self.chat_id, "caption": caption[:1000]},
                   files={"document": (filename, data, "application/pdf")})
        return True

    def get_file(self, file_id: str) -> bytes:
        """Download a file the user sent (Bot API limit: 20 MB; we enforce 5 MB before calling this)."""
        info = self._call("getFile", json={"file_id": file_id})
        path = info["result"]["file_path"]
        r = self._c.get(f"https://api.telegram.org/file/bot{self.token}/{path}")
        r.raise_for_status()
        return r.content

    def get_updates(self, offset: int | None = None) -> list[dict]:
        """Pending updates. Returns [] if a webhook is active (getUpdates then returns HTTP 409)."""
        if not self.token:
            return []
        params: dict = {"timeout": 0, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            return self._call("getUpdates", json=params).get("result", [])
        except TelegramError as e:
            log.info("getUpdates unavailable (%s) - relying on webhook relay payloads", e)
            return []
