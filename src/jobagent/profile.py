"""Verified candidate profile. Single source of truth for every claim the agent may make."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from jobagent.config import Settings

log = logging.getLogger(__name__)


def term_variants(term: str) -> set[str]:
    """Lowercased spellings a skill may legitimately appear under: 'React.js' -> react.js, react; 'LLMs' -> llm."""
    t = term.lower().strip()
    out = {t}
    for suf in (" apis", " api", ".js", "js"):
        if t.endswith(suf) and len(t) > len(suf) + 1:
            out.add(t[: -len(suf)].strip())
    if t.endswith("s") and len(t) > 3:
        out.add(t[:-1])
    parts = [x.strip() for x in re.split(r"[/,&]|and", t) if x.strip()]
    if len(parts) > 1:
        for part in parts:
            out |= term_variants(part)
    for word in re.findall(r"[a-z0-9.+#]+", t):
        if len(word) > 3:
            out.add(word)
            if word.endswith("s"):
                out.add(word[:-1])
    return out


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
    experience_level: str = ""  # e.g. "0-1 year (junior / entry-level)" - from the requirements doc, not a number
    experience_years: float | None = None
    current_location: str = ""
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=lambda: ["Remote", "Pune", "Bangalore", "Hyderabad"])
    skills: list[str] = Field(default_factory=list)
    project_areas: list[str] = Field(default_factory=list)  # areas of hands-on work (no specific projects invented)
    daily_target: str = ""
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
        terms: set[str] = set()
        for s in self.skills:
            terms |= term_variants(s)
        for p in self.projects:
            for t in p.tech:
                terms |= term_variants(t)
        return terms

    def prompt_view(self) -> dict:
        """Profile subset safe to hand to an LLM (no phone/email)."""
        return {
            "name": self.name, "headline": self.headline, "experience_level": self.experience_level,
            "experience_years": self.experience_years, "target_roles": self.target_roles,
            "location": self.current_location, "preferred_locations": self.preferred_locations,
            "skills": self.skills, "project_areas": self.project_areas, "education": self.education,
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
