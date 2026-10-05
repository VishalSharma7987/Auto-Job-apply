"""Provider-independent LLM client (any OpenAI-compatible endpoint: OpenRouter, Groq, Ollama...)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class QuotaExceeded(Exception):
    def __init__(self, provider: str, details: str = ""):
        super().__init__(f"{provider}: {details}")
        self.provider = provider
        self.details = details


class LLMError(Exception):
    pass


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: type[T], temperature: float = 0.0) -> T: ...


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating fences/prose around it."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON object in model output")


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str | None, model: str, max_retries: int = 3,
                 backoff: float = 2.0, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=60.0, max_retries=0)
        self.client = client
        self.model = model
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff = backoff
        self.calls = 0

    @property
    def provider(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc or self.base_url

    def _create(self, system: str, user: str, temperature: float):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            return self.client.chat.completions.create(
                model=self.model, messages=msgs, temperature=temperature,
                response_format={"type": "json_object"})
        except Exception as e:  # providers without json mode reject the param
            if getattr(e, "status_code", None) == 400:
                return self.client.chat.completions.create(model=self.model, messages=msgs, temperature=temperature)
            raise

    def complete_json(self, system: str, user: str, schema: type[T], temperature: float = 0.0) -> T:
        last_err = ""
        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                resp = self._create(system, user, temperature)
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                return schema.model_validate(extract_json(content))
            except (ValidationError, ValueError) as e:
                last_err = f"invalid JSON/schema: {str(e)[:200]}"
                log.warning("LLM output invalid (attempt %d): %s", attempt + 1, last_err)
            except Exception as e:
                status = getattr(e, "status_code", None)
                msg = str(e)[:300]
                if status in (402, 429) or "quota" in msg.lower() or "rate limit" in msg.lower():
                    last_err = f"HTTP {status}: {msg}"
                    if status == 402 or attempt == self.max_retries - 1:
                        raise QuotaExceeded(self.provider, last_err) from e
                elif status and 400 <= status < 500 and status not in (408, 409):
                    raise LLMError(f"HTTP {status}: {msg}") from e
                else:
                    last_err = msg
            if attempt < self.max_retries - 1:
                time.sleep(self.backoff * (2 ** attempt))
        raise LLMError(f"LLM failed after {self.max_retries} attempts: {last_err}")


def make_llm(settings) -> LLM:
    if settings.fake_mode:
        from jobagent.llm.fake import FakeLLM

        return FakeLLM()
    return OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                              settings.llm_max_retries, settings.llm_backoff_seconds)
