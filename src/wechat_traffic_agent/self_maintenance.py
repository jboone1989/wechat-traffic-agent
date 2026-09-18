"""Evidence-first self-maintenance for persistent Content Agent failures."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from agent_runtime.agency.diagnosis import (
    DiagnosticCase,
    DiagnosticMemory,
    ObservationKind,
)
from agent_runtime.evolution import EvolutionFactKind


_KNOWN_COGNITION_ERROR = "BELIEF_CHANGED requires a persisted worldview change"
_KNOWN_MODEL_JSON_ERROR = "backend did not return a JSON object"
_KNOWN_RECOVERY_INPUT_CHANGE = "model phase input changed during recovery"


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


_TRACE_FRAME = re.compile(r'File "[^"]*/([^/"]+\.py)", line \d+, in ([^\n]+)')


def _stable_failure_site(state_root: Path, cycle: str) -> str:
    path = Path(state_root) / "content" / "continuous" / "cycles" / cycle / "error_diagnostic.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return "unknown"
    trace = str(payload.get("traceback") or "") if isinstance(payload, dict) else ""
    frames = [
        f"{name}:{func.strip()}"
        for name, func in _TRACE_FRAME.findall(trace)
        if not (name == "content_loop.py" and func.strip() == "run_once")
        and not (name == "content_service.py" and func.strip() == "_extract_json")
    ]
    if not frames:
        return "unknown"
    return ">".join(frames[-3:])


def _read_last_seen(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return ""
    return str(payload.get("last_seen_at") or "") if isinstance(payload, dict) else ""


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
        failure_site = str(failure.get("failure_site") or _stable_failure_site(self.state_root, cycle))
        signature = sha256(
            f"{error_type}\0{message}\0{failure_site}".encode("utf-8")
        ).hexdigest()
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
            failure_site=failure_site,
            evidence_refs=evidence_refs,
            now=now,
        )
        case_path = self.memory.persist_case(case)
        proposal = self._proposal_for(
            error_type=error_type,
            message=message,
            cycle=cycle,
            failure_site=failure_site,
        )
        status = "REPAIR_PROPOSED" if proposal is not None else "DIAGNOSING"
        cycles = [*seen_cycles, cycle][-32:]
        payload = {
            "schema_version": "self-problem.v1",
            "problem_id": problem_id,
            "status": status,
            "symptom": message or error_type,
            "error_type": error_type,
            "failure_site": failure_site,
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
        failure_site: str,
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
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_explorer.py:filter_for_goal" in failure_site:
            contract_observation = case.observe(
                statement="A bounded cognition classifier returned no usable JSON object after its repair attempt.",
                scope="content-cognition-contract",
                kind=ObservationKind.FAILURE,
                evidence_refs=evidence_refs,
                now=now,
            )
            hypothesis = case.hypothesize(
                statement="A non-authoritative relevance classifier is allowed to terminate the whole content cycle when its model output is malformed.",
                scope="content-cognition-contract",
                predicts="The failed cycle traceback ends in the relevance filter after repeated malformed model responses.",
                falsifier="The malformed response came from an authoritative state transition whose failure cannot safely degrade to no-match.",
            )
            case.support(hypothesis.hypothesis_id, [contract_observation.observation_id])
            case.conclude(
                hypothesis.hypothesis_id,
                summary="The relevance gate should fail closed to no-match while preserving the classifier failure as evidence.",
                now=now,
            )
            case.record_remediation(
                "After one bounded repair attempt, preserve invalid-output evidence and return no admitted matches instead of aborting the full cycle."
            )
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_loop.py:_structured" in failure_site:
            contract_observation = case.observe(
                statement="The bounded evidence selector returned no usable JSON object after its repair attempt.",
                scope="content-cognition-contract",
                kind=ObservationKind.FAILURE,
                evidence_refs=evidence_refs,
                now=now,
            )
            hypothesis = case.hypothesize(
                statement="Malformed semantic selection output can terminate a cycle even though the admissible evidence set is already known.",
                scope="content-cognition-contract",
                predicts="The traceback reaches content_goal_loop._run through content_loop._structured during evidence selection.",
                falsifier="No admissible supplied evidence exists from which a conservative deterministic selection can be made.",
            )
            case.support(hypothesis.hypothesis_id, [contract_observation.observation_id])
            case.conclude(
                hypothesis.hypothesis_id,
                summary="Evidence selection can degrade to a deterministic supplied-evidence fallback without weakening the evidence boundary.",
                now=now,
            )
            case.record_remediation(
                "Bind only supplied evidence, preserve authoritative topic constraints and title de-duplication, and record that model selection failed."
            )
        elif _KNOWN_RECOVERY_INPUT_CHANGE in message:
            contract_observation = case.observe(
                statement="A persisted model receipt no longer matches the phase input after runtime code changed.",
                scope="content-cognition-contract",
                kind=ObservationKind.FAILURE,
                evidence_refs=evidence_refs,
                now=now,
            )
            hypothesis = case.hypothesize(
                statement="A stale partial cycle is being treated as a fresh runtime failure instead of an invalidated recovery attempt.",
                scope="content-cognition-contract",
                predicts="The mismatch appears only while replaying persisted model-call receipts from an interrupted earlier cycle.",
                falsifier="The same input mismatch occurs in a new cycle with no persisted model receipts.",
            )
            case.support(hypothesis.hypothesis_id, [contract_observation.observation_id])
            case.conclude(
                hypothesis.hypothesis_id,
                summary="The stale cycle must be invalidated without reusing or replaying mismatched model calls.",
                now=now,
            )
            case.record_remediation(
                "Preserve the diagnostic receipt, mark the stale cycle RECOVERY_INVALIDATED, and let a future fresh cycle restart cognition."
            )
        return case

    @staticmethod
    def _proposal_for(
        *, error_type: str, message: str, cycle: str, failure_site: str = "unknown"
    ) -> dict[str, Any] | None:
        if error_type != "ValueError":
            return None
        if _KNOWN_COGNITION_ERROR in message:
            target_file = "src/wechat_traffic_agent/content_goal_loop.py"
            repair_kind = "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW"
            verification = [
                "focused regression for deferred worldview plus overstated BELIEF_CHANGED",
                "existing persisted-worldview public-trace regression",
                "full test suite before release",
            ]
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_explorer.py:filter_for_goal" in failure_site:
            target_file = "src/wechat_traffic_agent/content_explorer.py"
            repair_kind = "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT"
            verification = [
                "two malformed classifier responses return zero admitted matches",
                "invented IDs and quotes remain rejected",
                "full content regression before release",
            ]
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_loop.py:_structured" in failure_site:
            target_file = "src/wechat_traffic_agent/content_goal_loop.py"
            repair_kind = "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION"
            verification = [
                "two malformed SELECT_EVIDENCE responses bind only supplied evidence",
                "fallback keeps authoritative topic constraints and title de-duplication",
                "full content regression before release",
            ]
        elif _KNOWN_RECOVERY_INPUT_CHANGE in message:
            target_file = "src/wechat_traffic_agent/content_loop.py"
            repair_kind = "INVALIDATE_STALE_MODEL_RECOVERY_INPUT_CHANGE"
            verification = [
                "mismatched persisted model receipt becomes RECOVERY_INVALIDATED rather than ERROR",
                "no mismatched model response is reused or replayed",
                "future fresh cycle remains eligible",
            ]
        else:
            return None
        proposal_id = "repair-proposal:" + sha256(
            f"{error_type}\0{message}\0{target_file}".encode("utf-8")
        ).hexdigest()[:24]
        return {
            "schema_version": "repair-proposal.v1",
            "proposal_id": proposal_id,
            "status": "PROPOSED",
            "repair_kind": repair_kind,
            "target_files": [target_file],
            "verification": verification,
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

    def reindex_legacy_problems_by_callsite(self, *, now: datetime) -> dict[str, int]:
        folder = self.root / "problems"
        migrated_cycles = 0
        legacy_rows = 0
        new_problem_ids: set[str] = set()
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("failure_site"):
                continue
            if str(payload.get("status") or "") in {"VERIFIED", "CLOSED", "REJECTED", "LEGACY_AGGREGATE"}:
                continue
            migrated_for_row: set[str] = set()
            for cycle in [str(x) for x in payload.get("cycles") or () if str(x)]:
                failure_site = _stable_failure_site(self.state_root, cycle)
                if failure_site == "unknown":
                    continue
                result_path = self.state_root / "content" / "continuous" / "cycles" / cycle / "result.json"
                try:
                    failure = json.loads(result_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(failure, dict) or failure.get("status") != "ERROR":
                    continue
                outcome = self.ingest_failure(
                    {**failure, "failure_site": failure_site},
                    now=now,
                )
                problem_id = str(outcome.get("problem_id") or "")
                if problem_id:
                    migrated_for_row.add(problem_id)
                    new_problem_ids.add(problem_id)
                if outcome.get("status") in {"RECORDED", "ALREADY_RECORDED"}:
                    migrated_cycles += 1
            if migrated_for_row:
                payload["status"] = "LEGACY_AGGREGATE"
                payload["repair_proposal"] = None
                payload["superseded_by"] = sorted(migrated_for_row)
                payload["superseded_at"] = now.astimezone(timezone.utc).isoformat()
                _atomic_json(path, payload)
                legacy_rows += 1
        return {
            "legacy_rows": legacy_rows,
            "migrated_cycles": migrated_cycles,
            "new_problem_count": len(new_problem_ids),
        }

    def reconcile_legacy_duplicates(self, *, now: datetime) -> dict[str, int]:
        folder = self.root / "problems"
        rows: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "") in {
                "VERIFIED", "CLOSED", "REJECTED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
            }:
                continue
            rows.append((path, payload))

        groups: dict[tuple[str, str, tuple[str, ...]], list[tuple[Path, dict[str, Any]]]] = {}
        for path, payload in rows:
            key = (
                str(payload.get("error_type") or ""),
                str(payload.get("symptom") or ""),
                tuple(sorted(str(x) for x in payload.get("cycles") or () if str(x))),
            )
            groups.setdefault(key, []).append((path, payload))

        archived = 0
        groups_reconciled = 0
        for matches in groups.values():
            if len(matches) < 2:
                continue
            explicit = [
                item for item in matches
                if "failure_site" in item[1]
            ]
            legacy = [
                item for item in matches
                if "failure_site" not in item[1]
            ]
            if not explicit or not legacy:
                continue
            canonical_path, canonical = sorted(
                explicit,
                key=lambda item: (
                    str(item[1].get("failure_site") or "") != "unknown",
                    str(item[1].get("last_seen_at") or ""),
                    str(item[1].get("problem_id") or ""),
                ),
                reverse=True,
            )[0]
            canonical_id = str(canonical.get("problem_id") or canonical_path.stem)
            changed = False
            for path, payload in legacy:
                payload["status"] = "LEGACY_DUPLICATE"
                payload["repair_proposal"] = None
                payload["superseded_by"] = [canonical_id]
                payload["superseded_at"] = now.astimezone(timezone.utc).isoformat()
                _atomic_json(path, payload)
                archived += 1
                changed = True
            if changed:
                groups_reconciled += 1
        return {
            "groups_reconciled": groups_reconciled,
            "archived_duplicates": archived,
        }

    def refresh_known_proposals(self) -> dict[str, int]:
        folder = self.root / "problems"
        enriched = 0
        scanned = 0
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            scanned += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("repair_proposal"):
                continue
            if str(payload.get("status") or "") in {
                "VERIFIED", "CLOSED", "REJECTED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
            }:
                continue
            cycles = [str(x) for x in payload.get("cycles") or () if str(x)]
            proposal = self._proposal_for(
                error_type=str(payload.get("error_type") or ""),
                message=str(payload.get("symptom") or ""),
                cycle=cycles[-1] if cycles else "unknown",
                failure_site=str(payload.get("failure_site") or "unknown"),
            )
            if proposal is None:
                continue
            payload["repair_proposal"] = proposal
            payload["status"] = "REPAIR_PROPOSED"
            _atomic_json(path, payload)
            enriched += 1
            latest = self.root / "latest.json"
            if str(payload.get("last_seen_at") or "") >= str(_read_last_seen(latest)):
                _atomic_json(latest, payload)
        return {"scanned": scanned, "enriched": enriched}

    def review(self) -> dict[str, Any]:
        self.refresh_known_proposals()
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
            if str(row.get("status") or "") not in {
                "VERIFIED", "CLOSED", "REJECTED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
            }
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
                    "failure_site": row.get("failure_site"),
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
