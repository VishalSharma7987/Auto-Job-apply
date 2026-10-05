"""All configuration comes from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # safety
    dry_run: bool = True
    fake_mode: bool = False
    max_applications_per_day: int = 15
    max_ai_matches_per_run: int = 30
    enable_browser_apply: bool = True
    enable_generic_apply: bool = False  # experimental, off by default

    # storage
    db_backend: str = "sqlite"  # sqlite | supabase
    sqlite_path: str = "data/jobagent.sqlite"
    supabase_url: str | None = None
    supabase_key: str | None = None

    # telegram
    telegram_bot_token: str | None = None
    telegram_allowed_chat_id: str | None = None

    # llm (OpenAI-compatible)
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str | None = Field(default=None, validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY"))
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    llm_max_retries: int = 3
    llm_backoff_seconds: float = 2.0

    # email
    gmail_address: str | None = None
    gmail_app_password: str | None = None

    # candidate (never committed)
    candidate_phone: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = Field(
        default=None, validation_alias=AliasChoices("CANDIDATE_GITHUB_URL", "GITHUB_URL", "github_url"))
    portfolio_url: str | None = None

    # paths
    profile_path: str = "profile/profile.yaml"
    resume_path: str = "profile/resume/resume.pdf"
    artifacts_dir: str = "artifacts"
    companies_path: str = "config/companies.yaml"
    sources_path: str = "config/sources.yaml"

    # sources (comma separated overrides)
    enabled_sources: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: Any) -> Any:
        # GitHub Actions passes unset secrets/vars as "" - treat as not provided.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        return data

    def abs_path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else ROOT_OR_CWD() / path

    def public_view(self) -> dict[str, Any]:
        """Non-secret settings for /settings."""
        return {
            "DRY_RUN": self.dry_run,
            "FAKE_MODE": self.fake_mode,
            "DB_BACKEND": self.db_backend,
            "MAX_APPLICATIONS_PER_DAY": self.max_applications_per_day,
            "MAX_AI_MATCHES_PER_RUN": self.max_ai_matches_per_run,
            "LLM_MODEL": self.llm_model,
            "LLM_BASE_URL": self.llm_base_url,
            "BROWSER_APPLY": self.enable_browser_apply,
            "GENERIC_APPLY": self.enable_generic_apply,
        }


def ROOT_OR_CWD() -> Path:
    """Repo root: cwd if it has config/, else the source checkout root."""
    cwd = Path.cwd()
    if (cwd / "config").exists():
        return cwd
    return ROOT


@lru_cache
def get_settings() -> Settings:
    return Settings()
