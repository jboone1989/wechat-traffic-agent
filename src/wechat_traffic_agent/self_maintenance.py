"""Evidence-first self-maintenance for persistent Content Agent failures."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from agent_runtime.agency.diagnosis import (
    DiagnosticCase,
    DiagnosticMemory,
    ObservationKind,
)
from agent_runtime.evolution import EvolutionFactKind


_KNOWN_COGNITION_ERROR = "BELIEF_CHANGED requires a persisted worldview change"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _cycle_ref(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


class SelfMaintenanceService:
    """Turn production failures into durable diagnosis and evolution evidence."""

    def __init__(
        self,
        *,
        state_root: Path,
        evolution_runtime: Any | None,
        capability_runtime: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "self_maintenance"
        self.evolution_runtime = evolution_runtime
        self.capability_runtime = capability_runtime
        self.memory = DiagnosticMemory(self.root)

    def ingest_failure(self, failure: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        if str(failure.get("status") or "") != "ERROR":
            return {"status": "IGNORED_NON_ERROR"}
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self-maintenance time requires timezone awareness")

        error_type = str(failure.get("error_type") or "UNKNOWN")
        message = " ".join(str(failure.get("message") or "").split())[:1000]
        cycle = _cycle_ref(failure.get("cycle"))
        signature = sha256(f"{error_type}\0{message}".encode("utf-8")).hexdigest()
        problem_id = "self-problem:" + signature[:24]
        path = self.root / "problems" / f"{signature[:24]}.json"
        previous: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                previous = {}
        seen_cycles = [str(x) for x in previous.get("cycles") or () if str(x)]
        if cycle in seen_cycles:
            return {
                "status": "ALREADY_RECORDED",
                "problem_id": problem_id,
                "problem_status": previous.get("status"),
            }

        evidence_refs = [
            f"content-cycle:{cycle}:result",
            "error-signature:sha256:" + signature,
        ]
        case = self._diagnose(
            problem_id=problem_id,
            error_type=error_type,
            message=message,
            cycle=cycle,
            evidence_refs=evidence_refs,
            now=now,
        )
        case_path = self.memory.persist_case(case)
        proposal = self._proposal_for(error_type=error_type, message=message, cycle=cycle)
        status = "REPAIR_PROPOSED" if proposal is not None else "DIAGNOSING"
        cycles = [*seen_cycles, cycle][-32:]
        payload = {
            "schema_version": "self-problem.v1",
            "problem_id": problem_id,
            "status": status,
            "symptom": message or error_type,
            "error_type": error_type,
            "failed_goal": "complete one eligible content cognition cycle",
            "affected_capability": "content.cognition",
            "first_seen_at": previous.get("first_seen_at") or now.astimezone(timezone.utc).isoformat(),
            "last_seen_at": now.astimezone(timezone.utc).isoformat(),
            "occurrences": int(previous.get("occurrences") or 0) + 1,
            "cycles": cycles,
            "diagnostic_case": str(case_path),
            "evidence_refs": evidence_refs,
            "repair_proposal": proposal,
        }
        _atomic_json(path, payload)
        _atomic_json(self.root / "latest.json", payload)
        evolution = self._observe_evolution(payload)
        return {
            "status": "RECORDED",
            "problem_id": problem_id,
            "problem_status": status,
            "repair_proposal": proposal,
            "evolution": evolution,
        }

    def _diagnose(
        self,
        *,
        problem_id: str,
        error_type: str,
        message: str,
        cycle: str,
        evidence_refs: list[str],
        now: datetime,
    ) -> DiagnosticCase:
        case = DiagnosticCase.start(
            symptom=f"{error_type}: {message}",
            scope_ladder=("content-cycle", "content-cognition-contract", "content-agent"),
            tags=("production-failure", "content-cognition", error_type.lower()),
            now=now,
        )
        observation = case.observe(
            statement=f"Cycle {cycle} ended with {error_type}: {message}",
            scope="content-cycle",
            kind=ObservationKind.FAILURE,
            evidence_refs=evidence_refs,
            now=now,
        )
        if _KNOWN_COGNITION_ERROR in message:
            contract_observation = case.observe(
                statement="The cognition contract rejected BELIEF_CHANGED because the durable worldview had no persisted change.",
                scope="content-cognition-contract",
                kind=ObservationKind.FAILURE,
                evidence_refs=evidence_refs,
                now=now,
            )
            hypothesis = case.hypothesize(
                statement="Model narration claimed a durable belief transition that the persisted worldview did not contain.",
                scope="content-cognition-contract",
                predicts="The durable worldview record is not FORMED or REVISED while cognition_state says BELIEF_CHANGED.",
                falsifier="The persisted worldview record proves a FORMATION or REVISION for the same cycle.",
            )
            case.support(hypothesis.hypothesis_id, [contract_observation.observation_id])
            case.conclude(
                hypothesis.hypothesis_id,
                summary="The failure is a cognition narration/state consistency mismatch, not a missing worldview write.",
                now=now,
            )
            case.record_remediation(
                "Treat durable worldview state as authoritative and normalize an impossible BELIEF_CHANGED narration instead of terminating the cycle."
            )
        return case

    @staticmethod
    def _proposal_for(*, error_type: str, message: str, cycle: str) -> dict[str, Any] | None:
        if error_type != "ValueError" or _KNOWN_COGNITION_ERROR not in message:
            return None
        proposal_id = "repair-proposal:" + sha256(
            f"{error_type}\0{message}\0content_goal_loop.py".encode("utf-8")
        ).hexdigest()[:24]
        return {
            "schema_version": "repair-proposal.v1",
            "proposal_id": proposal_id,
            "status": "PROPOSED",
            "repair_kind": "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW",
            "target_files": ["src/wechat_traffic_agent/content_goal_loop.py"],
            "verification": [
                "focused regression for deferred worldview plus overstated BELIEF_CHANGED",
                "existing persisted-worldview public-trace regression",
                "full test suite before release",
            ],
            "risk": "LOW",
            "reversible": True,
            "source_cycle": cycle,
        }

    def _observe_evolution(self, problem: dict[str, Any]) -> dict[str, Any]:
        runtime = self.evolution_runtime
        observer = getattr(runtime, "observe_evolution_fact", None)
        if not callable(observer):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        try:
            fact = observer(
                kind=EvolutionFactKind.FAILURE,
                subject="content-cognition-self-maintenance",
                summary=str(problem["symptom"]),
                severity=0.85,
                evidence_refs=list(problem.get("evidence_refs") or ()),
                recurrence_key=f"content-cognition:{problem['error_type']}:{problem['problem_id']}",
                need_hint="Make content cognition failures diagnosable and recoverable without human intervention.",
                target_capability="self-maintenance.content-cognition",
                metadata={
                    "problem_id": problem["problem_id"],
                    "repair_proposed": bool(problem.get("repair_proposal")),
                },
            )
            persist = getattr(runtime, "persist", None)
            if callable(persist):
                persist()
            return {"status": "RECORDED", "fact_id": str(getattr(fact, "fact_id", ""))}
        except Exception as exc:  # self-maintenance must not hide the original failure
            return {"status": "ERROR", "error_type": type(exc).__name__}

    def review(self) -> dict[str, Any]:
        folder = self.root / "problems"
        rows: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        open_rows = [
            row for row in rows
            if str(row.get("status") or "") not in {"VERIFIED", "CLOSED", "REJECTED"}
        ]
        open_rows.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
        return {
            "status": "OK",
            "open_count": len(open_rows),
            "repair_proposed_count": sum(1 for row in open_rows if row.get("repair_proposal")),
            "problems": [
                {
                    "problem_id": row.get("problem_id"),
                    "status": row.get("status"),
                    "error_type": row.get("error_type"),
                    "occurrences": row.get("occurrences"),
                    "last_seen_at": row.get("last_seen_at"),
                    "repair_proposal": row.get("repair_proposal"),
                }
                for row in open_rows[:12]
            ],
        }

    def backfill_recent_failures(self, *, now: datetime, limit: int = 24) -> dict[str, Any]:
        cycles_root = self.state_root / "content" / "continuous" / "cycles"
        paths = sorted(
            cycles_root.glob("*/result.json") if cycles_root.exists() else (),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: max(1, int(limit))]
        recorded = 0
        duplicates = 0
        for path in reversed(paths):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict) or result.get("status") != "ERROR":
                continue
            outcome = self.ingest_failure(result, now=now)
            if outcome.get("status") == "RECORDED":
                recorded += 1
            elif outcome.get("status") == "ALREADY_RECORDED":
                duplicates += 1
        return {
            "status": "OK",
            "scanned": len(paths),
            "recorded": recorded,
            "duplicates": duplicates,
        }


__all__ = ["SelfMaintenanceService"]
