from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from .memory_retrieval import MemoryRouter, RecallWeights
from .memory_system import CanonicalMemoryStore


MEMORY_ARCHITECTURE_INVARIANTS = (
    "trajectory_is_immutable_history",
    "domain_state_keeps_authority",
    "derived_indexes_are_rebuildable",
    "durable_memory_has_provenance_and_temporal_validity",
    "revision_preserves_history",
    "one_memory_router_for_recall",
    "one_formation_pipeline_for_durable_writes",
    "vector_search_does_not_define_memory_truth",
)
PROTECTED_BENCHMARK_CATEGORIES = frozenset({
    "temporal", "exact", "provenance", "rebuildability",
})
SIGNAL_KINDS = frozenset({
    "RECALL_FAILURE", "STALE_MEMORY", "CONTRADICTION", "PROCEDURE_FAILURE",
    "INDEX_COST", "CONTEXT_WASTE",
})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def architecture_fingerprint() -> str:
    return hashlib.sha256(_json(MEMORY_ARCHITECTURE_INVARIANTS).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryBenchmarkCase:
    case_id: str
    query: str
    expected_ids: tuple[str, ...]
    category: str = "semantic"
    forbidden_ids: tuple[str, ...] = ()
    limit: int = 5
    memory_types: tuple[str, ...] = ()
    source_type: str | None = None
    entity: str | None = None
    topic: str | None = None
    now: datetime | None = None

    def serializable(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "expected_ids": list(self.expected_ids),
            "category": self.category,
            "forbidden_ids": list(self.forbidden_ids),
            "limit": int(self.limit),
            "memory_types": list(self.memory_types),
            "source_type": self.source_type,
            "entity": self.entity,
            "topic": self.topic,
            "now": self.now.isoformat() if self.now else None,
        }


class MemoryBenchmark:
    def __init__(self, state_root: Path, *, store: CanonicalMemoryStore | None = None) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)

    def run(
        self,
        cases: Sequence[MemoryBenchmarkCase],
        *,
        weights: RecallWeights,
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("memory benchmark requires at least one case")
        router = MemoryRouter(self.state_root, store=self.store, weights=weights)
        rows: list[dict[str, Any]] = []
        for case in cases:
            recalled = router.recall(
                case.query,
                limit=case.limit,
                now=case.now,
                memory_types=case.memory_types,
                source_type=case.source_type,
                entity=case.entity,
                topic=case.topic,
            )
            ids = [str(row.get("memory_id") or "") for row in recalled]
            rank = 0
            for expected in case.expected_ids:
                if expected in ids:
                    candidate_rank = ids.index(expected) + 1
                    rank = candidate_rank if rank == 0 else min(rank, candidate_rank)
            expected_ok = not case.expected_ids or rank > 0
            forbidden = sorted(set(ids) & set(case.forbidden_ids))
            pass_case = expected_ok and not forbidden
            reciprocal_rank = (1.0 / rank) if rank else (1.0 if not case.expected_ids else 0.0)
            rows.append({
                "case_id": case.case_id,
                "category": case.category,
                "passed": pass_case,
                "rank": rank,
                "reciprocal_rank": reciprocal_rank,
                "returned_ids": ids,
                "forbidden_returned": forbidden,
            })
        hit_rate = sum(1 for row in rows if row["passed"]) / len(rows)
        mrr = sum(float(row["reciprocal_rank"]) for row in rows) / len(rows)
        categories: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            categories.setdefault(str(row["category"]), []).append(row)
        category_scores = {
            name: sum(1 for row in values if row["passed"]) / len(values)
            for name, values in categories.items()
        }
        score = hit_rate * 0.75 + mrr * 0.25
        return {
            "score": score,
            "hit_rate": hit_rate,
            "mrr": mrr,
            "case_count": len(rows),
            "category_scores": category_scores,
            "cases": rows,
        }

    @staticmethod
    def fingerprint(cases: Sequence[MemoryBenchmarkCase]) -> str:
        payload = [case.serializable() for case in cases]
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


class MemoryEvolutionEngine:
    """M11 evidence-gated evolution of recall policy under frozen memory invariants."""

    def __init__(
        self,
        state_root: Path,
        *,
        store: CanonicalMemoryStore | None = None,
        baseline_weights: RecallWeights | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.store = store or CanonicalMemoryStore(self.state_root)
        self.root = self.store.root / "evolution"
        self.root.mkdir(parents=True, exist_ok=True)
        self.experiments_root = self.root / "experiments"
        self.experiments_root.mkdir(parents=True, exist_ok=True)
        self.signal_path = self.root / "signals.jsonl"
        self.policy_path = self.store.root / "active_recall_policy.json"
        self.baseline_weights = baseline_weights
        self.benchmark = MemoryBenchmark(self.state_root, store=self.store)

    def active_weights(self) -> RecallWeights:
        if self.baseline_weights is not None:
            return self.baseline_weights
        if not self.policy_path.exists():
            return RecallWeights()
        try:
            raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
            return RecallWeights.from_mapping(dict(raw.get("weights") or {}))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return RecallWeights()

    def observe_signal(
        self,
        kind: str,
        *,
        evidence_ref: str,
        detail: str,
        metric: float | None = None,
    ) -> dict[str, Any]:
        normalized = str(kind).strip().upper()
        if normalized not in SIGNAL_KINDS:
            raise ValueError("unsupported memory evolution signal")
        if not str(evidence_ref).strip():
            raise ValueError("memory evolution signal requires evidence_ref")
        row = {
            "signal_id": "memory-signal:" + uuid4().hex,
            "kind": normalized,
            "evidence_ref": str(evidence_ref),
            "detail": str(detail)[:1000],
            "metric": float(metric) if metric is not None else None,
            "observed_at": _utc_iso(),
        }
        with self.signal_path.open("a", encoding="utf-8") as handle:
            handle.write(_json(row) + "\n")
        return row

    def recent_signals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.signal_path.exists():
            return []
        rows = [
            json.loads(line) for line in self.signal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return rows[-max(1, min(int(limit), 1000)):]

    def propose_from_signals(self, *, limit: int = 100) -> RecallWeights:
        current = self.active_weights()
        values = current.as_dict()
        kinds = [str(row.get("kind") or "") for row in self.recent_signals(limit=limit)]
        recall_failures = kinds.count("RECALL_FAILURE")
        contradictions = kinds.count("CONTRADICTION")
        context_waste = kinds.count("CONTEXT_WASTE") + kinds.count("INDEX_COST")
        if recall_failures:
            values["vector_weight"] = min(4.0, float(values["vector_weight"]) + 0.15 * recall_failures)
            values["lexical_weight"] = min(4.0, float(values["lexical_weight"]) + 0.05 * recall_failures)
        if contradictions:
            values["structured_weight"] = min(4.0, float(values["structured_weight"]) + 0.1 * contradictions)
        if context_waste:
            values["search_multiplier"] = max(
                2, int(values["search_multiplier"]) - min(3, context_waste)
            )
        if "STALE_MEMORY" in kinds:
            values["decay_enabled"] = True
        return RecallWeights.from_mapping(values)

    def evaluate_candidate(
        self,
        challenger: RecallWeights,
        cases: Sequence[MemoryBenchmarkCase],
        *,
        evidence_refs: Iterable[str],
        rationale: str,
    ) -> dict[str, Any]:
        evidence = list(dict.fromkeys(
            str(item).strip() for item in evidence_refs if str(item).strip()
        ))
        if not evidence:
            raise ValueError("memory evolution experiment requires evidence_refs")
        baseline = self.active_weights()
        baseline_result = self.benchmark.run(cases, weights=baseline)
        challenger_result = self.benchmark.run(cases, weights=challenger)
        experiment_id = "memory-experiment:" + uuid4().hex
        row = {
            "experiment_id": experiment_id,
            "status": "EVALUATED",
            "created_at": _utc_iso(),
            "architecture_fingerprint": architecture_fingerprint(),
            "benchmark_fingerprint": self.benchmark.fingerprint(cases),
            "rationale": str(rationale)[:1200],
            "evidence_refs": evidence,
            "baseline_weights": baseline.as_dict(),
            "challenger_weights": challenger.as_dict(),
            "baseline_result": baseline_result,
            "challenger_result": challenger_result,
        }
        self._write_experiment(row)
        return row

    def decide(self, experiment_id: str) -> dict[str, Any]:
        row = self._read_experiment(experiment_id)
        if row.get("status") not in {"EVALUATED", "PROMOTED", "REJECTED"}:
            raise ValueError("experiment is not evaluable")
        if row.get("status") in {"PROMOTED", "REJECTED"}:
            return row
        if str(row.get("architecture_fingerprint") or "") != architecture_fingerprint():
            raise ValueError("memory architecture invariants changed during experiment")
        baseline = dict(row.get("baseline_result") or {})
        challenger = dict(row.get("challenger_result") or {})
        baseline_categories = dict(baseline.get("category_scores") or {})
        challenger_categories = dict(challenger.get("category_scores") or {})
        regressions = []
        for category in PROTECTED_BENCHMARK_CATEGORIES:
            if category not in baseline_categories:
                continue
            if float(challenger_categories.get(category, 0.0)) + 1e-12 < float(baseline_categories[category]):
                regressions.append(category)
        improved = float(challenger.get("score") or 0.0) > float(baseline.get("score") or 0.0) + 1e-12
        if improved and not regressions:
            decision = "PROMOTED"
            policy = {
                "policy_schema": 1,
                "promoted_at": _utc_iso(),
                "experiment_id": experiment_id,
                "architecture_fingerprint": architecture_fingerprint(),
                "benchmark_fingerprint": row["benchmark_fingerprint"],
                "weights": dict(row["challenger_weights"]),
            }
            tmp = self.policy_path.with_suffix(".json.tmp")
            tmp.write_text(_json(policy) + "\n", encoding="utf-8")
            tmp.replace(self.policy_path)
        else:
            decision = "REJECTED"
        row["status"] = decision
        row["decided_at"] = _utc_iso()
        row["protected_regressions"] = regressions
        row["improved"] = improved
        self._write_experiment(row)
        return row

    def _experiment_path(self, experiment_id: str) -> Path:
        safe = str(experiment_id).replace(":", "-").replace("/", "-")
        return self.experiments_root / (safe + ".json")

    def _write_experiment(self, row: Mapping[str, Any]) -> None:
        path = self._experiment_path(str(row.get("experiment_id") or ""))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_json(dict(row)) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _read_experiment(self, experiment_id: str) -> dict[str, Any]:
        path = self._experiment_path(experiment_id)
        if not path.exists():
            raise FileNotFoundError(experiment_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid memory experiment")
        return value


__all__ = [
    "MEMORY_ARCHITECTURE_INVARIANTS", "MemoryBenchmark", "MemoryBenchmarkCase",
    "MemoryEvolutionEngine", "PROTECTED_BENCHMARK_CATEGORIES", "SIGNAL_KINDS",
    "architecture_fingerprint",
]
