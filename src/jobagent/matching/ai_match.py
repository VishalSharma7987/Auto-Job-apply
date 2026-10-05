"""LLM structured extraction + decision. Results are cached on the job row (match_json) so re-runs cost 0 calls."""

from __future__ import annotations

import logging

from jobagent.db.base import Repository
from jobagent.llm.client import LLM
from jobagent.llm.prompts import match_prompt
from jobagent.models import Job, MatchResult
from jobagent.profile import Profile

log = logging.getLogger(__name__)


def cached_match(job_row: dict) -> MatchResult | None:
    mj = job_row.get("match_json")
    if isinstance(mj, dict) and mj.get("decision"):
        try:
            return MatchResult.model_validate(mj)
        except ValueError:
            return None
    return None


def ai_match(job: Job, job_row: dict, profile: Profile, llm: LLM, repo: Repository) -> tuple[MatchResult, bool]:
    """Returns (result, used_llm). May raise QuotaExceeded / LLMError (caller handles)."""
    hit = cached_match(job_row)
    if hit:
        return hit, False
    system, user = match_prompt(job, profile)
    res = llm.complete_json(system, user, MatchResult, temperature=0.0)
    # Never trust matched_skills that are not actually in the verified profile.
    known = profile.verified_terms()
    res.matched_skills = [s for s in res.matched_skills if s.lower() in known]
    repo.update_job(job_row["id"], match_json=res.model_dump(), match_reasons=res.reasons,
                    requirements_json={"must_have": res.must_have_skills, "nice_to_have": res.nice_to_have,
                                       "years_min": res.required_years_min, "years_max": res.required_years_max})
    return res, True
