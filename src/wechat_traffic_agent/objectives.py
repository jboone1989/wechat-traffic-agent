"""Objective-aware policies: composition guidance, metric priorities, reward."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import ContentObjective, PerformanceObservation


class ObjectiveMetric(str, Enum):
    READS = "reads"
    FOLLOWS = "follows"
    DISCUSSIONS = "discussions"  # maps to PerformanceObservation.comments
    SHARES = "shares"
    CONVERSIONS = "conversions"


@dataclass(frozen=True)
class ObjectivePolicy:
    """Decides how one experiment objective guides composition and reward."""

    objective: ContentObjective
    guidance: str
    primary_metric: ObjectiveMetric
    secondary_metrics: tuple[ObjectiveMetric, ...]
    weights: tuple[tuple[ObjectiveMetric, float], ...]

    def _reached(self, obs: PerformanceObservation) -> int:
        return max(obs.impressions or 0, obs.reads or 0, 1)

    def _metric_rate(
        self, metric: ObjectiveMetric, obs: PerformanceObservation
    ) -> float | None:
        if metric is ObjectiveMetric.READS:
            if obs.reads is None:
                return None
            return min(1.0, (obs.reads + 2.0) / (self._reached(obs) + 20.0))
        raw = {
            ObjectiveMetric.FOLLOWS: obs.follows,
            ObjectiveMetric.DISCUSSIONS: obs.comments,
            ObjectiveMetric.SHARES: obs.shares,
            ObjectiveMetric.CONVERSIONS: obs.conversions,
        }[metric]
        if raw is None:
            return None
        return min(1.0, raw / self._reached(obs))

    def reward(self, obs: PerformanceObservation) -> float | None:
        """Weighted metric mix over available metrics, gated by the primary metric.

        An observation without the objective's primary metric carries no
        evidence for that objective: it is UNKNOWN (None), never zero. When
        the primary metric is available, remaining weights renormalize over
        whatever other metrics exist.
        """
        primary = self._metric_rate(self.primary_metric, obs)
        if primary is None:
            return None
        pairs = [
            (weight, self._metric_rate(metric, obs)) for metric, weight in self.weights
        ]
        available = [(weight, rate) for weight, rate in pairs if rate is not None]
        total = sum(weight for weight, _ in available)
        return sum(weight * rate for weight, rate in available) / total


READS_POLICY = ObjectivePolicy(
    objective=ContentObjective.READS,
    guidance=(
        "本轮目标是提高阅读与完读表现：标题制造值得点开的具体悬念，"
        "正文每一句都给出继续读下去的理由，把最有信息量的句子放在前面。"
    ),
    primary_metric=ObjectiveMetric.READS,
    secondary_metrics=(
        ObjectiveMetric.SHARES,
        ObjectiveMetric.FOLLOWS,
        ObjectiveMetric.DISCUSSIONS,
    ),
    weights=(
        (ObjectiveMetric.READS, 0.65),
        (ObjectiveMetric.SHARES, 0.15),
        (ObjectiveMetric.FOLLOWS, 0.10),
        (ObjectiveMetric.DISCUSSIONS, 0.10),
    ),
)

FOLLOWS_POLICY = ObjectivePolicy(
    objective=ContentObjective.FOLLOWS,
    guidance=(
        "本轮目标是提高关注转化：让读者产生「这个号值得持续看」的判断，"
        "互动句自然带出继续关注的收益，不乞求、不承诺、不强迫。"
    ),
    primary_metric=ObjectiveMetric.FOLLOWS,
    secondary_metrics=(ObjectiveMetric.READS, ObjectiveMetric.SHARES),
    weights=(
        (ObjectiveMetric.FOLLOWS, 0.60),
        (ObjectiveMetric.READS, 0.25),
        (ObjectiveMetric.SHARES, 0.15),
    ),
)

DISCUSSION_POLICY = ObjectivePolicy(
    objective=ContentObjective.DISCUSSION,
    guidance=(
        "本轮目标是引发讨论：互动问题要门槛低、有真实分歧空间，"
        "让不同处境的读者都想开口，禁止用对骂或站队制造评论。"
    ),
    primary_metric=ObjectiveMetric.DISCUSSIONS,
    secondary_metrics=(ObjectiveMetric.SHARES, ObjectiveMetric.READS),
    weights=(
        (ObjectiveMetric.DISCUSSIONS, 0.60),
        (ObjectiveMetric.SHARES, 0.25),
        (ObjectiveMetric.READS, 0.15),
    ),
)

CONVERSION_POLICY = ObjectivePolicy(
    objective=ContentObjective.CONVERSION,
    guidance=(
        "本轮目标是后端转化：只围绕 audience 里的 backend_offer 做自然引导，"
        "先给足信息价值再提到提供物；没有 backend_offer 时退化为关注引导。"
    ),
    primary_metric=ObjectiveMetric.CONVERSIONS,
    secondary_metrics=(ObjectiveMetric.FOLLOWS, ObjectiveMetric.READS),
    weights=(
        (ObjectiveMetric.CONVERSIONS, 0.70),
        (ObjectiveMetric.FOLLOWS, 0.20),
        (ObjectiveMetric.READS, 0.10),
    ),
)

OBJECTIVE_POLICIES = {
    ContentObjective.READS: READS_POLICY,
    ContentObjective.FOLLOWS: FOLLOWS_POLICY,
    ContentObjective.DISCUSSION: DISCUSSION_POLICY,
    ContentObjective.CONVERSION: CONVERSION_POLICY,
}


def policy_for(objective: ContentObjective) -> ObjectivePolicy:
    return OBJECTIVE_POLICIES[objective]
