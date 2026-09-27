from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4


FAILURE_KINDS = frozenset({
    "MISSED_RECALL",
    "WRONG_RECALL",
    "STALE_RECALL",
    "CONFLICT",
    "NO_USEFUL_MEMORY",
    "SALIENCE_MISMATCH",
})
POSITIVE_KINDS = frozenset({"MEMORY_HELPED"})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class MemoryLifeObservatory:
    """Append-only evidence about how memory was actually used.

    This is deliberately outside canonical memory. It is an observability ledger used to
    measure retrieval/decision quality and to feed bounded Memory Evolution candidates.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "memory_system" / "life_observatory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "memory_use_events.jsonl"

    def record(
        self,
        *,
        event_type: str,
        query: str = "",
        retrieved_memory_ids: Iterable[str] = (),
        used_memory_ids: Iterable[str] = (),
        decision_ref: str = "",
        action_ref: str = "",
        outcome_ref: str = "",
        outcome_success: bool | None = None,
        failure_kind: str = "",
        detail: str = "",
        recall_latency_ms: float | None = None,
        evidence_refs: Iterable[str] = (),
        parent_use_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        failure = str(failure_kind or "").strip().upper()
        if failure and failure not in FAILURE_KINDS:
            raise ValueError("unsupported memory failure kind")
        latency = None if recall_latency_ms is None else max(0.0, float(recall_latency_ms))
        row = {
            "memory_use_id": "memory-use:" + uuid4().hex,
            "event_type": str(event_type or "RETRIEVAL").strip().upper(),
            "observed_at": _utc_iso(),
            "query": str(query or "")[:1600],
            "retrieved_memory_ids": list(dict.fromkeys(
                str(item).strip() for item in retrieved_memory_ids if str(item).strip()
            )),
            "used_memory_ids": list(dict.fromkeys(
                str(item).strip() for item in used_memory_ids if str(item).strip()
            )),
            "decision_ref": str(decision_ref or ""),
            "action_ref": str(action_ref or ""),
            "outcome_ref": str(outcome_ref or ""),
            "outcome_success": None if outcome_success is None else bool(outcome_success),
            "failure_kind": failure,
            "detail": str(detail or "")[:1600],
            "recall_latency_ms": latency,
            "evidence_refs": list(dict.fromkeys(
                str(item).strip() for item in evidence_refs if str(item).strip()
            )),
            "parent_use_id": str(parent_use_id or ""),
            "metadata": dict(metadata or {}),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return row

    def record_retrieval(
        self,
        *,
        query: str,
        retrieved_memory_ids: Iterable[str],
        recall_latency_ms: float,
        source: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record(
            event_type="RETRIEVAL",
            query=query,
            retrieved_memory_ids=retrieved_memory_ids,
            recall_latency_ms=recall_latency_ms,
            metadata={**dict(metadata or {}), "source": str(source)},
        )

    def record_decision_use(
        self,
        *,
        query: str,
        retrieved_memory_ids: Iterable[str],
        used_memory_ids: Iterable[str],
        decision_ref: str,
        evidence_refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.record(
            event_type="DECISION_USE",
            query=query,
            retrieved_memory_ids=retrieved_memory_ids,
            used_memory_ids=used_memory_ids,
            decision_ref=decision_ref,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def record_failure(
        self,
        kind: str,
        *,
        query: str,
        retrieved_memory_ids: Iterable[str] = (),
        detail: str,
        evidence_refs: Iterable[str],
    ) -> dict[str, Any]:
        return self.record(
            event_type="FAILURE",
            query=query,
            retrieved_memory_ids=retrieved_memory_ids,
            failure_kind=kind,
            detail=detail,
            evidence_refs=evidence_refs,
        )

    def settle(
        self,
        use_id: str,
        *,
        outcome_ref: str,
        outcome_success: bool,
        memory_helped: bool | None,
        evidence_ref: str,
        detail: str = "",
    ) -> dict[str, Any]:
        return self.record(
            event_type="SETTLEMENT",
            outcome_ref=outcome_ref,
            outcome_success=outcome_success,
            failure_kind="" if memory_helped is not False else "NO_USEFUL_MEMORY",
            detail=("MEMORY_HELPED: " + detail) if memory_helped is True else detail,
            evidence_refs=[evidence_ref],
            parent_use_id=use_id,
            metadata={"memory_helped": memory_helped},
        )

    def rows(self, *, limit: int = 5000) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows[-max(1, min(int(limit), 100000)):]

    def latest_unsettled_retrieval(self) -> dict[str, Any] | None:
        rows = self.rows(limit=10000)
        settled = {
            str(row.get("parent_use_id") or "")
            for row in rows
            if row.get("event_type") == "SETTLEMENT" and str(row.get("parent_use_id") or "")
        }
        for row in reversed(rows):
            if row.get("event_type") != "RETRIEVAL":
                continue
            use_id = str(row.get("memory_use_id") or "")
            if use_id and use_id not in settled:
                return row
        return None

    def decision_use(self, decision_ref: str, *, cycle: str = "") -> dict[str, Any] | None:
        decision_ref = str(decision_ref).strip()
        cycle = str(cycle).strip()
        if not decision_ref:
            return None
        for row in reversed(self.rows(limit=10000)):
            if row.get("event_type") != "DECISION_USE":
                continue
            if str(row.get("decision_ref") or "") != decision_ref:
                continue
            if cycle and str((row.get("metadata") or {}).get("cycle") or "") != cycle:
                continue
            return row
        return None

    def metrics(self, *, limit: int = 5000) -> dict[str, Any]:
        rows = self.rows(limit=limit)
        failures = Counter(str(row.get("failure_kind") or "") for row in rows if row.get("failure_kind"))
        retrievals = [row for row in rows if row.get("event_type") == "RETRIEVAL"]
        decisions = [row for row in rows if row.get("event_type") == "DECISION_USE"]
        settlements = [row for row in rows if row.get("event_type") == "SETTLEMENT"]
        latencies = [
            float(row["recall_latency_ms"]) for row in retrievals
            if isinstance(row.get("recall_latency_ms"), (int, float))
        ]
        helped = sum(1 for row in settlements if (row.get("metadata") or {}).get("memory_helped") is True)
        hurt = sum(1 for row in settlements if (row.get("metadata") or {}).get("memory_helped") is False)
        settled_known = helped + hurt
        return {
            "event_count": len(rows),
            "retrieval_count": len(retrievals),
            "decision_use_count": len(decisions),
            "settlement_count": len(settlements),
            "failure_counts": dict(sorted(failures.items())),
            "mean_recall_latency_ms": (
                sum(latencies) / len(latencies) if latencies else None
            ),
            "memory_helped_count": helped,
            "memory_hurt_count": hurt,
            "memory_help_rate": (helped / settled_known) if settled_known else None,
            "causal_claim": False,
            "interpretation": "observed memory-use/outcome association; not causal attribution",
        }


class MemoryRecallTimer:
    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.elapsed_ms = (time.perf_counter() - self.started) * 1000.0
        return False


__all__ = ["FAILURE_KINDS", "MemoryLifeObservatory", "MemoryRecallTimer", "POSITIVE_KINDS"]
