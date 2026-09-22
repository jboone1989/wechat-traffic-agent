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
_TOPIC_STOPWORDS = frozenset({
    "a", "an", "and", "content", "experiment", "for", "in", "model", "of",
    "on", "test", "the", "to", "under", "vs", "with",
})


def _topic_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) > 1 and token not in _TOPIC_STOPWORDS
    }


def _topic_relevance(query: str, observed: str) -> float:
    """Return bounded transfer authority for lived experience across topics."""
    query = str(query or "").strip().casefold()
    observed = str(observed or "").strip().casefold()
    if query and query == observed:
        return 1.0
    left, right = _topic_terms(query), _topic_terms(observed)
    if left and right:
        overlap = len(left & right) / len(left | right)
        if overlap > 0.0:
            return min(0.9, 0.55 + 0.35 * overlap)
    return 0.25


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

        parsed: list[dict[str, Any]] = []
        for row in episodes:
            attrs = (row.get("payload") or {}).get("attributes") or {}
            source = str(attrs.get("source") or "")
            text = self._observation_text(row)
            if source == "website-audience-outcome":
                match = _AUDIENCE_RE.search(text)
                if not match:
                    continue
                outcome = (row.get("payload") or {}).get("outcome")
                try:
                    reward = float(outcome)
                except (TypeError, ValueError):
                    continue
                observed_topic = match.group("topic")
                parsed.append({
                    "kind": "AUDIENCE",
                    "profile": match.group("profile"),
                    "topic": observed_topic,
                    "relevance": _topic_relevance(topic, observed_topic),
                    "reward": max(0.0, min(1.0, reward)),
                    "pageviews": float(match.group("pageviews")),
                    "memory_id": str(row.get("memory_id") or ""),
                    "evidence_refs": [str(ref) for ref in row.get("evidence_refs") or []],
                })
            elif source == "content-action-prediction-outcome":
                match = _PREDICTION_RE.search(text)
                if not match or match.group("window") != "24h":
                    continue
                observed_topic = match.group("topic")
                parsed.append({
                    "kind": "PREDICTION",
                    "profile": match.group("profile"),
                    "topic": observed_topic,
                    "relevance": _topic_relevance(topic, observed_topic),
                    "error": max(0.0, min(1.0, float(match.group("error")))),
                    "memory_id": str(row.get("memory_id") or ""),
                    "evidence_refs": [str(ref) for ref in row.get("evidence_refs") or []],
                })

        exact = [row for row in parsed if row["relevance"] >= 0.999]
        related = [row for row in parsed if 0.5 <= row["relevance"] < 0.999]
        if exact:
            selected, scope = exact, "EXACT_TOPIC"
        elif related:
            selected, scope = related, "RELATED_TOPIC"
        else:
            selected, scope = parsed, "GLOBAL_TRANSFER"

        rewards: dict[str, list[tuple[float, float]]] = defaultdict(list)
        pageviews: dict[str, list[tuple[float, float]]] = defaultdict(list)
        errors: dict[str, list[tuple[float, float]]] = defaultdict(list)
        memory_ids: dict[str, list[str]] = defaultdict(list)
        evidence_refs: dict[str, list[str]] = defaultdict(list)

        for item in selected:
            profile = str(item["profile"])
            relevance = float(item["relevance"])
            if item["kind"] == "AUDIENCE":
                rewards[profile].append((float(item["reward"]), relevance))
                pageviews[profile].append((float(item["pageviews"]), relevance))
            else:
                errors[profile].append((float(item["error"]), relevance))
            memory_id = str(item.get("memory_id") or "")
            if memory_id:
                memory_ids[profile].append(memory_id)
            evidence_refs[profile].extend(item.get("evidence_refs") or ())

        profiles: dict[str, dict[str, Any]] = {}
        for profile in sorted(set(rewards) | set(errors)):
            reward_values = rewards.get(profile, [])
            error_values = errors.get(profile, [])
            reward_weight = sum(weight for _, weight in reward_values)
            error_weight = sum(weight for _, weight in error_values)
            mean_reward = (
                sum(value * weight for value, weight in reward_values) / reward_weight
                if reward_weight else None
            )
            prediction_accuracy = (
                1.0 - (
                    sum(value * weight for value, weight in error_values) / error_weight
                ) if error_weight else None
            )
            components: list[tuple[float, float]] = []
            if mean_reward is not None:
                components.append((0.7, mean_reward))
            if prediction_accuracy is not None:
                components.append((0.3, prediction_accuracy))
            total_weight = sum(weight for weight, _ in components) or 1.0
            raw_score = sum(weight * value for weight, value in components) / total_weight
            relevance_values = (
                [weight for _, weight in reward_values]
                + [weight for _, weight in error_values]
            )
            transfer_strength = (
                sum(relevance_values) / len(relevance_values)
                if relevance_values else 0.0
            )
            # Cross-topic experience can influence a choice, but is deliberately
            # shrunk toward neutral so it cannot masquerade as same-topic evidence.
            score = 0.5 + transfer_strength * (raw_score - 0.5)
            ids = list(dict.fromkeys(item for item in memory_ids.get(profile, []) if item))[-64:]
            refs = list(dict.fromkeys(item for item in evidence_refs.get(profile, []) if item))
            profiles[profile] = {
                "score": max(0.0, min(1.0, score)),
                "audience_samples": len(reward_values),
                "prediction_samples": len(error_values),
                "sample_count": max(len(reward_values), len(error_values)),
                "mean_relative_reward": mean_reward,
                "mean_prediction_accuracy": prediction_accuracy,
                "mean_pageviews_24h": (
                    sum(value * weight for value, weight in pageviews[profile])
                    / sum(weight for _, weight in pageviews[profile])
                    if pageviews.get(profile) else None
                ),
                "transfer_scope": scope,
                "transfer_strength": transfer_strength,
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
            "transfer_scope": scope,
            "profiles": profiles,
            "memory_use_id": "",
            "memory_ids": list(dict.fromkeys(retrieved)),
            "evidence_refs": list(dict.fromkeys(
                ref for value in profiles.values() for ref in value["evidence_refs"]
            )),
            "causal_claim": False,
        }


__all__ = ["MemoryDecisionPrior"]
