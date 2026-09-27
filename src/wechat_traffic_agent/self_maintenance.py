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

from .repair_request import RepairEvidenceRef, RepairRequest, submit_repair_request


_KNOWN_COGNITION_ERROR = "BELIEF_CHANGED requires a persisted worldview change"
_KNOWN_MODEL_JSON_ERROR = "backend did not return a JSON object"
_KNOWN_RECOVERY_INPUT_CHANGE = "model phase input changed during recovery"

_TASK_NON_SUCCESS_STATUSES = {
    "", "ERROR", "DEGRADED", "FAILED", "BUSY", "RESOURCE_DEFERRED", "DEFERRED",
    "SHUTDOWN_CHECKPOINT", "CANCELLED", "SKIPPED", "INTERRUPTED",
}


def _task_result_is_success(status: str, failed_parts: list[str]) -> bool:
    return not failed_parts and str(status or "").upper() not in _TASK_NON_SUCCESS_STATUSES


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

    def ingest_task_outcome(self, task: str, result: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """Record both failure and same-task recovery evidence from every wakeful task."""
        if not isinstance(result, dict):
            return {"status": "IGNORED_NON_ERROR"}
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self-maintenance time requires timezone awareness")
        task = str(task or "UNKNOWN").upper()
        status = str(result.get("status") or "").upper()
        failed_parts = [
            key for key, value in result.items()
            if isinstance(value, dict)
            and str(value.get("status") or "").upper() in {"ERROR", "DEGRADED"}
        ]
        embedded = (
            dict(result["self_maintenance"])
            if task == "CONTENT_COGNITION" and isinstance(result.get("self_maintenance"), dict)
            else None
        )
        cycle = str(result.get("cycle") or f"{task.lower()}-{int(now.timestamp())}")
        if _task_result_is_success(status, failed_parts):
            recovery = self._record_task_success(
                task=task,
                status=status,
                cycle=cycle,
                now=now,
            )
            if embedded is not None:
                return {**embedded, "task_recovery": recovery}
            return recovery
        if embedded is not None:
            return embedded
        if status not in {"ERROR", "DEGRADED"} and not failed_parts:
            return {"status": "IGNORED_NON_ERROR"}
        return self.ingest_failure({
            "status": "ERROR", "task": task, "cycle": cycle,
            "error_type": str(result.get("error_type") or status or "SUBTASK_FAILURE")[:120],
            "message": "failed components: " + ",".join(sorted(failed_parts)) if failed_parts else status,
            "failure_site": task.lower() + ":" + ",".join(sorted(failed_parts)) if failed_parts else task.lower(),
        }, now=now)

    def _record_task_success(
        self,
        *,
        task: str,
        status: str,
        cycle: str,
        now: datetime,
    ) -> dict[str, Any]:
        observed_at = now.astimezone(timezone.utc).isoformat()
        evidence_ref = f"wakeful-task:{task}:{cycle}:result"
        success = {
            "schema_version": "self-maintenance-task-success.v1",
            "task": task,
            "status": status,
            "cycle": cycle,
            "observed_at": observed_at,
            "evidence_ref": evidence_ref,
        }
        safe = re.sub(r"[^a-z0-9_.-]+", "-", task.lower()).strip("-") or "unknown"
        _atomic_json(self.root / "task_successes" / f"{safe}.json", success)
        recovered = self._reconcile_task_recovery(
            task=task,
            observed_at=now.astimezone(timezone.utc),
            evidence_ref=evidence_ref,
            result_status=status,
        )
        return {
            "status": "RECOVERY_RECORDED",
            "task": task,
            "cycle": cycle,
            "evidence_ref": evidence_ref,
            **recovered,
        }

    def _reconcile_task_recovery(
        self,
        *,
        task: str,
        observed_at: datetime,
        evidence_ref: str,
        result_status: str,
    ) -> dict[str, Any]:
        folder = self.root / "problems"
        recovered: list[dict[str, Any]] = []
        terminal = {
            "VERIFIED", "CLOSED", "REJECTED", "RECOVERED_VERIFIED",
            "LEGACY_AGGREGATE", "LEGACY_DUPLICATE",
        }
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "") in terminal:
                continue
            if str(payload.get("task") or "").upper() != task:
                continue
            try:
                last_seen = datetime.fromisoformat(str(payload.get("last_seen_at") or ""))
            except (TypeError, ValueError):
                continue
            if last_seen.tzinfo is None or last_seen.utcoffset() is None:
                continue
            if last_seen.astimezone(timezone.utc) >= observed_at:
                continue
            payload["status"] = "RECOVERED_VERIFIED"
            payload["recovered_at"] = observed_at.isoformat()
            payload["recovery_result_status"] = str(result_status)
            payload["recovery_evidence_refs"] = list(dict.fromkeys([
                *(str(x) for x in payload.get("recovery_evidence_refs") or () if str(x)),
                evidence_ref,
            ]))[-16:]
            _atomic_json(path, payload)
            recovered.append(payload)

        if recovered:
            latest_path = self.root / "latest.json"
            latest_id = ""
            try:
                latest = json.loads(latest_path.read_text(encoding="utf-8"))
                if isinstance(latest, dict):
                    latest_id = str(latest.get("problem_id") or "")
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                pass
            replacement = next(
                (row for row in recovered if str(row.get("problem_id") or "") == latest_id),
                None,
            )
            if replacement is not None:
                _atomic_json(latest_path, replacement)
        return {
            "recovered_count": len(recovered),
            "recovered_problem_ids": [
                str(row.get("problem_id") or "") for row in recovered
                if str(row.get("problem_id") or "")
            ],
        }

    def ingest_failure(self, failure: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        if str(failure.get("status") or "") != "ERROR":
            return {"status": "IGNORED_NON_ERROR"}
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self-maintenance time requires timezone awareness")

        error_type = str(failure.get("error_type") or "UNKNOWN")
        message = " ".join(str(failure.get("message") or "").split())[:1000]
        cycle = _cycle_ref(failure.get("cycle"))
        task = str(failure.get("task") or "CONTENT_COGNITION").upper()
        failure_site = str(failure.get("failure_site") or _stable_failure_site(self.state_root, cycle))
        signature = sha256(
            f"{task}\0{error_type}\0{message}\0{failure_site}".encode("utf-8")
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
            f"wakeful-task:{task}:{cycle}:result",
            "error-signature:sha256:" + signature,
        ]
        case = self._diagnose(
            problem_id=problem_id,
            error_type=error_type,
            message=message,
            cycle=cycle,
            failure_site=failure_site,
            evidence_refs=evidence_refs,
            task=task,
            now=now,
        )
        case_path = self.memory.persist_case(case)
        proposal = self._proposal_for(
            error_type=error_type,
            message=message,
            cycle=cycle,
            failure_site=failure_site,
        ) if task == "CONTENT_COGNITION" else None
        status = "REPAIR_PROPOSED" if proposal is not None else "DIAGNOSING"
        cycles = [*seen_cycles, cycle][-32:]
        payload = {
            "schema_version": "self-problem.v1",
            "problem_id": problem_id,
            "status": status,
            "symptom": message or error_type,
            "error_type": error_type,
            "failure_site": failure_site,
            "failed_goal": f"complete {task} task",
            "affected_capability": f"wakeful.{task.lower()}",
            "task": task,
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
        repair_request = self.request_code_repair(problem_id) if proposal is not None else {
            "status": "NOT_ELIGIBLE",
        }
        return {
            "status": "RECORDED",
            "problem_id": problem_id,
            "problem_status": status,
            "repair_proposal": proposal,
            "repair_request": repair_request,
            "evolution": evolution,
        }

    def request_code_repair(self, problem_id: str) -> dict[str, Any]:
        """Submit evidence to Pi Remote; Ferro receives no source-control authority."""
        folder = self.root / "problems"
        path = next(
            (item for item in folder.glob("*.json") if self._problem_id(item) == problem_id),
            None,
        )
        if path is None:
            return {"status": "PROBLEM_NOT_FOUND"}
        try:
            problem = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "PROBLEM_UNREADABLE"}
        if not isinstance(problem, dict):
            return {"status": "PROBLEM_UNREADABLE"}
        prior = problem.get("repair_request")
        if isinstance(prior, dict) and (prior.get("request_id") or prior.get("requestId") or prior.get("job_id") or prior.get("jobId")):
            return dict(prior)
        proposal = problem.get("repair_proposal")
        if not isinstance(proposal, dict) or str(proposal.get("risk") or "").upper() != "LOW":
            return {"status": "NOT_ELIGIBLE"}
        refs = [
            RepairEvidenceRef(type="FerroEvidence", id=str(ref))
            for ref in (problem.get("evidence_refs") or [])[:32]
            if str(ref).strip()
        ]
        failed_tests = []
        trial = problem.get("repair_trial")
        if isinstance(trial, dict):
            failed_tests = [str(item) for item in trial.get("failed_tests") or [] if str(item).strip()][:32]
        impact = str(problem.get("observed_world_impact") or "").strip()
        if not impact:
            impact = "Recorded failure prevented the affected cognition cycle from completing; downstream impact is not established."
        request = RepairRequest(
            idempotency_key=f"{problem_id}:{proposal.get('proposal_id') or 'proposal'}",
            source_problem_id=problem_id,
            symptom=str(problem.get("symptom") or problem.get("error_type") or "unknown failure"),
            failure_site=str(problem.get("failure_site") or "unknown"),
            evidence=refs,
            failed_tests=failed_tests,
            severity=str(problem.get("severity") or "LOW").upper(),
            risk="LOW",
            observed_world_impact=impact,
            target_files=[str(item) for item in proposal.get("target_files") or []][:2],
        )
        receipt = submit_repair_request(self.capability_runtime, request)
        audit = {
            **receipt,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": request.idempotency_key,
            "request_sha256": sha256(
                json.dumps(request.to_pi_remote_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "evidence_refs": [item.model_dump(exclude_none=True) for item in refs],
        }
        problem["repair_request"] = audit
        if audit.get("status") in {"REQUESTED", "RECEIVED", "ACCEPTED"}:
            problem["status"] = "REPAIR_REQUESTED"
            proposal["status"] = "REPAIR_REQUESTED"
            problem["repair_proposal"] = proposal
        _atomic_json(path, problem)
        latest = self.root / "latest.json"
        try:
            latest_payload = json.loads(latest.read_text(encoding="utf-8"))
            if isinstance(latest_payload, dict) and latest_payload.get("problem_id") == problem_id:
                latest_payload["repair_request"] = audit
                if audit.get("status") in {"REQUESTED", "RECEIVED", "ACCEPTED"}:
                    latest_payload["status"] = "REPAIR_REQUESTED"
                    proposal = latest_payload.get("repair_proposal")
                    if isinstance(proposal, dict):
                        proposal["status"] = "REPAIR_REQUESTED"
                _atomic_json(latest, latest_payload)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return audit

    @staticmethod
    def _problem_id(path: Path) -> str | None:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return str(row.get("problem_id")) if isinstance(row, dict) else None

    def _diagnose(
        self,
        *,
        problem_id: str,
        error_type: str,
        message: str,
        cycle: str,
        failure_site: str,
        evidence_refs: list[str],
        task: str = "CONTENT_COGNITION",
        now: datetime,
    ) -> DiagnosticCase:
        case = DiagnosticCase.start(
            symptom=f"{error_type}: {message}",
            scope_ladder=("content-cycle", "content-cognition-contract", "content-agent") if task == "CONTENT_COGNITION" else ("wakeful-task", "wakeful-runtime", "content-agent"),
            tags=("production-failure", task.lower(), error_type.lower()),
            now=now,
        )
        observation = case.observe(
            statement=f"Cycle {cycle} ended with {error_type}: {message}",
            scope="content-cycle" if task == "CONTENT_COGNITION" else "wakeful-task",
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
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_service.py:compose" in failure_site:
            contract_observation = case.observe(
                statement="Content composition requested a schema-shaped artifact through a text-only cognition response.",
                scope="content-cognition-contract",
                kind=ObservationKind.FAILURE,
                evidence_refs=evidence_refs,
                now=now,
            )
            hypothesis = case.hypothesize(
                statement="The content composer relies on prompt compliance for JSON even though the advisory backend supports structured output.",
                scope="content-cognition-contract",
                predicts="The failed request is dispatched as text and compose then fails while extracting a JSON object.",
                falsifier="The failed composition request was already constrained by the generated-content JSON schema.",
            )
            case.support(hypothesis.hypothesis_id, [contract_observation.observation_id])
            case.conclude(
                hypothesis.hypothesis_id,
                summary="Content composition should bind its existing Pydantic schema into the cognition request instead of parsing unconstrained text.",
                now=now,
            )
            case.record_remediation(
                "Request structured output using the generated-content schema for both initial composition and its one bounded repair attempt."
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
        elif _KNOWN_MODEL_JSON_ERROR in message and "content_service.py:compose" in failure_site:
            target_file = "src/wechat_traffic_agent/content_service.py"
            repair_kind = "USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION"
            verification = [
                "content composition request carries the generated-content JSON schema",
                "invented source URLs remain rejected after structured generation",
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
                subject=f"{problem.get('task', 'CONTENT_COGNITION').lower()}-self-maintenance",
                summary=str(problem["symptom"]),
                severity=0.85,
                evidence_refs=list(problem.get("evidence_refs") or ()),
                recurrence_key=f"{problem.get('task', 'CONTENT_COGNITION')}:{problem['error_type']}:{problem['problem_id']}",
                need_hint="Make content cognition failures diagnosable and recoverable without human intervention.",
                target_capability=f"self-maintenance.{str(problem.get('task') or 'CONTENT_COGNITION').lower().replace('_', '-')}",
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
            if str(payload.get("status") or "") in {"VERIFIED", "CLOSED", "REJECTED", "RECOVERED_VERIFIED", "LEGACY_AGGREGATE"}:
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
                "VERIFIED", "CLOSED", "REJECTED", "RECOVERED_VERIFIED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
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
        repair_requested = 0
        repair_candidates: list[tuple[str, str]] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            scanned += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "") in {
                "VERIFIED", "CLOSED", "REJECTED", "RECOVERED_VERIFIED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
            }:
                continue
            proposal = payload.get("repair_proposal")
            if not isinstance(proposal, dict):
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
            if str(proposal.get("risk") or "").upper() != "LOW":
                continue
            prior = payload.get("repair_request")
            if isinstance(prior, dict):
                if any(
                    prior.get(key)
                    for key in ("request_id", "requestId", "job_id", "jobId")
                ):
                    continue
                if str(prior.get("status") or "") not in {
                    "", "CAPABILITY_UNAVAILABLE", "REQUEST_FAILED"
                }:
                    continue
            problem_id = str(payload.get("problem_id") or "")
            if problem_id:
                repair_candidates.append((
                    str(payload.get("last_seen_at") or ""),
                    problem_id,
                ))

        # Keep startup escalation bounded. Prefer the most recently observed
        # known failures, and rely on the stable idempotency key for retries.
        for _seen_at, problem_id in sorted(
            repair_candidates, reverse=True
        )[:4]:
            receipt = self.request_code_repair(problem_id)
            if str(receipt.get("status") or "") in {
                "REQUESTED", "RECEIVED", "ACCEPTED"
            }:
                repair_requested += 1
        return {
            "scanned": scanned,
            "enriched": enriched,
            "repair_candidate_count": len(repair_candidates),
            "repair_requested": repair_requested,
        }

    def review(self) -> dict[str, Any]:
        refresh = self.refresh_known_proposals()
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
                "VERIFIED", "CLOSED", "REJECTED", "RECOVERED_VERIFIED", "LEGACY_AGGREGATE", "LEGACY_DUPLICATE"
            }
        ]
        open_rows.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
        return {
            "status": "OK",
            "open_count": len(open_rows),
            "repair_proposed_count": sum(1 for row in open_rows if row.get("repair_proposal")),
            "repair_requested_count": int(refresh.get("repair_requested") or 0),
            "proposal_refresh": refresh,
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
