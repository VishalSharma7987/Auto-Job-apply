from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jobagent.config import Settings
from jobagent.db.sqlite_repo import SqliteRepository
from jobagent.models import Job
from jobagent.profile import Profile
from jobagent.telegram.client import TelegramClient

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
CHAT = "4242"


class FakeTelegram(TelegramClient):
    """Captures outgoing messages; never touches the network."""

    def __init__(self, chat_id: str = CHAT):
        super().__init__("123456:TESTTOKEN_not_real_xxxxxxxxxxxxxxxxxxxx", chat_id)
        self.sent: list[str] = []
        self.photos: list[str] = []
        self.files: dict[str, bytes] = {}  # file_id -> bytes served by get_file
        self.documents: list[tuple[str, int]] = []  # (filename, size) sent back to the user

    def send_message(self, text: str) -> int:
        from jobagent.telegram.client import split_message

        parts = split_message(text)
        self.sent += parts
        return len(parts)

    def send_photo(self, path, caption: str = "") -> bool:
        self.photos.append(str(path))
        return True

    def get_updates(self, offset=None):
        return []

    def get_file(self, file_id: str) -> bytes:
        if file_id not in self.files:
            raise RuntimeError("file not found")
        return self.files[file_id]

    def send_document(self, data: bytes, filename: str, caption: str = "") -> bool:
        self.documents.append((filename, len(data)))
        return True


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, dry_run=True, fake_mode=True, db_backend="sqlite",
                    sqlite_path=str(tmp_path / "t.sqlite"), artifacts_dir=str(tmp_path / "artifacts"),
                    profile_path=str(ROOT / "profile" / "profile.yaml"),
                    companies_path=str(ROOT / "config" / "companies.yaml"),
                    sources_path=str(ROOT / "config" / "sources.yaml"),
                    resume_path=str(tmp_path / "resume.pdf"), telegram_allowed_chat_id=CHAT,
                    telegram_bot_token="123456:TESTTOKEN_not_real_xxxxxxxxxxxxxxxxxxxx",
                    gmail_address="candidate@example.com", gmail_app_password="app-pass-xxxx",
                    github_url="https://github.com/example", llm_backoff_seconds=0)


@pytest.fixture
def profile() -> Profile:
    data = yaml.safe_load((ROOT / "profile" / "profile.sample.yaml").read_text(encoding="utf-8"))
    p = Profile(**data)
    p.email, p.phone, p.github = "candidate@example.com", "+910000000000", "https://github.com/example"
    return p


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository(":memory:")


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def resume(tmp_path) -> Path:
    p = tmp_path / "resume.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake resume for tests\n")
    return p


def make_job(**kw) -> Job:
    from jobagent.utils.hashing import make_job_key

    base = dict(company="Acme AI", title="AI Developer", url="https://boards.greenhouse.io/acmeai/jobs/1",
                source="test", location="Remote - India", remote=True,
                description="0-2 years. Python, LangChain, RAG, FastAPI.")
    base.update(kw)
    base.setdefault("job_key", make_job_key(base["company"], base["title"], base["url"]))
    return Job(**base)


@pytest.fixture
def job_factory():
    return make_job
