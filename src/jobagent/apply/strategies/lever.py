from __future__ import annotations

from jobagent.apply.strategies import Strategy


class LeverStrategy(Strategy):
    def apply_url(self, job_url: str) -> str:
        base = job_url.split("?")[0].rstrip("/")
        return base if base.endswith("/apply") else base + "/apply"


# jobs.lever.co/<org>/<id>/apply hosts the form.
STRATEGY = LeverStrategy(
    name="lever",
    host_markers=("lever.co",),
    form_selectors=("form#application-form", "form.application-form", "form"),
    submit_selectors=("#btn-submit", "button[type=submit]", "input[type=submit]"),
)
