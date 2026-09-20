from __future__ import annotations

from collections import defaultdict
import re
import time
from pathlib import Path
from typing import Any

from .memory_observation import MemoryLifeObservatory
from .memory_system import CanonicalMemoryStore


_AUDIENCE_RE = re.compile(
    r"Website strategy (?P<profile>.+?) on topic (?P<topic>.+?) produced "
    r"(?P<pageviews>[0-9]+(?:\.[0-9]+)?) pageviews",
    re.IGNORECASE,
)
_PREDICTION_RE = re.compile(
    r"Content action prediction for (?P<profile>.+?) on (?P<topic>.+?) at (?P<window>1h|4h|24h): "
    r"forecast (?P<forecast>[0-9]+(?:\.[0-9]+)?) pageviews, observed "
    r"(?P<actual>[0-9]+(?:\.[0-9]+)?); prediction error (?P<error>[0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


class MemoryDecisionPrior:
    """Read-only decision prior derived from Ferro's canonical lived memory."""

    def __init__(self, state_root: Path, *, store: CanonicalMemoryStore | None = None) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)
        self.observatory = MemoryLifeObservatory(self.state_root)

    @staticmethod
    def _observation_text(row: dict[str, Any]) -> str:
        observations = (row.get("payload") or {}).get("observations") or []
        for item in observations:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                return str(item["text"])
        return str(row.get("text") or "")

    def strategy_prior(self, *, topic: str) -> dict[str, Any]:
        topic = str(topic).strip()
        started = time.perf_counter()
        episodes = self.store.query_memories(
            memory_types=["EPISODIC"],
            statuses=["ACTIVE"],
            topic="virtualbrain-life",
            access_scope="PRIVATE_OWNER",
            limit=5000,
        )
        rewards: dict[str, list[float]] = defaultdict(list)
        pageviews: dict[str, list[float]] = defaultdict(list)
        errors: dict[str, list[float]] = defaultdict(list)
        memory_ids: dict[str, list[str]] = defaultdict(list)
        evidence_refs: dict[str, list[str]] = defaultdict(list)

        for row in episodes:
            attrs = (row.get("payload") or {}).get("attributes") or {}
            source = str(attrs.get("source") or "")
            text = self._observation_text(row)
            if source == "website-audience-outcome":
                match = _AUDIENCE_RE.search(text)
                if not match or match.group("topic") != topic:
                    continue
                profile = match.group("profile")
                outcome = (row.get("payload") or {}).get("outcome")
                try:
                    reward = float(outcome)
                except (TypeError, ValueError):
                    continue
                rewards[profile].append(max(0.0, min(1.0, reward)))
                pageviews[profile].append(float(match.group("pageviews")))
            elif source == "content-action-prediction-outcome":
                match = _PREDICTION_RE.search(text)
                if not match or match.group("topic") != topic:
                    continue
                if match.group("window") != "24h":
                    continue
                profile = match.group("profile")
                errors[profile].append(max(0.0, min(1.0, float(match.group("error")))))
            else:
                continue
            memory_ids[profile].append(str(row.get("memory_id") or ""))
            evidence_refs[profile].extend(str(ref) for ref in row.get("evidence_refs") or [])

        profiles: dict[str, dict[str, Any]] = {}
        for profile in sorted(set(rewards) | set(errors)):
            reward_values = rewards.get(profile, [])
            error_values = errors.get(profile, [])
            mean_reward = sum(reward_values) / len(reward_values) if reward_values else None
            prediction_accuracy = (
                1.0 - sum(error_values) / len(error_values) if error_values else None
            )
            components: list[tuple[float, float]] = []
            if mean_reward is not None:
                components.append((0.7, mean_reward))
            if prediction_accuracy is not None:
                components.append((0.3, prediction_accuracy))
            total_weight = sum(weight for weight, _ in components) or 1.0
            score = sum(weight * value for weight, value in components) / total_weight
            ids = list(dict.fromkeys(item for item in memory_ids.get(profile, []) if item))
            refs = list(dict.fromkeys(item for item in evidence_refs.get(profile, []) if item))
            profiles[profile] = {
                "score": max(0.0, min(1.0, score)),
                "audience_samples": len(reward_values),
                "prediction_samples": len(error_values),
                # Audience and 24h prediction episodes can describe the same publication.
                # Use the larger independent stream for maturity so one publication is not
                # double-counted merely because it produced two memory projections.
                "sample_count": max(len(reward_values), len(error_values)),
                "mean_relative_reward": mean_reward,
                "mean_prediction_accuracy": prediction_accuracy,
                "mean_pageviews_24h": (
                    sum(pageviews[profile]) / len(pageviews[profile]) if pageviews.get(profile) else None
                ),
                "memory_ids": ids,
                "evidence_refs": refs,
            }

        retrieved = [mid for value in profiles.values() for mid in value["memory_ids"]]
        self.observatory.record_retrieval(
            query=f"content strategy memory prior for topic {topic}",
            retrieved_memory_ids=retrieved,
            recall_latency_ms=(time.perf_counter() - started) * 1000.0,
            source="memory-decision-prior",
        )
        return {
            "topic": topic,
            "profiles": profiles,
            "memory_use_id": "",
            "memory_ids": list(dict.fromkeys(retrieved)),
            "evidence_refs": list(dict.fromkeys(
                ref for value in profiles.values() for ref in value["evidence_refs"]
            )),
            "causal_claim": False,
        }


__all__ = ["MemoryDecisionPrior"]
