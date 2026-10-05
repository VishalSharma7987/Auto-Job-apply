"""Resume storage. Supabase: private bucket 'resumes' (service key only). Local: a folder (sqlite/dev/tests).

Objects are named '<variant>.pdf' - default.pdf, ai.pdf, fullstack.pdf. Contents are never logged.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

log = logging.getLogger(__name__)

BUCKET = "resumes"
MAX_RESUME_BYTES = 5 * 1024 * 1024
VARIANTS = ("default", "ai", "fullstack")


class ResumeStore(ABC):
    @abstractmethod
    def upload(self, name: str, data: bytes) -> None: ...

    @abstractmethod
    def download(self, name: str) -> bytes | None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...


class LocalResumeStore(ResumeStore):
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, name: str) -> Path:
        return self.root / Path(name).name  # no path traversal

    def upload(self, name: str, data: bytes) -> None:
        self._p(name).write_bytes(data)

    def download(self, name: str) -> bytes | None:
        p = self._p(name)
        return p.read_bytes() if p.exists() else None

    def delete(self, name: str) -> None:
        self._p(name).unlink(missing_ok=True)


class SupabaseResumeStore(ResumeStore):
    def __init__(self, client, bucket: str = BUCKET):
        self.bucket = client.storage.from_(bucket)

    def upload(self, name: str, data: bytes) -> None:
        self.bucket.upload(path=name, file=data, file_options={"content-type": "application/pdf", "upsert": "true"})

    def download(self, name: str) -> bytes | None:
        try:
            return self.bucket.download(name)
        except Exception as e:  # noqa: BLE001 - supabase raises for a missing object; treat as "not stored"
            log.info("resume %s not in storage (%s)", name, type(e).__name__)
            return None

    def delete(self, name: str) -> None:
        self.bucket.remove([name])


def make_store(settings, repo) -> ResumeStore:
    if settings.db_backend == "supabase" and hasattr(repo, "c"):
        return SupabaseResumeStore(repo.c)
    return LocalResumeStore(settings.abs_path("data/resume_store"))


def object_name(variant: str) -> str:
    return f"{variant}.pdf"
