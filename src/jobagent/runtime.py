"""Runtime profile: yaml (skills/roles) + personal details from the DB first, env as fallback, plus the resume file.

Resume order: Supabase Storage (uploaded via Telegram) -> RESUME_PDF_B64 env -> an existing local file -> missing.
Phone/LinkedIn/GitHub/portfolio/location order: DB -> env -> empty (forms that need an empty value go WAITING_USER).
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass

from jobagent.config import Settings
from jobagent.db.base import Repository
from jobagent.profile import Profile, load_profile, resume_path
from jobagent.storage import VARIANTS, ResumeStore, object_name

log = logging.getLogger(__name__)

LOCAL_NAMES = {"default": "resume.pdf", "ai": "resume_ai.pdf", "fullstack": "resume_fullstack.pdf"}


@dataclass
class RuntimeProfile:
    profile: Profile
    resume_source: str  # storage | env | local | missing
    variants: list[str]


def apply_db_details(profile: Profile, row: dict | None) -> Profile:
    """DB values win over env values (which load_profile already put there)."""
    row = row or {}
    profile.phone = row.get("phone") or profile.phone
    profile.linkedin = row.get("linkedin_url") or profile.linkedin
    profile.github = row.get("github_url") or profile.github
    profile.portfolio = row.get("portfolio_url") or profile.portfolio
    profile.current_location = row.get("location") or profile.current_location
    return profile


def _write_resumes(settings: Settings, row: dict, store: ResumeStore | None) -> tuple[str, list[str]]:
    target = resume_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    source = "missing"

    if store is not None and row:
        have = set((row.get("resume_variants") or {}).keys())
        if row.get("resume_path"):
            have.add("default")
        for variant in VARIANTS:
            if variant not in have:
                continue
            try:
                data = store.download(object_name(variant))
            except Exception as e:  # noqa: BLE001
                log.warning("resume %s download failed: %s", variant, type(e).__name__)
                data = None
            if data and data.startswith(b"%PDF"):
                (target.parent / LOCAL_NAMES[variant]).write_bytes(data)
                written.append(variant)
        if "default" in written:
            source = "storage"

    if source == "missing" and settings.resume_pdf_b64:
        try:
            data = base64.b64decode(settings.resume_pdf_b64.strip(), validate=False)
        except (binascii.Error, ValueError):
            data = b""
        if data.startswith(b"%PDF"):
            target.write_bytes(data)
            source = "env"
            written.append("default")
        else:
            log.warning("RESUME_PDF_B64 is set but is not a base64 PDF - ignored")

    if source == "missing" and target.exists() and target.stat().st_size > 0:
        source = "local"
        written.append("default")
    return source, written


def load_runtime_profile(settings: Settings, repo: Repository, store: ResumeStore | None) -> RuntimeProfile:
    profile = load_profile(settings)
    row = repo.get_profile_row()
    apply_db_details(profile, row)
    source, variants = _write_resumes(settings, row or {}, store)
    return RuntimeProfile(profile=profile, resume_source=source, variants=variants)
