from __future__ import annotations

import hashlib
import re
from typing import Any

from .memory_semantic import SemanticRelationalMemoryService
from .memory_system import CanonicalMemoryStore


def _tokens(text: str) -> set[str]:
    value = re.sub(r"\s+", " ", str(text or "").strip()).casefold()
    ascii_terms = {
        term for term in re.findall(r"[a-z0-9_]{3,}", value)
        if term not in {"the", "and", "that", "with", "from", "this", "into", "when", "then", "only"}
    }
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", value))
    cjk_terms = {cjk[i:i + 2] for i in range(max(0, len(cjk) - 1))}
    return ascii_terms | cjk_terms


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


class ExperienceKnowledgeConsolidator:
    """Turn repeated, evidence-backed episodes into revisable semantic knowledge."""

    def __init__(self, state_root, *, store: CanonicalMemoryStore | None = None) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.semantic = SemanticRelationalMemoryService(state_root, store=self.store)

    def consolidate(
        self,
        *,
        topic: str,
        source_ref: str,
        minimum_support: int = 2,
        similarity_threshold: float = 0.34,
        episode_limit: int = 120,
    ) -> list[dict[str, Any]]:
        episodes = self.store.query_memories(
            memory_types=["EPISODIC"],
            statuses=["ACTIVE"],
            topic=topic,
            access_scope="PRIVATE_OWNER",
            limit=episode_limit,
        )
        observations: list[dict[str, Any]] = []
        for row in reversed(episodes):
            payload = dict(row.get("payload") or {})
            lesson = str(payload.get("lesson") or "").strip()
            if not lesson:
                continue
            terms = _tokens(lesson)
            if not terms:
                continue
            observations.append({
                "episode_id": str(row["memory_id"]),
                "lesson": lesson,
                "terms": terms,
            })
        clusters: list[list[dict[str, Any]]] = []
        for observation in observations:
            best_index = -1
            best_score = 0.0
            for index, cluster in enumerate(clusters):
                union_terms = set().union(*(item["terms"] for item in cluster))
                score = _jaccard(observation["terms"], union_terms)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index >= 0 and best_score >= similarity_threshold:
                clusters[best_index].append(observation)
            else:
                clusters.append([observation])

        saved: list[dict[str, Any]] = []
        for cluster in clusters:
            if len(cluster) < max(2, int(minimum_support)):
                continue
            evidence = [item["episode_id"] for item in cluster]
            # Cluster identity must remain stable as later evidence arrives. Using the
            # evolving intersection/union changes claim_key over time and can turn a
            # legitimate revision into an unrelated duplicate. The first observation
            # anchors the cluster; later evidence revises the same claim with provenance.
            signature_terms = sorted(cluster[0]["terms"])[:12]
            signature = hashlib.sha256("|".join(signature_terms).encode("utf-8")).hexdigest()[:20]
            claim_key = f"experience-knowledge:{topic}:{signature}"
            representative = max((item["lesson"] for item in cluster), key=len)
            text = "Repeated experience supports: " + representative[:1600]
            current = self.semantic.current_claim(claim_key)
            if current is not None:
                current_refs = set(str(item) for item in (current.get("evidence_refs") or []))
                if set(evidence).issubset(current_refs):
                    saved.append(current)
                    continue
            try:
                memory = self.semantic.remember_semantic(
                    text=text,
                    claim_key=claim_key,
                    source_type="EXPERIENCE_CONSOLIDATION",
                    source_ref=str(source_ref),
                    importance=min(0.96, 0.72 + 0.04 * len(cluster)),
                    confidence=min(0.98, 0.58 + 0.08 * len(cluster)),
                    evidence_refs=evidence,
                    topics=[topic, "experience-knowledge"],
                    supersedes=str(current.get("memory_id") or "") if current else "",
                    kind="EXPERIENCE_KNOWLEDGE",
                )
            except ValueError as exc:
                if str(exc) == "duplicate_memory" and current is not None:
                    saved.append(current)
                    continue
                raise
            saved.append(memory)
        return saved


__all__ = ["ExperienceKnowledgeConsolidator"]
