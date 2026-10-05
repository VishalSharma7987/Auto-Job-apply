from __future__ import annotations

from jobagent.apply.strategies import Strategy

# Hosted boards: boards.greenhouse.io/<org>/jobs/<id>, job-boards.greenhouse.io/<org>/jobs/<id>.
# The application form is on the same page (#application_form / #application-form).
STRATEGY = Strategy(
    name="greenhouse",
    host_markers=("greenhouse.io",),
    form_selectors=("#application_form", "#application-form", "form#application", "form"),
    submit_selectors=("#submit_app", "button[type=submit]", "input[type=submit]"),
)
