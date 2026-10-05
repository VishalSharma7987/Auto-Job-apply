"""Deterministic offline LLM stand-in (FAKE_MODE and tests). Reads only what the prompt contains."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

from jobagent.models import EmailDraft, FormAnswer, MatchResult

T = TypeVar("T", bound=BaseModel)

TECH_VOCAB = [
    "python", "java", "javascript", "typescript", "react", "next.js", "node.js", "fastapi", "flask", "django",
    "langchain", "langgraph", "llamaindex", "rag", "llm", "openai", "pytorch", "tensorflow", "scikit-learn",
    "docker", "kubernetes", "aws", "gcp", "azure", "sql", "postgresql", "mongodb", "redis", "vector database",
    "pinecone", "faiss", "hugging face", "transformers", "agents", "mcp", "prompt engineering", "git", "rest api",
]


def _profile_from(user: str) -> dict:
    m = re.search(r"Candidate profile \(trusted\):\n(\{.*?\})\n", user + "\n", re.S)
    return json.loads(m.group(1)) if m else {}


def _years(text: str) -> tuple[float | None, float | None]:
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*\+?\s*years", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+)\s*\+\s*years", t) or re.search(r"(\d+)\s*years", t)
    if m:
        return float(m.group(1)), None
    return None, None


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: type[T], temperature: float = 0.0) -> T:
        self.calls += 1
        if schema is MatchResult:
            return self._match(user)  # type: ignore[return-value]
        if schema is EmailDraft:
            return self._email(user)  # type: ignore[return-value]
        if schema is FormAnswer:
            return FormAnswer(answer="", confidence=0.1, consequential=True)  # type: ignore[return-value]
        raise ValueError(schema)

    def _match(self, user: str) -> MatchResult:
        prof = _profile_from(user)
        desc = user.split("<<<UNTRUSTED_JOB_DESCRIPTION_START>>>")[-1].lower()
        head = user.lower()
        skills = [s.lower() for s in prof.get("skills", [])]
        mn, mx = _years(desc)
        required = [t for t in TECH_VOCAB if t in desc]
        matched = [t for t in required if t in skills]
        missing = [t for t in required if t not in skills]
        seniority = "senior" if re.search(r"\b(senior|staff|principal|lead)\b", head.split("\n")[0:6].__str__()) else "junior"
        loc_ok = bool(re.search(r"remote|pune|bangalore|bengaluru|hyderabad|india", head.split("job description")[0] + desc))
        ok = (mn is None or mn <= 2) and len(matched) >= 2 and loc_ok and seniority == "junior"
        reasons = []
        if matched:
            reasons.append(" + ".join(m.title() if len(m) > 3 else m.upper() for m in matched[:3]) + " match")
        if mn is not None:
            reasons.append(f"{int(mn)}{'-' + str(int(mx)) if mx else '+'} yrs required")
        if not ok:
            reasons.append("insufficient overlap, seniority or location")
        return MatchResult(
            decision="QUALIFIED" if ok else "REJECTED", required_years_min=mn, required_years_max=mx,
            must_have_skills=required[:6], nice_to_have=[], matched_skills=matched, missing_skills=missing,
            seniority=seniority, location_ok=loc_ok, reasons=reasons, confidence=0.7)

    def _email(self, user: str) -> EmailDraft:
        prof = _profile_from(user)
        subj = re.search(r"Subject must be exactly: (.+)", user)
        projects = prof.get("projects", [])[:2]
        lines = [f"- {p['name']}: {p.get('description', '')}".strip() for p in projects]
        body = (
            f"Hello Hiring Team,\n\nI am applying for this role. I am {prof.get('name', 'the candidate')}, "
            f"{prof.get('headline', '')}.\n" + ("\n".join(lines) + "\n" if lines else "")
            + "\nMy resume is attached. I would welcome the chance to talk.\n\nRegards,\n" + prof.get("name", "")
        )
        return EmailDraft(subject=subj.group(1).strip() if subj else "Application", body=body)
