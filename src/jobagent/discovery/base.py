from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from jobagent.models import RawJob
from jobagent.utils.http import HttpClient

log = logging.getLogger(__name__)


class SourceAdapter(ABC):
    name: str = "base"

    def __init__(self, http: HttpClient | None = None, **params):
        self.http = http or HttpClient()
        self.params = params
        self._deadline: float | None = None

    def begin(self, max_seconds: float | None) -> None:
        """Start the per-source clock; multi-request adapters stop fetching more boards/pages once it runs out."""
        self._deadline = None if not max_seconds else time.monotonic() + max_seconds

    def expired(self) -> bool:
        if self._deadline is not None and time.monotonic() > self._deadline:
            log.warning("source %s: time limit reached - returning what was fetched so far", self.name)
            return True
        return False

    @abstractmethod
    def fetch(self) -> list[RawJob]:
        """Return raw jobs. Must not raise for a single bad board - log and continue."""
