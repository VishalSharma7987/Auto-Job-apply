from __future__ import annotations

from jobagent.apply.strategies import Strategy

# EXPERIMENTAL and disabled by default (ENABLE_GENERIC_APPLY=false). Arbitrary company forms are unpredictable;
# every unknown required field goes to WAITING_USER anyway. Use only after reviewing dry-run screenshots.
STRATEGY = Strategy(
    name="generic",
    host_markers=(),
    experimental=True,
    apply_button_texts=("Apply now", "Apply for this job", "Apply"),
)
