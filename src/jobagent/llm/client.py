"""Provider-independent LLM client (any OpenAI-compatible endpoint: OpenRouter, Groq, Ollama...).

Speed/robustness rules (see docs/PERF_NOTES.md): hard per-call timeout (default 60 s), a max_tokens cap per task,
temperature 0, at most LLM_MAX_RETRIES attempts (default 2), and one log line per call with its duration.
"""

from __future__ import annotations

import json
import logging
import re
import threading
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


class LLMTimeout(LLMError):
    """Every attempt hit the per-call timeout."""


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: type[T], temperature: float = 0.0,
                      max_tokens: int | None = None) -> T: ...


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


class LLMCallTimeout(Exception):
    """Raised by the wall-clock guard (httpx read timeouts reset on OpenRouter keep-alive bytes)."""


def call_with_deadline(fn, seconds: float):
    """Run fn() in a daemon thread and give up after `seconds` wall-clock (the thread is abandoned, never joined,
    so it cannot keep the process alive)."""
    box: dict = {}

    def target():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            box["e"] = e

    t = threading.Thread(target=target, daemon=True, name="llm-call")
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise LLMCallTimeout(f"no response within {seconds:.0f}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _model_gone(status, msg: str) -> bool:
    low = msg.lower()
    return status == 404 or "no endpoints found" in low or "is not a valid model" in low


def _is_timeout(e: Exception) -> bool:
    name = type(e).__name__.lower()
    return "timeout" in name or "timed out" in str(e).lower()


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str | None, model: str, max_retries: int = 2,
                 backoff: float = 2.0, client=None, timeout: float = 60.0, fallback_models: list[str] | None = None):
        if client is None:
            import httpx
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0,
                            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)))
        self.client = client
        self.model = model
        self.base_url = base_url
        self.max_retries = max(1, max_retries)  # = total attempts
        self.backoff = backoff
        self.timeout = timeout
        self.calls = 0
        self.total_seconds = 0.0
        self.fallback_models = [m for m in (fallback_models or []) if m and m != model]
        self.switched_from: str | None = None  # set when the configured model was unavailable (404)

    @property
    def provider(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc or self.base_url

    def _create(self, system: str, user: str, temperature: float, max_tokens: int | None):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        kw: dict = {"model": self.model, "messages": msgs, "temperature": temperature}
        if max_tokens:
            kw["max_tokens"] = max_tokens

        def call():
            try:
                return self.client.chat.completions.create(response_format={"type": "json_object"}, **kw)
            except Exception as e:  # providers without json mode reject the param
                if getattr(e, "status_code", None) == 400:
                    return self.client.chat.completions.create(**kw)
                raise

        return call_with_deadline(call, self.timeout)

    def complete_json(self, system: str, user: str, schema: type[T], temperature: float = 0.0,
                      max_tokens: int | None = None) -> T:
        last_err = ""
        timeouts = 0
        for attempt in range(self.max_retries):
            t0 = time.monotonic()
            outcome = "ok"
            try:
                self.calls += 1
                resp = self._create(system, user, temperature, max_tokens)
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                return schema.model_validate(extract_json(content))
            except (ValidationError, ValueError) as e:
                outcome = "invalid-json"
                last_err = f"invalid JSON/schema: {str(e)[:200]}"
            except Exception as e:
                status = getattr(e, "status_code", None)
                msg = str(e)[:300]
                if _is_timeout(e):
                    outcome = "timeout"
                    timeouts += 1
                    last_err = f"timeout after {self.timeout:.0f}s"
                elif status in (402, 429) or "quota" in msg.lower() or "rate limit" in msg.lower():
                    outcome = f"http-{status}"
                    last_err = f"HTTP {status}: {msg}"
                    if status == 402 or attempt == self.max_retries - 1:
                        self._log(schema, attempt, t0, outcome)
                        raise QuotaExceeded(self.provider, last_err) from e
                elif _model_gone(status, msg) and self.fallback_models:
                    old, self.model = self.model, self.fallback_models.pop(0)
                    self.switched_from = self.switched_from or old
                    self._log(schema, attempt, t0, f"http-{status} model unavailable -> switching to {self.model}")
                    log.warning("LLM model %s is unavailable (%s); using fallback %s for this run", old, status,
                                self.model)
                    return self.complete_json(system, user, schema, temperature, max_tokens)
                elif status and 400 <= status < 500 and status not in (408, 409):
                    self._log(schema, attempt, t0, f"http-{status}")
                    raise LLMError(f"HTTP {status}: {msg}") from e
                else:
                    outcome = f"error-{type(e).__name__}"
                    last_err = msg
            finally:
                if outcome == "ok":
                    self._log(schema, attempt, t0, "ok")
            self._log(schema, attempt, t0, outcome)
            if attempt < self.max_retries - 1:
                time.sleep(self.backoff * (2 ** attempt))
        if timeouts == self.max_retries:
            raise LLMTimeout(f"LLM timed out on all {self.max_retries} attempts ({self.timeout:.0f}s each)")
        raise LLMError(f"LLM failed after {self.max_retries} attempts: {last_err}")

    def _log(self, schema: type, attempt: int, t0: float, outcome: str) -> None:
        dt = time.monotonic() - t0
        self.total_seconds += dt
        log.info("LLM %s attempt %d/%d model=%s %.1fs %s", schema.__name__, attempt + 1, self.max_retries,
                 self.model, dt, outcome)


def make_llm(settings) -> LLM:
    if settings.fake_mode:
        from jobagent.llm.fake import FakeLLM

        return FakeLLM()
    fallbacks = [m.strip() for m in (settings.llm_fallback_models or "").split(",") if m.strip()]
    return OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                              settings.llm_max_retries, settings.llm_backoff_seconds,
                              timeout=settings.llm_timeout_seconds, fallback_models=fallbacks)
