from __future__ import annotations

from jobagent.apply.strategies import Strategy


class AshbyStrategy(Strategy):
    def apply_url(self, job_url: str) -> str:
        base = job_url.split("?")[0].rstrip("/")
        return base if base.endswith("/application") else base + "/application"


# jobs.ashbyhq.com/<org>/<id>/application hosts the form.
STRATEGY = AshbyStrategy(
    name="ashby",
    host_markers=("ashbyhq.com",),
    form_selectors=("form", "[class*=application]"),
    submit_selectors=("button[type=submit]", "button:has-text('Submit Application')", "button:has-text('Submit')"),
)
