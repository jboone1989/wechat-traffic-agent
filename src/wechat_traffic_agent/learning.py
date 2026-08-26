"""Evidence-based selection of emotional angles."""
from __future__ import annotations

from dataclasses import dataclass

from .models import EmotionAngle, PerformanceObservation


@dataclass(frozen=True)
class AngleEstimate:
    angle: EmotionAngle
    samples: int
    impressions: int
    reward: float


class TrafficLearner:
    """Pure learner: history is supplied by Runtime sensors, never stored here."""

    def estimates(
        self, history: list[PerformanceObservation]
    ) -> list[AngleEstimate]:
        estimates: list[AngleEstimate] = []
        for angle in EmotionAngle:
            items = [item for item in history if item.angle is angle]
            impressions = sum(item.impressions for item in items)
            if items:
                weight = sum(max(item.impressions, 20) for item in items)
                reward = sum(
                    item.reward * max(item.impressions, 20) for item in items
                ) / weight
            else:
                reward = 0.0
            estimates.append(
                AngleEstimate(
                    angle=angle,
                    samples=len(items),
                    impressions=impressions,
                    reward=reward,
                )
            )
        return estimates

    def recommend(self, history: list[PerformanceObservation]) -> EmotionAngle:
        estimates = self.estimates(history)
        # Explore every angle twice before exploiting measured performance.
        under_sampled = [item for item in estimates if item.samples < 2]
        if under_sampled:
            return min(
                under_sampled,
                key=lambda item: (item.samples, item.impressions, item.angle.value),
            ).angle
        return max(
            estimates,
            key=lambda item: (item.reward, item.impressions, item.angle.value),
        ).angle

