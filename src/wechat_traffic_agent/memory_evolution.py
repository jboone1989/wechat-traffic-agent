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
    access_scope: str | None = None
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
            "access_scope": self.access_scope,
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
                access_scope=case.access_scope,
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
        self.cycle_state_path = self.root / "cycle_state.json"
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

    def _signal_rows(self) -> list[dict[str, Any]]:
        if not self.signal_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.signal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def recent_signals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._signal_rows()
        return rows[-max(1, min(int(limit), 1000)):]

    def _propose_from_rows(self, rows: Sequence[Mapping[str, Any]]) -> RecallWeights:
        current = self.active_weights()
        values = current.as_dict()
        kinds = [str(row.get("kind") or "") for row in rows]
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

    def propose_from_signals(self, *, limit: int = 100) -> RecallWeights:
        return self._propose_from_rows(self.recent_signals(limit=limit))

    @staticmethod
    def _semantic_probe(row: Mapping[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        if str(row.get("memory_type") or "") == "PROCEDURAL":
            parts = [
                str(payload.get("name") or ""),
                " ".join(str(item) for item in payload.get("capabilities") or []),
                " ".join(str(item) for item in payload.get("verification") or []),
            ]
            value = " ".join(part for part in parts if part.strip())
            if value.strip():
                return value[:280]
        if str(row.get("memory_type") or "") == "EPISODIC":
            parts = [
                str(payload.get("goal") or ""),
                str(payload.get("lesson") or ""),
                str(payload.get("outcome") or ""),
            ]
            value = " ".join(part for part in parts if part.strip())
            if value.strip():
                return value[:280]
        text = str(row.get("text") or "")
        ascii_terms = [
            term for term in __import__("re").findall(r"[a-zA-Z0-9_]{4,}", text)
            if term.casefold() not in {"this", "that", "with", "from", "when", "then", "into", "only"}
        ]
        cjk = "".join(__import__("re").findall(r"[\u3400-\u9fff]", text))
        cjk_terms = [cjk[i:i + 2] for i in range(0, max(0, len(cjk) - 1), 2)]
        return " ".join([*ascii_terms[:10], *cjk_terms[:8]])[:280]

    def build_self_supervised_benchmark(self, *, max_cases: int = 24) -> list[MemoryBenchmarkCase]:
        rows = self.store.query_memories(
            memory_types=["SEMANTIC", "EPISODIC", "PROCEDURAL"],
            statuses=["ACTIVE"],
            access_scope="PRIVATE_OWNER",
            limit=5000,
        )
        rows.sort(
            key=lambda row: (
                float(row.get("importance") or 0.0),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        cases: list[MemoryBenchmarkCase] = []
        for row in rows:
            if len(cases) >= max(2, int(max_cases)):
                break
            memory_id = str(row.get("memory_id") or "")
            text = str(row.get("text") or "").strip()
            if not memory_id or len(text) < 8:
                continue
            cases.append(MemoryBenchmarkCase(
                case_id="self-exact:" + memory_id,
                query=text[:320],
                expected_ids=(memory_id,),
                category="exact",
                limit=5,
                memory_types=(str(row.get("memory_type") or "SEMANTIC"),),
                access_scope="PRIVATE_OWNER",
            ))
            if len(cases) >= max_cases:
                break
            probe = self._semantic_probe(row)
            if probe and probe.casefold() != text[: len(probe)].casefold():
                cases.append(MemoryBenchmarkCase(
                    case_id="self-semantic:" + memory_id,
                    query=probe,
                    expected_ids=(memory_id,),
                    category="semantic",
                    limit=5,
                    memory_types=(str(row.get("memory_type") or "SEMANTIC"),),
                    access_scope="PRIVATE_OWNER",
                ))
        return cases[:max_cases]

    def _read_cycle_state(self) -> dict[str, Any]:
        if not self.cycle_state_path.exists():
            return {"processed_signal_count": 0}
        try:
            value = json.loads(self.cycle_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"processed_signal_count": 0}
        return value if isinstance(value, dict) else {"processed_signal_count": 0}

    def _write_cycle_state(self, row: Mapping[str, Any]) -> None:
        tmp = self.cycle_state_path.with_suffix(".json.tmp")
        tmp.write_text(_json(dict(row)) + "\n", encoding="utf-8")
        tmp.replace(self.cycle_state_path)

    def run_cycle(self, *, minimum_cases: int = 4) -> dict[str, Any]:
        rows = self._signal_rows()
        state = self._read_cycle_state()
        processed = max(0, min(int(state.get("processed_signal_count") or 0), len(rows)))
        pending = rows[processed:]
        if not pending:
            return {"status": "NO_NEW_SIGNALS", "processed_signal_count": processed}
        cases = self.build_self_supervised_benchmark()
        if len(cases) < max(1, int(minimum_cases)):
            return {
                "status": "INSUFFICIENT_BENCHMARK",
                "pending_signal_count": len(pending),
                "benchmark_case_count": len(cases),
            }
        baseline = self.active_weights()
        challenger = self._propose_from_rows(pending)
        if challenger.as_dict() == baseline.as_dict():
            out = {
                "status": "NO_POLICY_CHANGE",
                "processed_signal_count": len(rows),
                "processed_signal_ids": [str(row.get("signal_id") or "") for row in pending],
                "completed_at": _utc_iso(),
            }
            self._write_cycle_state(out)
            return out
        experiment = self.evaluate_candidate(
            challenger,
            cases,
            evidence_refs=[str(row.get("signal_id") or row.get("evidence_ref") or "") for row in pending],
            rationale="automatic memory recall-policy experiment from observed memory failures",
        )
        decision = self.decide(str(experiment["experiment_id"]))
        out = {
            "status": str(decision.get("status") or ""),
            "experiment_id": str(experiment["experiment_id"]),
            "processed_signal_count": len(rows),
            "processed_signal_ids": [str(row.get("signal_id") or "") for row in pending],
            "baseline_score": float((experiment.get("baseline_result") or {}).get("score") or 0.0),
            "challenger_score": float((experiment.get("challenger_result") or {}).get("score") or 0.0),
            "protected_regressions": list(decision.get("protected_regressions") or []),
            "completed_at": _utc_iso(),
        }
        self._write_cycle_state(out)
        return out

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
