"""StrategyLearner: relative-performance learning over experiment history.

PR6 replaces "TrafficLearner → EmotionAngle only" with a strategy learner
spanning audience / objective / variable / treatment dimensions while
staying deliberately simple: minimum samples, smoothed historical lift
against a trailing account baseline, exploration before exploitation.
Contextual bandits can wait until real experiments accumulate.

Two isolation rules are absolute:

1. context = (audience_key, objective, observation_window). A silver-
   audience nostalgia win never informs programmer-audience choices; a
   discussion-objective experiment never shares reward with reads; 24h
   numbers are never compared to 7d numbers.
2. UNKNOWN stays UNKNOWN — an observation whose primary metric is missing
   counts as explored but is excluded from every reward average (never 0).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median

from .learning import TrafficLearner
from .models import ContentObjective, EmotionAngle, PerformanceObservation
from .objectives import policy_for

CONTEXT_KEY_AUDIENCE = "*"
SMOOTHING_ALPHA = 2.0

_MAX_TIME = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class StrategyEstimate:
    """Aggregated performance of one treatment inside one isolated context."""

    context_key: str
    variable: str
    value: str
    samples: int
    scored_samples: int
    avg_reward: float | None
    lift_vs_trailing_baseline: float | None
    smoothed_lift: float | None


@dataclass(frozen=True)
class StrategyDecision:
    """Which variable to test next for one audience context."""

    variable: object  # caller-supplied candidate item (e.g. planner enum), passed through
    exploration: bool
    reason: str


class StrategyLearner:
    def __init__(self, *, min_samples: int = 3, baseline_window: int = 10) -> None:
        self.min_samples = max(1, min_samples)
        self.baseline_window = max(1, baseline_window)

    # ------------------------------------------------------------------ #
    # Context isolation                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _context_parts(obs: PerformanceObservation) -> tuple[str, str, str]:
        audience = obs.audience_key or CONTEXT_KEY_AUDIENCE
        objective = obs.objective.value if obs.objective else CONTEXT_KEY_AUDIENCE
        window = obs.observation_window or CONTEXT_KEY_AUDIENCE
        return audience, objective, window

    @staticmethod
    def _context_key(parts: tuple[str, str, str]) -> str:
        return f"({parts[0]}, {parts[1]}, {parts[2]})"

    def contexts(
        self, history: Sequence[PerformanceObservation]
    ) -> dict[tuple[str, str, str], list[PerformanceObservation]]:
        grouped: dict[tuple[str, str, str], list[PerformanceObservation]] = {}
        for obs in history:
            grouped.setdefault(self._context_parts(obs), []).append(obs)
        return grouped

    # ------------------------------------------------------------------ #
    # Estimates                                                          #
    # ------------------------------------------------------------------ #

    def estimates(self, history: Sequence[PerformanceObservation]) -> list[StrategyEstimate]:
        rows: list[StrategyEstimate] = []
        for parts, observations in sorted(self.contexts(history).items()):
            policy = (
                policy_for(ContentObjective(parts[1]))
                if parts[1] != CONTEXT_KEY_AUDIENCE
                else None
            )
            ordered = self._chronological(observations)
            scored_rewards = self._scored_rewards(policy, observations, ordered)

            buckets: dict[tuple[str, str], list[int]] = {}
            for position, (_, obs) in enumerate(ordered):
                if obs.variable_under_test is None:
                    continue
                key = (obs.variable_under_test, obs.treatment_value or "")
                buckets.setdefault(key, []).append(position)

            for (variable, treatment), positions in sorted(buckets.items()):
                bucket_scored = [
                    (position, scored_rewards[position])
                    for position in positions
                    if position in scored_rewards
                ]
                avg_reward = (
                    sum(reward for _, reward in bucket_scored) / len(bucket_scored)
                    if bucket_scored
                    else None
                )
                lifts = [
                    lift
                    for lift in (
                        self._lift_at(scored_rewards, position)
                        for position, _ in bucket_scored
                    )
                    if lift is not None
                ]
                mean_lift = sum(lifts) / len(lifts) if lifts else None
                smoothed = (
                    sum(lifts) / (len(lifts) + SMOOTHING_ALPHA) if lifts else None
                )
                rows.append(
                    StrategyEstimate(
                        context_key=self._context_key(parts),
                        variable=variable,
                        value=treatment,
                        samples=len(positions),
                        scored_samples=len(bucket_scored),
                        avg_reward=avg_reward,
                        lift_vs_trailing_baseline=mean_lift,
                        smoothed_lift=smoothed,
                    )
                )
        return rows

    @staticmethod
    def _chronological(
        observations: Sequence[PerformanceObservation],
    ) -> list[tuple[int, PerformanceObservation]]:
        indexed = list(enumerate(observations))
        indexed.sort(
            key=lambda pair: (
                pair[1].published_at or _MAX_TIME,
                pair[1].post_id,
            )
        )
        return indexed

    @staticmethod
    def _scored_rewards(policy, observations, ordered) -> dict[int, float]:
        """position (in chronological order) -> policy reward; UNKNOWN excluded."""
        if policy is None:
            return {}
        rewards: dict[int, float] = {}
        for position, (_, obs) in enumerate(ordered):
            reward = policy.reward(obs)
            if reward is not None:
                rewards[position] = reward
        return rewards

    def _lift_at(
        self, scored_rewards: dict[int, float], position: int
    ) -> float | None:
        """reward minus the median of up to ``baseline_window`` prior rewards.

        No comparable prior is UNKNOWN, never a synthetic 0.0 lift.
        """
        prior = [
            reward
            for prior_position, reward in sorted(scored_rewards.items())
            if prior_position < position
        ][-self.baseline_window :]
        if not prior:
            return None
        return scored_rewards[position] - median(prior)

    # ------------------------------------------------------------------ #
    # Recommendation                                                     #
    # ------------------------------------------------------------------ #

    def recommend(
        self,
        history: Sequence[PerformanceObservation],
        *,
        candidate_variables: Sequence,
        audience_key: str | None = None,
        objective: ContentObjective = ContentObjective.READS,
        observation_window: str,
    ) -> StrategyDecision:
        """Explore under-min variables first, then exploit best smoothed lift.

        ``observation_window`` 是显式必填参数：探索计数与利用排序都只在
        ``(audience, objective, window)`` 完全一致的 context 里进行，
        与 estimates() 的隔离口径一致——24h 和 7d 永不互相污染。
        缺窗口标注的观察落在 ``(*)`` context，同样不会混入。
        """
        requested = (
            audience_key or CONTEXT_KEY_AUDIENCE,
            objective.value,
            observation_window,
        )
        counts: dict[str, int] = {}
        for parts, observations in self.contexts(history).items():
            if parts != requested:
                continue
            for obs in observations:
                if obs.variable_under_test is None:
                    continue
                counts[obs.variable_under_test] = counts.get(obs.variable_under_test, 0) + 1

        names = [getattr(item, "value", item) for item in candidate_variables]
        under_explored = [
            item
            for item, name in zip(candidate_variables, names)
            if counts.get(name, 0) < self.min_samples
        ]
        if under_explored:
            chosen = under_explored[0]
            chosen_name = next(name for name, item in zip(names, candidate_variables) if item is chosen)
            return StrategyDecision(
                variable=chosen,
                exploration=True,
                reason=(
                    f"{counts.get(chosen_name, 0)} experiments < min_samples {self.min_samples}"
                ),
            )

        target_key = self._context_key(requested)
        allowed = set(names)
        grouped: dict[str, list[StrategyEstimate]] = {}
        for estimate in self.estimates(history):
            if estimate.context_key != target_key:
                continue
            if estimate.variable not in allowed or estimate.smoothed_lift is None:
                continue
            grouped.setdefault(estimate.variable, []).append(estimate)

        def rank(entry: tuple[str, list[StrategyEstimate]]):
            name, items = entry
            mean_lift = sum(item.smoothed_lift for item in items) / len(items)
            total_samples = sum(item.samples for item in items)
            return (-mean_lift, total_samples, names.index(name))

        for name, _items in sorted(grouped.items(), key=rank):
            winner = next((item for item in candidate_variables if getattr(item, "value") == name), None)
            if winner is not None:
                return StrategyDecision(
                    variable=winner, exploration=False, reason="best smoothed lift"
                )
        fallback_name = min(names, key=lambda n: counts.get(n, 0))
        return StrategyDecision(
            variable=next(
                item for item in candidate_variables if getattr(item, "value") == fallback_name
            ),
            exploration=False,
            reason="no scorable evidence yet; least sampled",
        )

    def recommended_angle(
        self,
        history: Sequence[PerformanceObservation],
        *,
        objective: ContentObjective = ContentObjective.READS,
        audience_key: str | None = None,
        observation_window: str | None = None,
    ) -> EmotionAngle:
        """Emotion dimension still rides the scoped TrafficLearner rule."""
        return TrafficLearner().recommend(
            list(history),
            objective,
            audience_key=audience_key,
            observation_window=observation_window,
        )
