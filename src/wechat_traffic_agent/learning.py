"""Evidence-based selection of emotional angles, per experiment objective."""
from __future__ import annotations

from dataclasses import dataclass

from .models import ContentObjective, EmotionAngle, PerformanceObservation
from .objectives import policy_for

UNSCOPED_CONTEXT = "*"


@dataclass(frozen=True)
class AngleEstimate:
    angle: EmotionAngle
    samples: int
    scored_samples: int
    impressions: int
    reward: float | None


class TrafficLearner:
    """Pure learner: history is supplied by Runtime sensors, never stored here.

    Recommendation is scoped to ``(audience_key, objective, observation_window)``.
    Omitting audience/window uses only unlabeled observations — labeled foreign
    context must never leak into an unscoped call.

    Legacy compatibility: ``objective=None`` means READS (the pre-PR6 default).
    It is not a wildcard for FOLLOWS / DISCUSSION / CONVERSION.
    """

    @staticmethod
    def _observation_objective(item: PerformanceObservation) -> ContentObjective:
        return item.objective or ContentObjective.READS

    @staticmethod
    def scoped_history(
        history: list[PerformanceObservation],
        objective: ContentObjective,
        *,
        audience_key: str | None = None,
        observation_window: str | None = None,
    ) -> list[PerformanceObservation]:
        """Return the same evidence slice used for recommendation.

        Callers that prompt a model with history must use this helper so
        labeled foreign context cannot leak after the angle is chosen.
        """
        requested_audience = audience_key or UNSCOPED_CONTEXT
        requested_window = observation_window or UNSCOPED_CONTEXT
        scoped: list[PerformanceObservation] = []
        for item in history:
            item_audience = item.audience_key or UNSCOPED_CONTEXT
            item_window = item.observation_window or UNSCOPED_CONTEXT
            if item_audience != requested_audience or item_window != requested_window:
                continue
            if TrafficLearner._observation_objective(item) is not objective:
                continue
            scoped.append(item)
        return scoped

    def estimates(
        self,
        history: list[PerformanceObservation],
        objective: ContentObjective = ContentObjective.READS,
        *,
        audience_key: str | None = None,
        observation_window: str | None = None,
    ) -> list[AngleEstimate]:
        policy = policy_for(objective)
        history = self.scoped_history(
            history,
            objective,
            audience_key=audience_key,
            observation_window=observation_window,
        )
        estimates: list[AngleEstimate] = []
        for angle in EmotionAngle:
            items = [item for item in history if item.angle is angle]
            impressions = sum(item.impressions or 0 for item in items)
            scored: list[tuple[float, int]] = []
            for item in items:
                reward = policy.reward(item)
                if reward is not None:
                    scored.append((reward, max(item.impressions or 0, 20)))
            if scored:
                weight = sum(weight for _, weight in scored)
                reward = sum(r * w for r, w in scored) / weight
            else:
                reward = None
            estimates.append(
                AngleEstimate(
                    angle=angle,
                    samples=len(items),
                    scored_samples=len(scored),
                    impressions=impressions,
                    reward=reward,
                )
            )
        return estimates

    def recommend(
        self,
        history: list[PerformanceObservation],
        objective: ContentObjective = ContentObjective.READS,
        *,
        audience_key: str | None = None,
        observation_window: str | None = None,
    ) -> EmotionAngle:
        estimates = self.estimates(
            history,
            objective,
            audience_key=audience_key,
            observation_window=observation_window,
        )
        # Explore every angle twice before exploiting measured performance.
        # An angle with samples but no scorable metric is still unexplored:
        # "no data" must never be read as "bad performance".
        under_sampled = [
            item for item in estimates if item.samples < 2 or item.scored_samples == 0
        ]
        if under_sampled:
            return min(
                under_sampled,
                key=lambda item: (item.samples, item.impressions, item.angle.value),
            ).angle
        return max(
            estimates,
            key=lambda item: (item.reward or 0.0, item.impressions, item.angle.value),
        ).angle
