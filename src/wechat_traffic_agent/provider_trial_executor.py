"""Provider-trial network boundary: resolve one target and measure it.

The executor does not inspect prior results, select a strategy, persist
receipts, or decide whether a provider is viable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TrialExecution:
    measurements: tuple
    trials: tuple


class ProviderTrialExecutor:
    def __init__(self, *, resolve_target: Callable, run_trial: Callable,
                 to_trials: Callable):
        self.resolve_target = resolve_target
        self.run_trial = run_trial
        self.to_trials = to_trials

    def resolve(self, plan, *, api_key: str, excluded_models: frozenset[str]):
        return self.resolve_target(
            plan.credential_env, api_key=api_key, exclude_models=excluded_models,
        )

    def execute(self, plan, *, api_key: str, target: Any, now) -> TrialExecution:
        measurements = tuple(self.run_trial(
            plan, api_key=api_key, now=now, target=target,
        ))
        return TrialExecution(
            measurements=measurements,
            trials=tuple(self.to_trials(measurements)),
        )
