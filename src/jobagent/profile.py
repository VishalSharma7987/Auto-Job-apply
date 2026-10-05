"""Verified candidate profile. Single source of truth for every claim the agent may make."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from jobagent.config import Settings

log = logging.getLogger(__name__)


class Project(BaseModel):
    name: str
    description: str = ""
    tech: list[str] = Field(default_factory=list)
    url: str | None = None


class LegalPrefs(BaseModel):
    work_authorization_india: bool = True
    needs_sponsorship: str | bool = "unknown"
    relocation: str | bool = "ask"
    salary: str = "ask"
    accept_terms: bool = False  # tick consent/privacy checkboxes automatically (default: ask the user)


class Profile(BaseModel):
    name: str = "Vishal Sharma"
    headline: str = ""
    experience_years: float = 0
    current_location: str = ""
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=lambda: ["Remote", "Pune", "Bangalore", "Hyderabad"])
    skills: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    legal_prefs: LegalPrefs = Field(default_factory=LegalPrefs)
    # filled from env, never from the YAML
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""

    @property
    def last_name(self) -> str:
        parts = self.name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    def verified_terms(self) -> set[str]:
        """Lowercased technologies the candidate may legitimately claim."""
        terms = {s.lower() for s in self.skills}
        for p in self.projects:
            terms |= {t.lower() for t in p.tech}
        return terms

    def prompt_view(self) -> dict:
        """Profile subset safe to hand to an LLM (no phone/email)."""
        return {
            "name": self.name, "headline": self.headline, "experience_years": self.experience_years,
            "location": self.current_location, "skills": self.skills, "education": self.education,
            "experience": self.experience,
            "projects": [p.model_dump(exclude={"url"}) for p in self.projects],
            "legal_prefs": self.legal_prefs.model_dump(),
        }

    @property
    def is_complete(self) -> bool:
        return bool(self.skills)


def load_profile(settings: Settings) -> Profile:
    path = settings.abs_path(settings.profile_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    prof = Profile(**(data or {}))
    if not prof.is_complete and settings.fake_mode:
        sample = path.parent / "profile.sample.yaml"
        if sample.exists():
            log.warning("profile.yaml has no skills - using SAMPLE profile (FAKE_MODE only)")
            prof = Profile(**yaml.safe_load(sample.read_text(encoding="utf-8")))
    prof.email = settings.gmail_address or ""
    prof.phone = settings.candidate_phone or ""
    prof.linkedin = settings.linkedin_url or ""
    prof.github = settings.github_url or ""
    prof.portfolio = settings.portfolio_url or ""
    return prof


def resume_path(settings: Settings) -> Path:
    return settings.abs_path(settings.resume_path)
