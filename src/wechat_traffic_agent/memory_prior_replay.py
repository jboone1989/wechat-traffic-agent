from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .content_strategy import choose_content_strategy
from .traffic_learning import TopicTrafficSignal


def replay_memory_prior_ab(
    *,
    state_root: Path,
    account: str,
    cycle: str,
    topic: str,
    traffic_signal: TopicTrafficSignal,
    as_of: datetime,
    mind_strategy_context: Mapping[str, Any],
    action_forecasts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay the exact same prospective decision with and without MemoryPrior.

    This never publishes. It is a deterministic counterfactual over the selector inputs and
    therefore measures decision influence, not real-world causal outcome lift.
    """
    enriched = deepcopy(dict(mind_strategy_context))
    baseline_context = deepcopy(enriched)
    baseline_context.pop("memory_prior", None)
    baseline = choose_content_strategy(
        state_root=Path(state_root),
        account=account,
        cycle=cycle,
        topic=topic,
        traffic_signal=traffic_signal,
        as_of=as_of,
        mind_strategy_context=baseline_context,
        action_forecasts=action_forecasts,
    )
    assisted = choose_content_strategy(
        state_root=Path(state_root),
        account=account,
        cycle=cycle,
        topic=topic,
        traffic_signal=traffic_signal,
        as_of=as_of,
        mind_strategy_context=enriched,
        action_forecasts=action_forecasts,
    )
    return {
        "status": "DIFFERENT_DECISION" if baseline.profile.name != assisted.profile.name else "SAME_DECISION",
        "cycle": cycle,
        "topic": topic,
        "baseline_profile": baseline.profile.name,
        "memory_assisted_profile": assisted.profile.name,
        "baseline_mode": baseline.selection_mode,
        "memory_assisted_mode": assisted.selection_mode,
        "memory_prior_applied": assisted.memory_prior_applied,
        "memory_ids": list(assisted.memory_prior_memory_ids),
        "causal_claim": False,
        "interpretation": "counterfactual selector influence only; not evidence of outcome improvement",
    }


__all__ = ["replay_memory_prior_ab"]
