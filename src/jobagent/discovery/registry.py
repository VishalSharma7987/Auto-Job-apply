"""Builds the enabled source adapters from config/sources.yaml + config/companies.yaml (+ env override)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from jobagent.config import Settings
from jobagent.discovery.arbeitnow import ArbeitnowAdapter
from jobagent.discovery.ashby import AshbyAdapter
from jobagent.discovery.base import SourceAdapter
from jobagent.discovery.career_pages import CareerPagesAdapter
from jobagent.discovery.fake import FakeAdapter
from jobagent.discovery.greenhouse import GreenhouseAdapter
from jobagent.discovery.jobicy import JobicyAdapter
from jobagent.discovery.lever import LeverAdapter
from jobagent.discovery.remoteok import RemoteOKAdapter
from jobagent.discovery.remotive import RemotiveAdapter
from jobagent.utils.http import HttpClient

log = logging.getLogger(__name__)

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "greenhouse": GreenhouseAdapter, "lever": LeverAdapter, "ashby": AshbyAdapter, "remotive": RemotiveAdapter,
    "remoteok": RemoteOKAdapter, "arbeitnow": ArbeitnowAdapter, "jobicy": JobicyAdapter,
    "career_pages": CareerPagesAdapter,
}


def _load(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def build_adapters(settings: Settings, http: HttpClient | None = None) -> list[SourceAdapter]:
    if settings.fake_mode:
        return [FakeAdapter(http=http)]
    http = http or HttpClient()
    sources = _load(settings.abs_path(settings.sources_path)).get("sources", {})
    companies = _load(settings.abs_path(settings.companies_path))
    enabled = {s.strip() for s in settings.enabled_sources.split(",")} if settings.enabled_sources else None
    out: list[SourceAdapter] = []
    for name, cls in ADAPTERS.items():
        cfg = sources.get(name, {}) or {}
        on = (name in enabled) if enabled is not None else cfg.get("enabled", True)
        if not on:
            continue
        params = dict(cfg.get("params", {}) or {})
        if name in ("greenhouse", "lever", "ashby"):
            params["boards"] = companies.get(name, []) or []
            if not params["boards"]:
                continue
        if name == "career_pages":
            params["pages"] = companies.get("career_pages", []) or []
            if not params["pages"]:
                continue
        out.append(cls(http=http, **params))
    log.info("sources enabled: %s", [a.name for a in out])
    return out
