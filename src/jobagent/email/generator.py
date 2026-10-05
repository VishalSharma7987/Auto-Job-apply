"""Email drafting from the verified profile only, with a post-generation "no unverified claims" guard."""

from __future__ import annotations

import logging
import re

from jobagent.llm.client import LLM, LLMError
from jobagent.llm.fake import TECH_VOCAB
from jobagent.llm.prompts import email_prompt
from jobagent.models import EmailDraft, Job
from jobagent.profile import Profile

log = logging.getLogger(__name__)
MAX_WORDS = 180

# Technologies we police. Any of these named in a draft must exist in profile skills / project tech.
POLICED_TECH = sorted(set(TECH_VOCAB) | {
    "golang", "rust", "c++", "c#", "kotlin", "php", "ruby", "spark", "hadoop", "kafka", "airflow",
    "terraform", "jenkins", "tableau", "power bi", "numpy", "pandas", "keras", "opencv", "spring boot", "angular",
    "vue", "svelte", "graphql", "elasticsearch", "mysql", "dynamodb", "firebase", "supabase", "cuda", "ollama",
    "gemini", "claude", "gpt-4", "llama", "mistral", "crewai", "autogen", "streamlit", "gradio", "tailwind",
}, key=len, reverse=True)
_ALIASES = {"js": "javascript", "ts": "typescript", "node": "node.js", "nodejs": "node.js", "nextjs": "next.js",
            "postgres": "postgresql", "sklearn": "scikit-learn", "huggingface": "hugging face"}


def _norm(s: str) -> str:
    s = s.lower().strip()
    return _ALIASES.get(s, s)


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9+#.]){re.escape(term)}(?![a-z0-9+#]|\.[a-z0-9])", text.lower()) is not None


def unverified_claims(body: str, profile: Profile) -> list[str]:
    """Technologies named in `body` that the profile does not list."""
    known = {_norm(t) for t in profile.verified_terms()} | set(profile.verified_terms())
    bad = []
    for term in POLICED_TECH:
        if _mentions(body, term) and _norm(term) not in known:
            # allow if it is part of a longer verified phrase (e.g. "go" inside "google")
            if not any(_norm(term) in k.split() or term == k for k in known):
                bad.append(term)
    return sorted(set(bad))


def _word_count(s: str) -> int:
    return len(s.split())


def subject_for(job: Job, profile: Profile) -> str:
    return f"Application – {job.title} | {profile.name}"


def template_email(job: Job, profile: Profile, links: dict[str, str]) -> EmailDraft:
    """Deterministic fallback: built only from profile facts."""
    lines = [f"Hello Hiring Team at {job.company},", "",
             f"I am writing to apply for the {job.title} role. {profile.headline}".strip()]
    for p in profile.projects[:2]:
        tech = f" ({', '.join(p.tech[:4])})" if p.tech else ""
        lines.append(f"- {p.name}{tech}: {p.description}".strip())
    if not profile.projects and profile.project_areas:
        lines.append(f"My hands-on work covers {', '.join(profile.project_areas[:4])}.")
    if profile.skills:
        lines.append(f"My core skills include {', '.join(profile.skills[:6])}.")
    lines += ["", "My resume is attached" + (" and my work is linked below." if links else "."), ]
    lines += [f"{k}: {v}" for k, v in links.items()]
    lines += ["", "Thank you for your time.", "", f"Regards,\n{profile.name}"]
    return EmailDraft(subject=subject_for(job, profile), body="\n".join(lines))


def _links(profile: Profile) -> dict[str, str]:
    return {k: v for k, v in {"GitHub": profile.github, "Portfolio": profile.portfolio,
                              "LinkedIn": profile.linkedin}.items() if v}


def generate_email(job: Job, profile: Profile, llm: LLM) -> tuple[EmailDraft, str]:
    """Returns (draft, how) where how in {"llm", "llm_retry", "template"}."""
    links = _links(profile)
    feedback = ""
    for attempt in range(2):
        try:
            system, user = email_prompt(job, profile, links, feedback)
            d = llm.complete_json(system, user, EmailDraft, temperature=0.3)
        except LLMError as e:
            log.warning("email LLM failed: %s", e)
            break
        d.subject = subject_for(job, profile)
        problems = []
        bad = unverified_claims(d.body, profile)
        if bad:
            problems.append(f"remove unverified technologies: {', '.join(bad)}")
        if _word_count(d.body) > MAX_WORDS:
            problems.append(f"shorten to under {MAX_WORDS} words")
        if profile.github == "" and "github.com" in d.body.lower() or (
                profile.portfolio == "" and re.search(r"portfolio:?\s*https?://", d.body, re.I)):
            problems.append("remove links that were not provided")
        if not problems:
            if links:
                missing = [f"{k}: {v}" for k, v in links.items() if v not in d.body]
                if missing:
                    d.body = d.body.rstrip() + "\n\n" + "\n".join(missing)
            return d, "llm" if attempt == 0 else "llm_retry"
        feedback = "; ".join(problems)
        log.info("email draft rejected by guard: %s", feedback)
    return template_email(job, profile, links), "template"
