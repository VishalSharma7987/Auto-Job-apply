"""Per-ATS strategies. Each one only describes where things are; filling logic is shared (field_mapper)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Strategy:
    name: str
    host_markers: tuple[str, ...]
    experimental: bool = False
    form_selectors: tuple[str, ...] = ("form",)
    submit_selectors: tuple[str, ...] = ("button[type=submit]", "input[type=submit]")
    apply_button_texts: tuple[str, ...] = ()  # click first if the form is behind an "Apply" button
    success_markers: tuple[str, ...] = (
        "thank you for applying", "application submitted", "application has been submitted",
        "application received", "we have received your application", "thanks for applying", "successfully submitted")
    success_url_markers: tuple[str, ...] = ("confirmation", "thanks", "submitted", "success")
    extra: dict = field(default_factory=dict)

    def matches(self, url: str) -> bool:
        return any(m in url.lower() for m in self.host_markers)

    def apply_url(self, job_url: str) -> str:
        return job_url


def pick_strategy(url: str, allow_generic: bool = False) -> Strategy | None:
    from jobagent.apply.strategies import ashby, generic, greenhouse, lever

    for s in (greenhouse.STRATEGY, lever.STRATEGY, ashby.STRATEGY):
        if s.matches(url):
            return s
    return generic.STRATEGY if allow_generic else None
