"""httpx wrapper: timeouts, retries, UA, per-host rate limiting."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx

USER_AGENT = "ai-job-agent/0.1 (+personal job search; respects robots.txt)"


class HttpClient:
    def __init__(self, timeout: float = 15.0, retries: int = 2, min_interval: float = 0.5,
                 headers: dict | None = None, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        self.retries = retries
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                r = self._client.get(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 5) * (0.0 if self.min_interval == 0 else 1.0))
                    continue
                return r
            except httpx.TransportError as e:
                last = e
                time.sleep(0 if self.min_interval == 0 else 1.0)
        assert last is not None
        raise last

    def get_json(self, url: str, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def get_text(self, url: str, **kw) -> str | None:
        """Body text for HTML-ish 200 responses, else None (never raises on HTTP errors)."""
        try:
            r = self.get(url, **kw)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        return r.text

    def close(self) -> None:
        self._client.close()
