"""General epistemic-action guidance for evidence-seeking agent decisions.

This module contains domain-independent principles.  It must not contain benchmark
answers, task-specific hints, or evaluator-private information.
"""
from __future__ import annotations

EPISTEMIC_ACTION_GUIDANCE = """Before finalizing a claim or answer, ask whether the
current evidence actually distinguishes it from other plausible explanations.
If multiple plausible states remain and an available tool can resolve the ambiguity,
prefer a bounded discriminating probe over guessing. Choose the cheapest reliable
probe whose possible outcomes would separate the alternatives.

For changing or time-dependent state, use a controlled measurement with an explicit
baseline and interval rather than comparing observations taken at unknown times.
After changing the environment, verify the requested effect directly before claiming
completion. Stop gathering evidence once it is decisive; do not explore for its own
sake."""


def epistemic_action_guidance() -> str:
    return EPISTEMIC_ACTION_GUIDANCE
