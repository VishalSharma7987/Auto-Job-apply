from __future__ import annotations

from abc import ABC, abstractmethod

from jobagent.models import RawJob
from jobagent.utils.http import HttpClient


class SourceAdapter(ABC):
    name: str = "base"

    def __init__(self, http: HttpClient | None = None, **params):
        self.http = http or HttpClient()
        self.params = params

    @abstractmethod
    def fetch(self) -> list[RawJob]:
        """Return raw jobs. Must not raise for a single bad board - log and continue."""
