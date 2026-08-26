"""Evidence-based selection of emotional angles, per experiment objective."""
from __future__ import annotations

from dataclasses import dataclass

from .models import ContentObjective, EmotionAngle, PerformanceObservation
from .objectives import policy_for


@dataclass(frozen=True)
class AngleEstimate:
    angle: EmotionAngle
    samples: int
    scored_samples: int
    impressions: int
    reward: float | None


class TrafficLearner:
    """Pure learner: history is supplied by Runtime sensors, never stored here."""

    def estimates(
        self,
        history: list[PerformanceObservation],
        objective: ContentObjective = ContentObjective.READS,
    ) -> list[AngleEstimate]:
        policy = policy_for(objective)
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
    ) -> EmotionAngle:
        estimates = self.estimates(history, objective)
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
