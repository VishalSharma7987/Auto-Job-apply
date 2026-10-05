from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from jobagent.utils.http import USER_AGENT, HttpClient


class RobotsChecker:
    """robots.txt gate. Unreachable robots.txt = allowed (standard); an explicit Disallow = blocked."""

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()
        self._cache: dict[str, RobotFileParser | None] = {}

    def _parser(self, origin: str) -> RobotFileParser | None:
        if origin not in self._cache:
            text = self.http.get_text(origin + "/robots.txt")
            if text is None:
                self._cache[origin] = None
            else:
                rp = RobotFileParser()
                rp.parse(text.splitlines())
                self._cache[origin] = rp
        return self._cache[origin]

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return False
        rp = self._parser(f"{p.scheme}://{p.netloc}")
        return True if rp is None else rp.can_fetch(USER_AGENT.split("/")[0], url)
