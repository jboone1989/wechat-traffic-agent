"""Task-granular execution for the Content Agent Wakeful Core adopter."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any


class _ShutdownRequested(BaseException):
    """Internal cooperative-cancellation signal; bypass broad Exception handlers."""


class WakefulContentTaskRunner:
    MAX_OWNER_PREDICTION_DEFER = timedelta(hours=1)

    def __init__(self, **components: Any) -> None:
        self.__dict__.update(components)
        # wakeful is an optional scheduling/signal hook for unit-sized task
        # runners. Keep the attribute explicit so absence does not turn an
        # otherwise valid interoception result into AttributeError.
        self.wakeful = components.get("wakeful")
        self.stop_requested = components.get("stop_requested") or (lambda: False)
        state_root = components.get("state_root")
        app = components.get("app")
        capability_runtime = (
            components.get("capability_runtime")
            or components.get("application_runtime")
            or components.get("app_runtime")
            or getattr(app, "runtime", None)
        )
        evolution_runtime = (
            components.get("evolution_runtime")
            or getattr(app, "domain_runtime", None)
        )
        if state_root is not None and getattr(self, "self_maintenance", None) is None:
            from .self_maintenance import SelfMaintenanceService

            self.self_maintenance = SelfMaintenanceService(
                state_root=Path(state_root),
                evolution_runtime=evolution_runtime,
                capability_runtime=capability_runtime,
            )
        if state_root is not None and getattr(self, "interoceptive_faults", None) is None:
            from .interoceptive_fault import InteroceptiveFaultSystem

            self.interoceptive_faults = InteroceptiveFaultSystem(Path(state_root))
        if state_root is not None and getattr(self, "capability_contracts", None) is None:
            from .capability_contracts import CapabilityContractMonitor

            self.capability_contracts = CapabilityContractMonitor(Path(state_root))
        if state_root is not None and getattr(self, "owner_agent_episode", None) is None:
            from .owner_agent_episode import OwnerAgentEpisode

            self.owner_agent_episode = OwnerAgentEpisode(Path(state_root))
        if state_root is not None and getattr(self, "goal_scheduler_evolution", None) is None:
            from .goal_scheduler_evolution import GoalSchedulerEvolution

            self.goal_scheduler_evolution = GoalSchedulerEvolution(Path(state_root))
        if (
            state_root is not None
            and components.get("repair_planner_runtime") is not None
            and getattr(self, "self_repair", None) is None
        ):
            from .self_repair_executor import SelfRepairExecutor

            self.self_repair = SelfRepairExecutor(
                state_root=Path(state_root),
                workspace_root=components.get("workspace_root"),
                deployed_root=components.get("deployed_root"),
                planner_runtime=components.get("repair_planner_runtime"),
                capability_runtime=capability_runtime,
            )

    def _stopping(self) -> bool:
        try:
            return bool(self.stop_requested())
        except Exception:
            return False

    def _raise_if_stopping(self) -> None:
        if self._stopping():
            raise _ShutdownRequested()

    async def _await_with_shutdown(self, awaitable: Any) -> Any:
        task = asyncio.create_task(awaitable)
        try:
            while not task.done():
                if self._stopping():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise _ShutdownRequested()
                await asyncio.wait({task}, timeout=0.1)
            return task.result()
        finally:
            if not task.done():
                task.cancel()

    def _run_async(self, awaitable: Any) -> Any:
        if self._stopping():
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise _ShutdownRequested()
        return asyncio.run(self._await_with_shutdown(awaitable))

    def _owner_behavioral_context(self) -> dict[str, Any]:
        wakeful = getattr(self, "wakeful", None)
        projection = getattr(wakeful, "behavioral_projection", None)
        if not callable(projection):
            raise RuntimeError(
                "owner cognition requires live wakeful behavioral projection"
            )
        from .behavioral_state import build_behavioral_state, build_phenotype_prior

        behavioral_state = build_behavioral_state(projection())
        phenotype_prior = build_phenotype_prior(behavioral_state)
        return {
            "phenotype_prior": phenotype_prior,
            "behavioral_state": behavioral_state,
        }

    def _windfall_pending_path(self) -> Path | None:
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return None
        return Path(state_root) / "application" / "llm_resource_pool" / "windfall_feast_pending.json"

    def _record_windfall_pending(self, windfall: dict[str, Any], now: datetime) -> None:
        path = self._windfall_pending_path()
        if path is None:
            return
        model = str(windfall.get("model") or "").strip()
        if not model:
            return
        payload = {
            "schema_version": "llm-windfall-feast.v1",
            "model": model,
            "provider_label": str(windfall.get("provider_label") or ""),
            "observed_at": str(windfall.get("observed_at") or now.isoformat()),
            "capability_tier": str(windfall.get("capability_tier") or ""),
            "capability_score": float(windfall.get("capability_score") or 0.0),
            "trial_ids": [str(x) for x in windfall.get("trial_ids") or () if str(x)],
            "status": "PENDING",
        }
        payload["event_id"] = "windfall-feast:" + sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _load_windfall_pending(self) -> dict[str, Any] | None:
        path = self._windfall_pending_path()
        if path is None or not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) and raw.get("status") == "PENDING" else None

    def _consume_windfall_pending(self, pending: dict[str, Any], hypothesis_id: str, now: datetime) -> None:
        path = self._windfall_pending_path()
        if path is None or not path.exists():
            return
        receipt_dir = path.parent / "windfall_feast_receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            **pending,
            "status": "CONSUMED",
            "hypothesis_id": str(hypothesis_id),
            "consumed_at": now.isoformat(),
        }
        receipt_path = receipt_dir / (str(pending.get("event_id") or "windfall") + ".json")
        tmp = receipt_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(receipt_path)
        path.unlink(missing_ok=True)

    @staticmethod
    def _compact_interoception(projection: dict[str, Any]) -> dict[str, Any]:
        return {
            key: projection.get(key)
            for key in (
                "schema_version",
                "status",
                "updated_at",
                "distress",
                "confidence_in_self",
                "active_episode_ids",
                "suspected_faults",
                "symptoms",
                "latest_observation_id",
            )
            if projection.get(key) is not None
        }

    @staticmethod
    def _contract_failure_site(task: str, subsystem: str) -> str:
        task = str(task or "UNKNOWN").upper()
        subsystem = str(subsystem or "capability")
        if subsystem == "context":
            return "cognition_context.py:capability_contract"
        if subsystem == "neural":
            return "behavioral_state.py:capability_contract"
        if subsystem == "tool_use":
            return "owner_agent_episode.py:tool_effect_contract"
        if subsystem == "interoception":
            return "capability_contracts.py:contract_sensor"
        if subsystem == "goal_execution":
            if task == "RESOURCE_REVIEW":
                # Resource-goal effect spans wakeful orchestration plus the
                # survival resource actuator. Give diagnosis both surfaces.
                return "resource_review:survival_resources"
            if task == "CONTENT_COGNITION":
                return "content_cognition:content_goal_loop"
            if task == "CHECK_X":
                return "check_x:x_growth"
            if task == "CHECK_OUTCOMES":
                return "check_outcomes:outcome"
            return task.lower() + ":goal_effect_contract"
        return "capability_contracts.py:" + subsystem

    def _finalize_task_result(
        self,
        task: str,
        result: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        """Make task pathology visible even when defensive code preserved continuity."""
        task = str(task or "UNKNOWN").upper()
        rows = dict(result) if isinstance(result, dict) else {
            "task": task,
            "status": "ERROR",
            "error_type": "InvalidTaskResult",
            "message": f"wakeful task returned {type(result).__name__}",
        }
        rows.setdefault("task", task)
        if str(rows.get("status") or "").upper() in {
            "WAITING_FOR_CHATGPT",
            "SHUTDOWN_CHECKPOINT",
            "CANCELLED",
            "STALE_OWNER_COGNITION_IGNORED",
        }:
            # Administrative/non-executed states are neither illness nor
            # recovery evidence. Preserve their external result contract.
            return rows
        contract_monitor = getattr(self, "capability_contracts", None)
        if contract_monitor is not None:
            try:
                task_contract = contract_monitor.observe_task(
                    task, rows, now=now
                )
                rows["capability_contracts"] = task_contract
                if task in {"NEURAL_INTEROCEPTION", "RESOURCE_REVIEW", "SELF_REPAIR"}:
                    durable_contract = contract_monitor.observe_durable_state(now=now)
                    rows["durable_capability_contracts"] = durable_contract
                    if list(durable_contract.get("violations") or []):
                        rows["capability_contracts"] = durable_contract
            except Exception as exc:  # contract sensing failure is itself pathology
                rows["capability_contracts"] = {
                    "schema_version": "ferro-capability-contracts.v1",
                    "surface": "task_result",
                    "task": task,
                    "status": "SENSOR_FAILURE",
                    "violations": [{
                        "kind": "capability_contract_sensor_failure",
                        "subsystem": "interoception",
                        "severity": 0.72,
                        "contract": "capability_contracts_must_be_observable",
                        "expected": "contract evaluator returns a deterministic report",
                        "observed": type(exc).__name__ + ": " + str(exc)[:400],
                        "evidence_refs": [],
                    }],
                }

        sensor = getattr(self, "interoceptive_faults", None)
        if sensor is None:
            return rows

        try:
            sensed = sensor.observe(task, rows, now=now)
        except Exception as exc:  # interoception failure is itself a degraded capability
            rows["interoception"] = {
                "status": "SENSOR_FAILURE",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            }
            if str(rows.get("status") or "").upper() not in {
                "ERROR", "FAILED", "DEGRADED",
            }:
                rows["status"] = "DEGRADED"
            maintenance = getattr(self, "self_maintenance", None)
            if maintenance is not None:
                try:
                    rows["maintenance_observation"] = maintenance.ingest_failure(
                        {
                            "status": "ERROR",
                            "task": "NEURAL_INTEROCEPTION",
                            "cycle": f"interoception-sensor-{int(now.timestamp())}",
                            "error_type": type(exc).__name__,
                            "message": "interoception sensor failed: " + str(exc)[:400],
                            "failure_site": "interoception:sensor",
                        },
                        now=now,
                    )
                except Exception as maintenance_exc:  # noqa: BLE001
                    rows["maintenance_observation"] = self._error(maintenance_exc)
            return rows

        projection = dict(sensed.get("projection") or {})
        observation = dict(sensed.get("observation") or {})
        rows["interoception"] = {
            **self._compact_interoception(projection),
            "observation_id": observation.get("observation_id"),
            "episode_id": observation.get("episode_id"),
            "severity": observation.get("severity"),
            "primary_symptom": observation.get("primary_symptom"),
            "diagnosis_active": bool(sensed.get("diagnosis_active")),
        }

        maintenance = getattr(self, "self_maintenance", None)
        maintenance_result: dict[str, Any] | None = None
        if maintenance is not None:
            try:
                maintenance_result = maintenance.ingest_task_outcome(
                    task, rows, now=now
                )
                rows["maintenance_observation"] = maintenance_result
            except Exception as exc:  # noqa: BLE001
                rows["maintenance_observation"] = self._error(exc)

        diagnostic_failure = sensed.get("diagnostic_failure")
        contract_report = (
            rows.get("capability_contracts")
            if isinstance(rows.get("capability_contracts"), dict)
            else {}
        )
        contract_violations = [
            row for row in list(contract_report.get("violations") or [])
            if isinstance(row, dict)
        ]
        if isinstance(diagnostic_failure, dict) and contract_violations:
            top_contract = contract_violations[0]
            contract_subsystem = str(top_contract.get("subsystem") or "capability")
            diagnostic_failure = {
                **diagnostic_failure,
                "cycle": (
                    str(contract_report.get("observation_id") or "")
                    if (
                        str(contract_report.get("surface") or "") == "durable_state"
                        and str(contract_report.get("observation_id") or "")
                    )
                    else diagnostic_failure.get("cycle")
                ),
                "error_type": "CAPABILITY_CONTRACT_VIOLATION",
                "failure_site": (
                    str(top_contract.get("failure_site") or "")
                    or self._contract_failure_site(
                        task,
                        contract_subsystem,
                    )
                ),
                "message": (
                    "capability contract violation: "
                    + str(top_contract.get("kind") or "unknown")
                    + "; expected="
                    + str(top_contract.get("expected") or "")[:300]
                    + "; observed="
                    + str(top_contract.get("observed") or "")[:400]
                ),
            }
        if (
            isinstance(diagnostic_failure, dict)
            and maintenance is not None
            and str((maintenance_result or {}).get("status") or "") not in {
                "RECORDED", "ALREADY_RECORDED",
            }
        ):
            try:
                rows["maintenance_observation"] = maintenance.ingest_failure(
                    diagnostic_failure, now=now
                )
            except Exception as exc:  # noqa: BLE001
                rows["maintenance_observation"] = self._error(exc)

        maintenance_observation = (
            rows.get("maintenance_observation")
            if isinstance(rows.get("maintenance_observation"), dict)
            else {}
        )
        if (
            isinstance(diagnostic_failure, dict)
            and str(maintenance_observation.get("status") or "") == "RECORDED"
        ):
            schedule_repair = getattr(self.wakeful, "schedule_self_maintenance", None)
            if callable(schedule_repair):
                try:
                    schedule_repair(now, seconds=0)
                    rows["interoception"]["repair_wake"] = {
                        "status": "SCHEDULED",
                        "task": "SELF_REPAIR",
                        "problem_id": maintenance_observation.get("problem_id"),
                    }
                except Exception as exc:  # noqa: BLE001
                    rows["interoception"]["repair_wake"] = self._error(exc)

        if observation.get("abnormal"):
            emit = getattr(self.wakeful, "emit_interoceptive_fault", None)
            if callable(emit):
                try:
                    rows["interoception"]["neural_signal"] = emit(
                        {
                            "episode_id": observation.get("episode_id"),
                            "subsystem": observation.get("subsystem"),
                            "primary_symptom": observation.get("primary_symptom"),
                            "severity": observation.get("severity"),
                            "projection": projection,
                        },
                        observed_at=now,
                    )
                except Exception as exc:  # noqa: BLE001
                    rows["interoception"]["neural_signal"] = self._error(exc)

        if bool(sensed.get("should_diagnose")):
            schedule = getattr(self.wakeful, "schedule_reflection", None)
            if callable(schedule):
                try:
                    episode_id = str(observation.get("episode_id") or "")
                    rows["interoception"]["diagnostic_wake"] = schedule(
                        now,
                        seconds=15,
                        reason=(
                            "diagnose felt runtime pathology: "
                            + str(observation.get("primary_symptom") or "unknown")
                            + " in "
                            + str(observation.get("subsystem") or "unknown")
                        ),
                        relevant_refs=tuple(
                            ref for ref in (episode_id, "interoception:active") if ref
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    rows["interoception"]["diagnostic_wake"] = self._error(exc)
        return rows

    def observe_settled_capability_outcome(
        self,
        task: str,
        outcome: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        """Sense post-task world settlement that did not exist at task return time."""
        task = str(task or "UNKNOWN").upper()
        if not isinstance(outcome, dict):
            return {"status": "IGNORED_NON_MAPPING_OUTCOME", "task": task}
        monitor = getattr(self, "capability_contracts", None)
        sensor = getattr(self, "interoceptive_faults", None)
        if monitor is None or sensor is None:
            return {"status": "SENSOR_UNAVAILABLE", "task": task}

        cycle = str(
            outcome.get("close_evidence_ref")
            or outcome.get("goal_id")
            or outcome.get("outcome_id")
            or f"settled-{task.lower()}-{int(now.timestamp())}"
        )
        synthetic = {
            "task": task,
            "status": "OK",
            "cycle": cycle,
            "autonomous_goal_outcome": dict(outcome),
        }
        try:
            contract_report = monitor.observe_task(task, synthetic, now=now)
        except Exception as exc:
            return {
                "status": "SENSOR_FAILURE",
                "task": task,
                "error_type": type(exc).__name__,
                "message": str(exc)[:400],
            }
        if not list(contract_report.get("violations") or []):
            return {
                "status": "PASS",
                "task": task,
                "capability_contracts": contract_report,
            }

        sensed = sensor.observe(
            task,
            {
                **synthetic,
                "capability_contracts": contract_report,
            },
            now=now,
        )
        projection = dict(sensed.get("projection") or {})
        observation = dict(sensed.get("observation") or {})
        receipt: dict[str, Any] = {
            "status": "VIOLATED",
            "task": task,
            "capability_contracts": contract_report,
            "interoception": {
                **self._compact_interoception(projection),
                "observation_id": observation.get("observation_id"),
                "episode_id": observation.get("episode_id"),
                "severity": observation.get("severity"),
                "primary_symptom": observation.get("primary_symptom"),
            },
        }

        if observation.get("abnormal"):
            emit = getattr(self.wakeful, "emit_interoceptive_fault", None)
            if callable(emit):
                try:
                    receipt["interoception"]["neural_signal"] = emit(
                        {
                            "episode_id": observation.get("episode_id"),
                            "subsystem": observation.get("subsystem"),
                            "primary_symptom": observation.get("primary_symptom"),
                            "severity": observation.get("severity"),
                            "projection": projection,
                        },
                        observed_at=now,
                    )
                except Exception as exc:  # noqa: BLE001
                    receipt["interoception"]["neural_signal"] = self._error(exc)

        if bool(sensed.get("should_diagnose")):
            violations = [
                row for row in list(contract_report.get("violations") or [])
                if isinstance(row, dict)
            ]
            top = violations[0] if violations else {}
            subsystem = str(top.get("subsystem") or "goal_execution")
            maintenance = getattr(self, "self_maintenance", None)
            diagnostic_failure = sensed.get("diagnostic_failure")
            if maintenance is not None and isinstance(diagnostic_failure, dict):
                try:
                    receipt["maintenance_observation"] = maintenance.ingest_failure(
                        {
                            **diagnostic_failure,
                            "task": task,
                            "error_type": "CAPABILITY_CONTRACT_VIOLATION",
                            "failure_site": (
                                str(top.get("failure_site") or "")
                                or self._contract_failure_site(
                                    task,
                                    subsystem,
                                )
                            ),
                            "message": (
                                "capability contract violation: "
                                + str(top.get("kind") or "unknown")
                                + "; expected="
                                + str(top.get("expected") or "")[:300]
                                + "; observed="
                                + str(top.get("observed") or "")[:400]
                            ),
                        },
                        now=now,
                    )
                except Exception as exc:  # noqa: BLE001
                    receipt["maintenance_observation"] = self._error(exc)
            schedule = getattr(self.wakeful, "schedule_reflection", None)
            if callable(schedule):
                try:
                    episode_id = str(observation.get("episode_id") or "")
                    receipt["diagnostic_wake"] = schedule(
                        now,
                        seconds=15,
                        reason=(
                            "diagnose settled capability pathology: "
                            + str(observation.get("primary_symptom") or "unknown")
                            + " in "
                            + str(observation.get("subsystem") or subsystem)
                        ),
                        relevant_refs=tuple(
                            ref for ref in (episode_id, "interoception:active") if ref
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    receipt["diagnostic_wake"] = self._error(exc)
        return receipt

    def run(
        self,
        task: str,
        *,
        now: datetime,
        focus_topic: str | None = None,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        try:
            result = self._run_unobserved(
                task,
                now=now,
                focus_topic=focus_topic,
                relevant_refs=relevant_refs,
            )
        except Exception as exc:
            self._finalize_task_result(
                task,
                self._error(exc, uncaught=True),
                now=now,
            )
            raise
        return self._finalize_task_result(task, result, now=now)

    def _run_unobserved(
        self,
        task: str,
        *,
        now: datetime,
        focus_topic: str | None = None,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        from .livingruntime_chatgpt import (
            LivingRuntimeChatGPTPendingError,
            LivingRuntimeChatGPTProviderError,
            resume_next_durable_cognition,
        )

        resume_request_ids = [
            str(ref).split(":", 1)[1]
            for ref in relevant_refs
            if str(ref).startswith("cognition-request:")
            and str(ref).split(":", 1)[1].strip()
        ]
        # A multi-call wakeful task can suspend more than once. Older persisted
        # refs are historical checkpoints; the newest request is the only
        # unresolved continuation pointer.
        resume_request_id = resume_request_ids[-1] if resume_request_ids else ""
        if task == "CONTENT_COGNITION" and resume_request_id:
            owner_goal = self._active_owner_goal_context(
                relevant_refs=relevant_refs,
                now=now,
            )
            episode = getattr(self, "owner_agent_episode", None)
            classify = getattr(episode, "classify_request", None)
            owner_goal_id = (
                str(owner_goal.get("goal_id") or "").strip()
                if isinstance(owner_goal, dict)
                else ""
            )
            if owner_goal_id and callable(classify):
                request_class = str(
                    classify(owner_goal_id, resume_request_id) or ""
                )
                if request_class == "STALE":
                    return {
                        "task": task,
                        "status": "STALE_OWNER_COGNITION_IGNORED",
                        "request_id": resume_request_id,
                        "owner_goal_id": owner_goal_id,
                        "resumable": False,
                    }
        # CONTENT_COGNITION contains multiple semantic LLM phases.  Never bind
        # its wake-level request id to "the next provider call": a duplicate
        # owner-episode wake can otherwise leak a completed OwnerAgent response
        # into the following mission/content phase.  OwnerAgentEpisode and
        # ReceiptRuntime each bind their own exact durable request below.
        resume_context = (
            resume_next_durable_cognition(resume_request_id)
            if resume_request_id and task != "CONTENT_COGNITION"
            else nullcontext()
        )
        try:
            with resume_context:
                self._raise_if_stopping()
                if task == "SELF_REPAIR":
                    return self._self_repair(now)
                regulator = getattr(self, "resource_regulator", None)
                if regulator is not None and not resume_request_id:
                    owner_authorized = (
                        task == "CONTENT_COGNITION"
                        and self._active_owner_goal_context() is not None
                    )
                    admission = regulator.claim(
                        task,
                        now,
                        owner_authorized=owner_authorized,
                    )
                    if not admission["allowed"]:
                        return {
                            "task": task,
                            "status": "RESOURCE_DEFERRED",
                            "resource_admission": admission,
                        }
                if task == "NEURAL_INTEROCEPTION":
                    return self._neural_interoception(now)
                if task == "NEURAL_PLAY":
                    return self._neural_play(now)
                if task == "NEURAL_DREAM":
                    return self._neural_dream(now)
                if task == "NEURAL_TRANSFER":
                    return self._neural_transfer(now)
                if task == "NEURAL_DEVELOPMENT":
                    return self._neural_development(now)
                if task == "NEURAL_DEVELOPMENT_TRANSFER":
                    return self._neural_development_transfer(now)
                if task == "NEURAL_EVOLUTION_CONSOLIDATION":
                    return self._neural_evolution_consolidation(now)
                if task == "RESOURCE_REVIEW":
                    return self._resource(now)
                if task == "REFLECT":
                    return self._reflect(
                        now,
                        relevant_refs=relevant_refs,
                    )
                if task == "CHECK_X":
                    return self._x(now)
                if task == "CHECK_OUTCOMES":
                    return self._outcomes(now)
                if task == "OBSERVE_WORLD":
                    return self._observe(now)
                if task == "VERIFY_PREDICTION":
                    return self._verify_prediction(now)
                if task == "CONTENT_COGNITION":
                    return self._content(
                        now,
                        focus_topic=focus_topic,
                        relevant_refs=relevant_refs,
                    )
                return {"status": "NOOP", "task": task}
        except LivingRuntimeChatGPTPendingError as exc:
            result: dict[str, Any] = {
                "task": task,
                "status": "WAITING_FOR_CHATGPT",
                "request_id": exc.request_id,
                "resumable": True,
            }
            resume_refs = tuple(relevant_refs)
            if task == "CONTENT_COGNITION":
                owner_goal = self._active_owner_goal_context(
                    relevant_refs=relevant_refs,
                    now=now,
                )
                owner_goal_id = (
                    str(owner_goal.get("goal_id") or "").strip()
                    if isinstance(owner_goal, dict)
                    else ""
                )
                if owner_goal_id:
                    resume_refs = tuple(
                        dict.fromkeys((*resume_refs, owner_goal_id))
                    )
            schedule_resume = getattr(self.wakeful, "schedule_chatgpt_resume", None)
            if callable(schedule_resume):
                try:
                    result["chatgpt_resume"] = schedule_resume(
                        now,
                        request_id=exc.request_id,
                        task=task,
                        relevant_refs=resume_refs,
                    )
                except Exception as schedule_exc:  # noqa: BLE001
                    result["chatgpt_resume"] = self._error(schedule_exc)
            return result
        except LivingRuntimeChatGPTProviderError as exc:
            # A bounded ChatGPT dispatch/response timeout is an expected
            # provider-availability state, not a process-fatal error. Keep any
            # durable owner episode checkpoint intact and re-enter cognition
            # later instead of letting the exception escape to systemd.
            result = {
                "task": task,
                "status": "CHATGPT_UNAVAILABLE",
                "retryable": True,
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            }
            if task == "CONTENT_COGNITION":
                episode = getattr(self, "owner_agent_episode", None)
                clear_pending = getattr(episode, "clear_pending_request", None)
                if resume_request_id and callable(clear_pending):
                    result["owner_episode_pending_cleared"] = bool(
                        clear_pending(
                            resume_request_id,
                            error_type=type(exc).__name__,
                            now=now,
                        )
                    )
            schedule_recovery = getattr(
                self.wakeful,
                "schedule_provider_recovery",
                None,
            )
            if callable(schedule_recovery):
                try:
                    retry_refs = tuple(
                        ref
                        for ref in relevant_refs
                        if not str(ref).startswith("cognition-request:")
                    )
                    result["provider_recovery"] = schedule_recovery(
                        now,
                        default_seconds=300,
                        task=task,
                        relevant_refs=retry_refs,
                    )
                except Exception as schedule_exc:  # noqa: BLE001
                    result["provider_recovery"] = self._error(schedule_exc)
            return result
        except _ShutdownRequested:
            return {
                "status": "SHUTDOWN_CHECKPOINT",
                "task": task,
                "reason": "SIGTERM_OR_SIGINT",
            }

    def _neural_interoception(self, now: datetime) -> dict[str, Any]:
        fault_projection = (
            self.interoceptive_faults.projection(now=now)
            if getattr(self, "interoceptive_faults", None) is not None
            else {}
        )
        projection = self.wakeful.refresh_neural_life(now)
        return {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK" if projection.get("status") != "DISABLED" else "DISABLED",
            "neural_life": projection,
            "fault_interoception": self._compact_interoception(fault_projection),
        }

    def _neural_play(self, now: datetime) -> dict[str, Any]:
        try:
            return self._neural_play_impl(now)
        except Exception as exc:  # noqa: BLE001
            brain_lab = getattr(self.wakeful, "brain_lab", None)
            substrate = getattr(brain_lab, "substrate", None)
            recorder = getattr(substrate, "record_error", None)
            if callable(recorder):
                try:
                    recorder(exc, now)
                except Exception:
                    pass
            return {
                "task": "NEURAL_PLAY",
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
                "failure_isolated": True,
                "service_continuity_preserved": True,
                "active_game_stage": "FAILED_ISOLATED",
            }

    def _neural_play_impl(self, now: datetime) -> dict[str, Any]:
        # Keep one normal embodied session for organ-handoff semantics, then use
        # the already-admitted resource window for a bounded headless arena.
        result = self.wakeful.run_brain_lab(now, steps=12)
        arena = None
        graduation = None
        competence_graduation = None
        open_world_graduation = None
        adversarial_open_world_graduation = None
        active_stage = "FOUNDATION_GAMES_V1"
        brain_lab = getattr(self.wakeful, "brain_lab", None)
        owner_goal = self._active_owner_goal_context(now=now)
        owner_commitment_timeslice = owner_goal is not None

        def run_arena(**kwargs):
            if owner_commitment_timeslice:
                return {
                    "status": "DEFERRED_ACCELERATION_FOR_OWNER_COMMITMENT",
                    "owner_goal_id": str(owner_goal.get("goal_id") or ""),
                    "bounded_embodied_steps": 12,
                }
            return brain_lab.run_accelerated_arena(**kwargs)

        if brain_lab is not None and hasattr(brain_lab, "run_accelerated_arena"):
            graduation = brain_lab.game_intelligence.graduation_status()
            competence_graduation = brain_lab.foundation_competence()
            if not competence_graduation.get("graduated"):
                active_stage = "FOUNDATION_SKILL_REMEDIATION"
                deficits = tuple(
                    str(value).upper()
                    for value in competence_graduation.get("remediation_environments") or ()
                )
                arena = run_arena(
                    environments=deficits or brain_lab.ENVIRONMENTS,
                    sessions_per_environment=6,
                    base_steps=40,
                    now=now,
                )
            elif graduation.get("graduated"):
                harder = brain_lab.open_world_curriculum_environments(limit=4)
                if harder:
                    open_world_graduation = (
                        brain_lab.game_intelligence.open_world_graduation_status(harder)
                    )
                    if not open_world_graduation.get("graduated"):
                        active_stage = "HARDER_OPEN_WORLD_CURRICULUM"
                        arena = run_arena(
                            environments=harder,
                            sessions_per_environment=3,
                            base_steps=40,
                            now=now,
                        )
                    else:
                        adversarial = brain_lab.adversarial_open_world_environments(
                            limit=4
                        )
                        active_stage = "LONG_HORIZON_ADVERSARIAL_OPEN_WORLD"
                        adversarial_open_world_graduation = (
                            brain_lab.game_intelligence.adversarial_open_world_graduation_status(
                                adversarial
                            )
                        )
                        if adversarial_open_world_graduation.get("graduated"):
                            active_stage = "OPEN_ENDED_SELF_CHALLENGE"
                        arena = run_arena(
                            environments=adversarial,
                            sessions_per_environment=(
                                1
                                if adversarial_open_world_graduation.get("graduated")
                                else 3
                            ),
                            base_steps=64,
                            now=now,
                        )
                else:
                    active_stage = "FOUNDATION_MAINTENANCE_WAITING_FOR_OPEN_WORLD"
                    arena = run_arena(
                        sessions_per_environment=1,
                        base_steps=24,
                        now=now,
                    )
            else:
                arena = run_arena(
                    sessions_per_environment=6,
                    base_steps=32,
                    now=now,
                )
        return {
            "task": "NEURAL_PLAY",
            **result,
            "accelerated_arena": arena,
            "graduation": graduation,
            "competence_graduation": competence_graduation,
            "open_world_graduation": open_world_graduation,
            "adversarial_open_world_graduation": adversarial_open_world_graduation,
            "active_game_stage": active_stage,
            "owner_commitment_timeslice": owner_commitment_timeslice,
        }

    def _neural_dream(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_neural_dream(now)
        return {
            "task": "NEURAL_DREAM",
            **result,
        }

    def _neural_transfer(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_neural_transfer(now, training_steps=12)
        return {
            "task": "NEURAL_TRANSFER",
            **result,
        }

    def _neural_development(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_development_trial(now, steps=12)
        return {
            "task": "NEURAL_DEVELOPMENT",
            **result,
        }

    def _neural_development_transfer(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_development_transfer(now, training_steps=12)
        return {
            "task": "NEURAL_DEVELOPMENT_TRANSFER",
            **result,
        }

    def _neural_evolution_consolidation(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_evolution_consolidation(now, steps=24)
        return {
            "task": "NEURAL_EVOLUTION_CONSOLIDATION",
            **result,
        }

    def _resource(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "RESOURCE_REVIEW", "status": "OK"}
        pressure = "UNKNOWN"
        from .runtime_backend import chatgpt_only_mode
        chatgpt_only = chatgpt_only_mode()
        try:
            observation = self.resource_economy.observe(now)
            rows["resource_observation"] = observation
            routing_policy = (
                observation.get("llm_routing_policy")
                if isinstance(observation, dict) else {}
            ) or {}
            chatgpt_only = chatgpt_only or (
                str(routing_policy.get("routing_mode") or "").upper()
                == "CHATGPT_ONLY"
            )
            projection = self.content_mind.public_projection().get("resource_awareness") or {}
            current = projection.get("current") or {}
            pressure = str(
                (routing_policy.get("pressure") if chatgpt_only else None)
                or current.get("pressure")
                or "UNKNOWN"
            )
        except Exception as exc:  # noqa: BLE001
            rows["resource_observation"] = self._error(exc)
            rows["status"] = "DEGRADED"

        infrastructure_pressure = "UNKNOWN"
        initial_infrastructure_pressure = "UNKNOWN"
        state_root = getattr(self, "state_root", None) or getattr(
            self.resource_economy, "state_root", None
        )
        if state_root is None:
            rows["survival_resource_observation"] = {
                "status": "UNAVAILABLE_NO_STATE_ROOT"
            }
        else:
            try:
                from .survival_resources import SurvivalResourceManager
                survival = SurvivalResourceManager(Path(state_root))
                observed = survival.observe(now=now)
                rows["survival_resource_observation"] = observed
                infrastructure_pressure = str(observed.get("pressure") or "UNKNOWN")
                initial_infrastructure_pressure = infrastructure_pressure
                network_service = getattr(self, "network_service", None)
                if network_service is not None and not any(
                    row.get("status") == "ACTIVE"
                    for row in survival.remote_hosts()
                ):
                    try:
                        rows["remote_machine_discovery"] = (
                            survival.discover_agent_network_hosts(
                                network_service, now=now
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        rows["remote_machine_discovery"] = {
                            "status": "UNAVAILABLE",
                            **self._error(exc),
                        }
                actions = survival.execute_approved(now=now)
                rows["survival_resource_actions"] = actions
                if actions.get("results"):
                    after_actions = survival.observe(now=now)
                    rows["survival_resource_after_actions"] = after_actions
                    infrastructure_pressure = str(
                        after_actions.get("pressure") or infrastructure_pressure
                    )
                    # Some safe actions unlock another safe action immediately
                    # (for example: provision receiver -> create first replica).
                    # Execute one bounded follow-up phase in the same review so
                    # onboarding does not stall until the next hourly cadence.
                    followup_actions = survival.execute_approved(now=now)
                    if followup_actions.get("results"):
                        rows["survival_resource_followup_actions"] = followup_actions
                        after_followup = survival.observe(now=now)
                        rows["survival_resource_after_followup_actions"] = after_followup
                        infrastructure_pressure = str(
                            after_followup.get("pressure") or infrastructure_pressure
                        )
            except Exception as exc:  # noqa: BLE001
                rows["survival_resource_observation"] = self._error(exc)
                rows["status"] = "DEGRADED"

        pressure_rank = {"UNKNOWN": 0, "NORMAL": 1, "WATCH": 2, "CONSERVE": 3, "CRITICAL": 4}
        combined_pressure = max(
            (pressure, infrastructure_pressure),
            key=lambda value: pressure_rank.get(str(value).upper(), 0),
        )
        pressure = str(combined_pressure).upper()
        self.wakeful.set_resource_pressure(pressure)

        rows["llm_provider_mode"] = (
            "LIVINGRUNTIME_CHATGPT_ONLY" if chatgpt_only else "MULTI_PROVIDER"
        )

        model_reputation = getattr(self, "model_reputation", None)
        if model_reputation is not None and not chatgpt_only:
            try:
                rows["model_reputation"] = model_reputation.observe(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["model_reputation"] = self._error(exc)
                rows["status"] = "DEGRADED"
        nvidia_catalog = getattr(self, "nvidia_catalog", None)
        if nvidia_catalog is not None and not chatgpt_only:
            try:
                rows["nvidia_capability_catalog"] = nvidia_catalog.observe(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["nvidia_capability_catalog"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if chatgpt_only:
            rows["resource_scout"] = {"status": "SKIPPED_CHATGPT_ONLY"}
        else:
            try:
                rows["resource_scout"] = self.resource_scout.maybe_discover(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["resource_scout"] = self._error(exc)
                rows["status"] = "DEGRADED"
        capability_evolution = getattr(self, "capability_evolution", None)
        if capability_evolution is not None:
            try:
                rows["capability_evolution"] = capability_evolution.review(
                    now=now,
                    cycle=int(now.timestamp() // 3600),
                    pressure=pressure,
                )
            except Exception as exc:  # noqa: BLE001
                rows["capability_evolution"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if chatgpt_only:
            rows["resource_verification"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["provider_leads"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["provider_verification"] = {"status": "SKIPPED_CHATGPT_ONLY"}
        else:
            try:
                rows["resource_verification"] = self.resource_scout.verify_pending(now=now, limit=8)
            except Exception as exc:  # noqa: BLE001
                rows["resource_verification"] = self._error(exc)
                rows["status"] = "DEGRADED"
            try:
                rows["provider_leads"] = self.resource_scout.expand_provider_leads(now=now, limit=12)
            except Exception as exc:  # noqa: BLE001
                rows["provider_leads"] = self._error(exc)
                rows["status"] = "DEGRADED"
            try:
                rows["provider_verification"] = self.resource_scout.verify_provider_leads(now=now, limit=8)
            except Exception as exc:  # noqa: BLE001
                rows["provider_verification"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if chatgpt_only:
            rows["provider_trial_plans"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["provider_trials"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["capability_windfall"] = {"status": "SKIPPED_CHATGPT_ONLY"}
        else:
            try:
                trial_plans = self.resource_scout.plan_trials(now=now, limit=3)
                rows["provider_trial_plans"] = trial_plans
                plans = list(trial_plans.get("plans") or [])
                local_status = str((trial_plans.get("local_inference") or {}).get("status") or "UNKNOWN")
                autonomous_pending = bool(trial_plans.get("autonomous_work_pending"))
                credential_resolutions = []
                for credential_env in trial_plans.get("available_credential_envs") or []:
                    credential_resolutions.extend(self.human_assistance.fulfill_matching(
                        kind="MODEL_RESOURCE_ACCESS", detail_key="credential_env",
                        detail_value=str(credential_env), now=now,
                        evidence_refs=(f"env-presence:{credential_env}",),
                    ))
                if credential_resolutions:
                    rows["provider_access_resolution"] = {
                        "status": "FULFILLED",
                        "request_ids": [row.get("request_id") for row in credential_resolutions],
                    }
                if autonomous_pending:
                    self.wakeful.schedule_resource_followup(now, seconds=300)
                    rows["resource_followup"] = {
                        "status": "SCHEDULED",
                        "reason": "unverified autonomous resource/provider evidence remains",
                        "pending_verification_count": int(trial_plans.get("pending_verification_count") or 0),
                        "pending_provider_verification_count": int(trial_plans.get("pending_provider_verification_count") or 0),
                        "delay_seconds": 300,
                    }
                ready = next((item for item in plans if item.get("status") == "READY_TO_TRIAL"), None)
                if ready is not None and not chatgpt_only:
                    trial_result = self.resource_scout.run_ready_trials(now=now, provider_limit=2)
                    rows["provider_trials"] = trial_result
                    if bool(trial_result.get("replan_required")):
                        self.wakeful.schedule_resource_followup(now, seconds=300)
                        rows["resource_followup"] = {
                            "status": "SCHEDULED",
                            "reason": "provider trial evidence requires alternate routes",
                            "pending_verification_count": int(trial_plans.get("pending_verification_count") or 0),
                            "delay_seconds": 300,
                        }
                    resolutions = []
                    for provider in trial_result.get("providers") or []:
                        if int(provider.get("completed_count") or 0) <= 0:
                            continue
                        fulfilled = self.human_assistance.fulfill_matching(
                            kind="MODEL_RESOURCE_ACCESS", detail_key="credential_env",
                            detail_value=str(provider.get("credential_env") or ""), now=now,
                            evidence_refs=tuple(str(x) for x in provider.get("trial_ids") or () if x),
                        )
                        resolutions.extend(fulfilled)
                    if resolutions:
                        rows["provider_access_resolution"] = {
                            "status": "FULFILLED",
                            "request_ids": [row.get("request_id") for row in resolutions],
                        }
                    elif any(
                        code in {"HTTP_401", "HTTP_403"}
                        for provider in trial_result.get("providers") or []
                        for code in provider.get("error_codes") or []
                    ):
                        rows["provider_access_resolution"] = {"status": "CREDENTIAL_REJECTED"}
                probe_windfall = getattr(
                    self.resource_scout, "probe_capability_windfall", None
                )
                if callable(probe_windfall) and not chatgpt_only:
                    try:
                        windfall = probe_windfall(now=now)
                        rows["capability_windfall"] = windfall
                        if windfall.get("status") == "AVAILABLE":
                            self._record_windfall_pending(windfall, now)
                            sync_policy = getattr(
                                self.resource_economy, "sync_llm_routing_policy", None
                            )
                            if callable(sync_policy):
                                rows["llm_routing_policy"] = sync_policy(now=now)
                            schedule = getattr(
                                self.wakeful, "schedule_windfall_reflection", None
                            )
                            if callable(schedule):
                                schedule(now, seconds=60)
                                rows["windfall_feast"] = {
                                    "status": "SCHEDULED",
                                    "task": "REFLECT",
                                    "reason": "new high-capability free model verified",
                                    "model": windfall.get("model"),
                                    "capability_tier": windfall.get("capability_tier"),
                                    "capability_score": windfall.get("capability_score"),
                                }
                    except Exception as exc:  # noqa: BLE001
                        rows["capability_windfall"] = self._error(exc)
                        rows["status"] = "DEGRADED"

                if chatgpt_only:
                    rows["provider_trials"] = {"status": "SKIPPED_CHATGPT_ONLY"}
                    rows["capability_windfall"] = {"status": "SKIPPED_CHATGPT_ONLY"}

                top = plans[0] if plans else None
                if (
                    not chatgpt_only
                    and not autonomous_pending
                    and ready is None and top is not None and top.get("status") == "NEEDS_CREDENTIAL"
                    and local_status == "DEFER_LOCAL_RESOURCE_PRESSURE"
                ):
                    request = self.human_assistance.request(
                        kind="MODEL_RESOURCE_ACCESS",
                        urgency="NORMAL",
                        summary=(
                            f"我已验证 {top['provider_label']} 是当前更值得试的低成本模型资源，"
                            "但本机资源压力不适合新增本地推理栈，而且我缺少访问凭据。"
                        ),
                        requested_action=(
                            f"请在官方 {top['signup_url']} 创建/获取 {top['credential_env']}，"
                            "并把它配置到 Content Agent 的受保护服务器环境中；不要通过聊天或企业微信发送密钥明文。"
                        ),
                        now=now,
                        details={
                            "plan_id": top.get("plan_id"),
                            "lead_id": top.get("lead_id"),
                            "provider_label": top.get("provider_label"),
                            "credential_env": top.get("credential_env"),
                            "signup_url": top.get("signup_url"),
                            "priority_score": top.get("priority_score"),
                            "reasons": top.get("reasons"),
                            "local_inference": trial_plans.get("local_inference"),
                        },
                    )
                    rows["provider_access_request"] = {
                        "request_id": request.get("request_id"),
                        "delivery_status": request.get("delivery_status"),
                        "kind": request.get("kind"),
                    }
            except Exception as exc:  # noqa: BLE001
                rows["provider_trial_plans"] = self._error(exc)
                rows["status"] = "DEGRADED"

        initial_rank = pressure_rank.get(str(initial_infrastructure_pressure).upper(), 0)
        final_rank = pressure_rank.get(str(infrastructure_pressure).upper(), 0)
        if initial_rank > 0 and final_rank > 0:
            if final_rank < initial_rank:
                utility = min(1.0, 0.45 + 0.20 * (initial_rank - final_rank))
            elif final_rank > initial_rank:
                utility = max(-1.0, -0.45 - 0.20 * (final_rank - initial_rank))
            else:
                utility = 0.0
            rows["world_utility"] = round(utility, 6)
            rows["world_utility_evidence"] = {
                "source": "SURVIVAL_PRESSURE_DELTA",
                "before": str(initial_infrastructure_pressure).upper(),
                "after": str(infrastructure_pressure).upper(),
                "world_grounded": True,
            }
        return rows

    def _self_repair(self, now: datetime) -> dict[str, Any]:
        result = self._self_repair_recovery(now, max_items=3)
        return {
            "task": "SELF_REPAIR",
            **result,
        }

    def _self_repair_recovery(
        self,
        now: datetime,
        *,
        max_items: int,
    ) -> dict[str, Any]:
        """Advance bounded repair work from a recovery-safe task lane."""
        self_repair = getattr(self, "self_repair", None)
        if self_repair is None:
            return {"status": "SELF_REPAIR_UNAVAILABLE"}
        run_batch = getattr(self_repair, "run_batch", None)
        result = (
            run_batch(now=now, max_items=max_items)
            if callable(run_batch)
            else self_repair.run_once(now=now)
        )
        repair_status = str(result.get("status") or "")
        nested = (
            list(result.get("results") or ())
            if repair_status == "BATCH_PROGRESSED"
            else [result]
        )
        repair_statuses = {
            str(item.get("status") or "")
            for item in nested
            if isinstance(item, dict)
        }
        schedule = getattr(self.wakeful, "schedule_self_maintenance", None)
        immediate = {
            "GENERATED_REPAIR_PROPOSED",
            "REQUESTED_TO_PI_REMOTE",
            "CANDIDATE_REJECTED_REPLAN_QUEUED",
            "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED",
        }
        if callable(schedule) and repair_statuses & immediate:
            retry_values = [
                int(item.get("retry_seconds") or 60)
                for item in nested
                if isinstance(item, dict)
                and str(item.get("status") or "") in immediate
            ]
            schedule(now, seconds=min(retry_values or [60]))
        elif callable(schedule) and "GENERATED_PLAN_DEFERRED" in repair_statuses:
            retry_values = [
                int(item.get("retry_seconds") or 900)
                for item in nested
                if isinstance(item, dict)
                and str(item.get("status") or "") == "GENERATED_PLAN_DEFERRED"
            ]
            schedule(now, seconds=min(retry_values or [900]))
        return result

    def _reflect(
        self,
        now: datetime,
        *,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        from .livingruntime_chatgpt import LivingRuntimeChatGPTProviderError

        rows: dict[str, Any] = {"task": "REFLECT", "status": "OK"}
        continuous = getattr(self.daily_reflection, "reflect_now", None)
        if callable(continuous):
            try:
                rows["continuous_reflection"] = self._run_async(
                    continuous(now, relevant_refs=relevant_refs)
                )
            except LivingRuntimeChatGPTProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                rows["continuous_reflection"] = self._error(exc)
                rows["status"] = "DEGRADED"
        else:
            rows["continuous_reflection"] = {
                "status": "CAPABILITY_UNAVAILABLE",
            }
        try:
            cognitive = getattr(
                self.daily_reflection,
                "maybe_generate_cognitive",
                None,
            )
            rows["daily_reflection"] = (
                self._run_async(cognitive(now))
                if callable(cognitive)
                else self.daily_reflection.maybe_generate(now)
            )
        except LivingRuntimeChatGPTProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            rows["daily_reflection"] = self._error(exc)
            rows["status"] = "DEGRADED"
        self_maintenance = getattr(self, "self_maintenance", None)
        if self_maintenance is not None:
            try:
                rows["self_maintenance"] = self_maintenance.review()
            except Exception as exc:  # noqa: BLE001
                rows["self_maintenance"] = self._error(exc)
                rows["status"] = "DEGRADED"
        try:
            rows["evolution_deliberation"] = self._evolution_deliberation(now)
        except LivingRuntimeChatGPTProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            rows["evolution_deliberation"] = self._error(exc)
            rows["status"] = "DEGRADED"
        return rows

    def _windfall_deliberation(
        self, runtime: Any, pending: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        deliberate = getattr(runtime, "deliberate_windfall_self_review", None)
        if not callable(deliberate):
            return {"status": "WINDFALL_RUNTIME_CAPABILITY_UNAVAILABLE"}
        if not bool(getattr(runtime, "has_advisory_backend", False)):
            return {"status": "WINDFALL_BACKEND_UNAVAILABLE"}
        refs = [
            str(x) for x in pending.get("trial_ids") or () if str(x)
        ]
        event_id = str(pending.get("event_id") or "")
        model = str(pending.get("model") or "")
        if event_id:
            refs.append(event_id)
        if model:
            refs.append("windfall-model:" + model)
        hypothesis = self._run_async(
            deliberate(evidence_refs=refs, max_tokens=1800)
        )
        persist = getattr(runtime, "persist", None)
        if callable(persist):
            persist()
        hypothesis_id = str(getattr(hypothesis, "hypothesis_id", "") or "")
        self._consume_windfall_pending(pending, hypothesis_id, now)
        return {
            "status": "WINDFALL_PROPOSED",
            "hypothesis_id": hypothesis_id,
            "model": model,
            "confidence": float(getattr(hypothesis, "confidence", 0.0) or 0.0),
            "fitness_dimensions": sorted(
                dict(getattr(hypothesis, "expected_fitness", {}) or {})
            ),
            "observed_at": now.isoformat(),
            "windfall_used": True,
            "token_budget": 1800,
        }

    def _fallback_evolution_hypothesis(self, runtime: Any, candidate: Any, error: Exception):
        proposer = getattr(runtime, "propose_evolution_insight", None)
        if not callable(proposer):
            raise error
        need_id = str(getattr(candidate, "need_id", "") or "")
        statement = str(getattr(candidate, "statement", "") or "Improve the open evolution need").strip()
        target = str(getattr(candidate, "target_capability", "") or "the affected capability").strip()
        evidence_refs = [str(x) for x in getattr(candidate, "evidence_refs", ()) or () if str(x)]
        return proposer(
            statement=(
                "A bounded shadow trial may improve this open need without expanding authority: "
                + statement[:900]
            ),
            proposed_change=(
                f"Prototype a reversible improvement for {target}; preserve current authority boundaries, "
                "record before/after evidence, run focused and full regression, and require observed fitness "
                "improvement before adoption."
            ),
            evidence_refs=evidence_refs,
            need_id=need_id or None,
            expected_fitness={
                "autonomy": 0.20,
                "reliability": 0.20,
                "reversibility": 0.15,
            },
            confidence=0.25,
        )

    def _evolution_deliberation(self, now: datetime) -> dict[str, Any]:
        runtime = getattr(self.app, "domain_runtime", None)
        deliberate = getattr(runtime, "deliberate_evolution_need", None)
        if runtime is None or not callable(deliberate):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}

        from .runtime_backend import chatgpt_only_mode
        pending_windfall = (
            None if chatgpt_only_mode() else self._load_windfall_pending()
        )
        needs = list(getattr(runtime, "evolution_needs", ()) or ())
        if not needs:
            if pending_windfall is not None:
                return self._windfall_deliberation(runtime, pending_windfall, now)
            return {"status": "NO_OPEN_NEEDS"}

        active_hypothesis_needs: set[str] = set()
        for hypothesis in list(
            getattr(runtime, "evolution_hypotheses", ()) or ()
        ):
            status = getattr(hypothesis, "status", "")
            status_value = str(getattr(status, "value", status))
            if status_value not in {"PROPOSED", "SUPPORTED"}:
                continue
            need_id = str(getattr(hypothesis, "need_id", "") or "")
            if need_id:
                active_hypothesis_needs.add(need_id)

        ordered = sorted(
            needs,
            key=lambda item: (
                float(getattr(item, "pressure", 0.0) or 0.0),
                int(getattr(item, "created_cycle", 0) or 0),
            ),
            reverse=True,
        )
        candidate = next(
            (
                item
                for item in ordered
                if str(getattr(item, "need_id", "") or "")
                not in active_hypothesis_needs
            ),
            None,
        )
        if candidate is None:
            if pending_windfall is not None:
                return self._windfall_deliberation(runtime, pending_windfall, now)
            return {
                "status": "ALREADY_PROPOSED",
                "open_need_count": len(needs),
            }
        if not bool(getattr(runtime, "has_advisory_backend", False)):
            return {
                "status": "BACKEND_UNAVAILABLE",
                "need_id": str(getattr(candidate, "need_id", "") or ""),
                "pressure": float(getattr(candidate, "pressure", 0.0) or 0.0),
            }

        windfall_active = False
        state_root = getattr(self, "state_root", None)
        if state_root is not None and not chatgpt_only_mode():
            policy_path = (
                state_root / "application" / "llm_resource_pool" / "policy.json"
            )
            try:
                import json
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                windfall_active = bool(
                    isinstance(policy, dict) and policy.get("windfall_active")
                )
            except (OSError, ValueError, TypeError):
                windfall_active = False
        token_budget = 1800 if windfall_active else 800
        fallback_reason = None
        try:
            hypothesis = self._run_async(
                deliberate(
                    str(getattr(candidate, "need_id")),
                    max_tokens=token_budget,
                )
            )
        except ValueError as exc:
            if not str(exc).startswith("evolution "):
                raise
            hypothesis = self._fallback_evolution_hypothesis(runtime, candidate, exc)
            fallback_reason = type(exc).__name__ + ":" + str(exc)[:180]
        persist = getattr(runtime, "persist", None)
        if callable(persist):
            persist()
        hypothesis_id = str(
            getattr(hypothesis, "hypothesis_id", "") or ""
        )
        if pending_windfall is not None and windfall_active:
            self._consume_windfall_pending(
                pending_windfall, hypothesis_id, now
            )
        expected_fitness = dict(
            getattr(hypothesis, "expected_fitness", {}) or {}
        )
        return {
            "status": "PROPOSED_FALLBACK" if fallback_reason else "PROPOSED",
            "need_id": str(getattr(candidate, "need_id", "") or ""),
            "hypothesis_id": str(
                getattr(hypothesis, "hypothesis_id", "") or ""
            ),
            "confidence": float(
                getattr(hypothesis, "confidence", 0.0) or 0.0
            ),
            "fitness_dimensions": sorted(expected_fitness),
            "pressure": float(getattr(candidate, "pressure", 0.0) or 0.0),
            "observed_at": now.isoformat(),
            "windfall_used": windfall_active,
            "token_budget": token_budget,
            "fallback_reason": fallback_reason,
        }

    def _x(self, now: datetime) -> dict[str, Any]:
        if self.x_loop is None:
            return {"task": "CHECK_X", "status": "DISABLED"}
        try:
            result = self._run_async(self.x_loop.observe(now=now))
            self.wakeful.emit_x_mentions(count=int(result.get("new_mentions") or 0), observed_at=now)
            return {"task": "CHECK_X", "status": "OK", "x_social_observation": result}
        except Exception as exc:  # noqa: BLE001
            return {"task": "CHECK_X", "status": "DEGRADED", "x_social_observation": self._error(exc)}

    def _runtime_tick(self, now: datetime) -> tuple[Any | None, dict[str, Any]]:
        try:
            guard = self.network_service.execution_lock if self.network_service is not None else nullcontext()
            with guard:
                result = self.app.run_tick(as_of_time=now.isoformat())
            return result, {"application": result.to_payload()}
        except Exception as exc:  # noqa: BLE001
            return None, {"application": self._error(exc)}

    def _observe(self, now: datetime) -> dict[str, Any]:
        result, rows = self._runtime_tick(now)
        rows.update({"task": "OBSERVE_WORLD", "status": "OK" if result is not None else "DEGRADED"})
        if result is None or not result.generation_ready:
            return rows

        # World observation must not depend on the optional Trading Agent
        # Network producer. The content explorer already owns the bounded
        # public sensing stack (RSS, OKX and any configured evidence sources).
        explorer = getattr(getattr(self, "continuous_loop", None), "explorer", None)
        observed_items: list[dict[str, Any]] = []
        if explorer is not None:
            try:
                evidence_pool = self._run_async(
                    explorer.collect(now=now, active=True)
                )
                observed_items = [
                    row
                    for row in (evidence_pool.get("items") or ())
                    if isinstance(row, dict)
                ]
                failures = [
                    row
                    for row in (evidence_pool.get("failures") or ())
                    if isinstance(row, dict)
                ]
                evidence_ids = [
                    str(row.get("evidence_id") or "").strip()
                    for row in observed_items
                    if str(row.get("evidence_id") or "").strip()
                ]
                receipt_path = (
                    Path(self.state_root)
                    / "application"
                    / "world_observation_receipt.json"
                )
                prior_ids: set[str] = set()
                try:
                    prior = json.loads(receipt_path.read_text(encoding="utf-8"))
                    prior_ids = {
                        str(value)
                        for value in (prior.get("seen_evidence_ids") or ())
                        if str(value)
                    }
                except (OSError, ValueError, json.JSONDecodeError):
                    prior_ids = set()
                novel_ids = [
                    evidence_id
                    for evidence_id in evidence_ids
                    if evidence_id not in prior_ids
                ]
                combined_ids = list(
                    dict.fromkeys((*evidence_ids, *prior_ids))
                )[:256]
                receipt = {
                    "schema_version": "ferro-world-observation.v1",
                    "observed_at": now.isoformat(),
                    "seen_evidence_ids": combined_ids,
                    "current_evidence_count": len(evidence_ids),
                    "novel_evidence_count": len(novel_ids),
                }
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = receipt_path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(
                        receipt,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
                tmp.replace(receipt_path)
                rows["world_evidence"] = {
                    "status": "OBSERVED" if observed_items else "NO_EVIDENCE",
                    "item_count": len(observed_items),
                    "novel_evidence_count": len(novel_ids),
                    "failure_count": len(failures),
                    "failures": failures[:8],
                    "evidence_refs": novel_ids[:12],
                }
                if novel_ids:
                    # Weak reward for genuinely new external evidence. This is
                    # intentionally far below task/business outcome utility.
                    rows["world_utility"] = round(
                        min(0.24, 0.08 + 0.02 * min(len(novel_ids), 8)),
                        6,
                    )
                    rows["world_utility_evidence"] = novel_ids[:12]
                else:
                    rows["world_utility"] = 0.0
                    rows["world_utility_evidence"] = []
            except Exception as exc:  # noqa: BLE001
                rows["world_evidence"] = self._error(exc)

        if self.draft_worker is None:
            if not observed_items and rows.get("world_evidence", {}).get("status") == "ERROR":
                rows["status"] = "DEGRADED"
            return rows
        try:
            draft = self._run_async(self.draft_worker.run_once(now=now))
            draft_payload = draft.to_payload()
            rows["draft_worker"] = draft_payload
            draft_status = str(draft_payload.get("status") or "").upper()
            evidence_ref = str(
                draft_payload.get("artifact_id")
                or draft_payload.get("contract_id")
                or draft_payload.get("task_id")
                or ""
            ).strip()
            if evidence_ref:
                self.wakeful.emit_network_evidence(
                    evidence_ref=evidence_ref,
                    observed_at=now,
                )
            elif draft_status in {"NO_RESEARCH_PROVIDER", "NO_RESEARCH"}:
                rows["draft_worker"]["required_for_observation"] = False
                if (
                    not observed_items
                    and rows.get("world_evidence", {}).get("status") == "ERROR"
                ):
                    rows["status"] = "DEGRADED"
        except Exception as exc:  # noqa: BLE001
            code = str(getattr(exc, "code", "") or "")
            if code or type(exc).__name__ == "NetworkError":
                self.wakeful.emit_network_error(code=code or type(exc).__name__, observed_at=now)
            message = str(exc).strip()
            blocked = code in {"INVALID_SIGNATURE", "TAMPER_DETECTED"} or (
                type(exc).__name__ == "NetworkError" and message.upper() in {"UNSIGNED EVIDENCE", "TAMPER_DETECTED"}
            )
            rows["draft_worker"] = self._error(exc, code=code)
            if blocked:
                rows["draft_worker"]["status"] = "BLOCKED_BY_NETWORK_TRUST"
            elif observed_items:
                rows["draft_worker"]["status"] = "OPTIONAL_SOURCE_UNAVAILABLE"
                rows["draft_worker"]["required_for_observation"] = False
            else:
                rows["status"] = "DEGRADED"
        return rows

    def _outcomes(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "CHECK_OUTCOMES", "status": "OK"}
        for name, collector in (
            ("google_search_console", self.gsc_collector),
            ("website_outcomes", self.web_outcome_collector),
            ("website_engagement", self.engagement_outcome_collector),
        ):
            if collector is None:
                continue
            try:
                method = collector.collect
                rows[name] = method(now=now)
            except Exception as exc:  # noqa: BLE001
                rows[name] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.web_outcome_collector is not None:
            try:
                from .content_action_prediction import settle_action_predictions
                learning = settle_action_predictions(
                    state_root=self.state_root,
                    account=self.web_base_url,
                    as_of=now,
                    mind=self.content_mind,
                )
                rows["content_action_prediction_learning"] = learning
                feedback_rows = []
                for outcome in learning.get("recorded") or ():
                    feedback_rows.append(self.wakeful.apply_prediction_outcome_feedback(outcome))
                rows["neural_prediction_learning"] = {
                    "status": "APPLIED" if feedback_rows else "NO_NEW_OUTCOMES",
                    "count": len(feedback_rows),
                    "feedback": feedback_rows,
                    "publication_authority": False,
                }
                competition_rows = []
                if hasattr(self.wakeful, "settle_brain_cortex"):
                    prediction_cycles: dict[str, str] = {}
                    cycles_root = Path(self.state_root) / "content" / "continuous" / "cycles"
                    for prediction_path in cycles_root.glob("*/action_prediction.json"):
                        try:
                            prediction_payload = json.loads(prediction_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError, json.JSONDecodeError):
                            continue
                        prediction_id = str(prediction_payload.get("prediction_id") or "")
                        prediction_cycle = str(prediction_payload.get("cycle") or "")
                        if prediction_id and prediction_cycle:
                            prediction_cycles[prediction_id] = prediction_cycle
                    for outcome in learning.get("recorded") or ():
                        prediction_id = str(outcome.get("prediction_id") or "")
                        cycle_id = prediction_cycles.get(prediction_id)
                        if not cycle_id:
                            continue
                        expected = float(outcome.get("expected_pageviews") or 0.0)
                        actual = float(outcome.get("actual_pageviews") or 0.0)
                        reliability = float(outcome.get("reliability") or 0.0)
                        utility = math.tanh((actual - expected) / max(1.0, expected + 1.0)) * reliability
                        raw_observed_at = outcome.get("observed_at")
                        if isinstance(raw_observed_at, datetime):
                            observed_at = raw_observed_at
                        else:
                            try:
                                observed_at = datetime.fromisoformat(str(raw_observed_at))
                            except ValueError:
                                observed_at = now
                        competition_rows.append(self.wakeful.settle_brain_cortex(
                            cycle=cycle_id,
                            utility=utility,
                            evidence_refs=outcome.get("evidence_refs") or (),
                            observed_at=observed_at,
                        ))
                rows["brain_vs_cortex_learning"] = {
                    "status": "SETTLED" if competition_rows else "NO_NEW_OUTCOMES",
                    "count": len(competition_rows),
                    "results": competition_rows,
                }
                evolution_bridge = getattr(self, "evolution_bridge", None)
                if evolution_bridge is not None:
                    rows["evolution_learning"] = evolution_bridge.record_action_outcomes(
                        learning.get("recorded") or ()
                    )
                    rows["memory_evolution_learning"] = evolution_bridge.sync_memory_evolution()
            except Exception as exc:  # noqa: BLE001
                rows["content_action_prediction_learning"] = self._error(exc)
                rows["status"] = "DEGRADED"
        evolution_bridge = getattr(self, "evolution_bridge", None)
        if evolution_bridge is not None:
            try:
                rows["context_builder_evolution"] = (
                    evolution_bridge.sync_context_builder_evolution()
                )
            except Exception as exc:  # noqa: BLE001
                rows["context_builder_evolution"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.website_experiment_evaluator is not None:
            try:
                rows["website_experiment_evaluation"] = self.website_experiment_evaluator.evaluate(as_of=now)
            except Exception as exc:  # noqa: BLE001
                rows["website_experiment_evaluation"] = self._error(exc)
                rows["status"] = "DEGRADED"
        return rows

    def _verify_prediction(self, now: datetime) -> dict[str, Any]:
        if self.continuous_loop is None:
            return {"task": "VERIFY_PREDICTION", "status": "DISABLED"}
        ledger = getattr(self.continuous_loop, "predictions", None)
        owner_goal = self._active_owner_goal_context()
        due_predictions = (
            tuple(ledger.due(now))
            if ledger is not None and callable(getattr(ledger, "due", None))
            else ()
        )
        overdue_owner_prediction = any(
            (
                due_at := getattr(prediction, "due_at", None)
            ) is not None
            and now - due_at.astimezone(timezone.utc)
            >= self.MAX_OWNER_PREDICTION_DEFER
            for prediction in due_predictions
        )
        if (
            owner_goal is not None
            and str(owner_goal.get("origin") or "") == "EXTERNAL_MISSION"
            and not overdue_owner_prediction
        ):
            deferred = []
            if (
                ledger is not None
                and callable(getattr(ledger, "due", None))
                and callable(getattr(ledger, "defer_verification", None))
            ):
                deferred = [
                    ledger.defer_verification(
                        prediction.prediction_id,
                        deferred_at=now,
                        delay=timedelta(minutes=15),
                        reason="OWNER_EXTERNAL_MISSION_PRIORITY",
                    )
                    for prediction in due_predictions
                ]
                if deferred:
                    sync = getattr(self.wakeful, "sync_prediction_wakes", None)
                    if callable(sync):
                        sync(now)
            return {
                "task": "VERIFY_PREDICTION",
                "status": "OWNER_MISSION_DEFERRED",
                "owner_goal_id": str(owner_goal.get("goal_id") or ""),
                "deferred_count": len(deferred),
                "retry_after": (
                    deferred[0].get("retry_after")
                    if deferred
                    else None
                ),
            }
        try:
            if due_predictions:
                for prediction in due_predictions:
                    self.wakeful.emit_prediction_due(
                        prediction_ref=str(prediction.prediction_id), observed_at=now,
                    )
            result = self._run_async(self.continuous_loop.verify_due_predictions(now=now))
            return {
                "task": "VERIFY_PREDICTION",
                "status": "OK",
                "prediction_verification": result,
                "owner_mission_calibration_debt": overdue_owner_prediction,
            }
        except Exception as exc:  # noqa: BLE001
            return {"task": "VERIFY_PREDICTION", "status": "DEGRADED", "prediction_verification": self._error(exc)}

    def _pending_chatgpt_cycle(self) -> dict[str, str] | None:
        """Return the newest unfinished cycle suspended on a durable ChatGPT request."""
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return None
        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        if not cycles_root.is_dir():
            return None
        candidates: list[tuple[float, dict[str, str]]] = []
        for marker in cycles_root.glob("*/waiting_for_chatgpt.json"):
            folder = marker.parent
            if (folder / "result.json").exists():
                continue
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                mtime = marker.stat().st_mtime
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "").upper() != "WAITING_FOR_CHATGPT":
                continue
            if payload.get("resumable") is not True:
                continue
            cycle = str(payload.get("cycle") or folder.name).strip()
            request_id = str(payload.get("request_id") or "").strip()
            if cycle != folder.name or not request_id:
                continue
            candidates.append((mtime, {"cycle": cycle, "request_id": request_id}))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _runtime_revision(self) -> str:
        """Return the deployed Ferro revision when release metadata is available."""
        release = Path(__file__).resolve().parents[2] / ".ferro-release.json"
        try:
            payload = json.loads(release.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return "unknown"
        if not isinstance(payload, dict):
            return "unknown"
        revision = str(
            payload.get("gitCommit")
            or payload.get("releaseId")
            or ""
        ).strip()
        return revision[:128] or "unknown"

    def _content_recovery(
        self,
        *,
        preflight: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Derive a fresh immutable cycle after a failed scheduled cycle.

        The failed result remains untouched. At most three recovery cycles are
        allowed for one base cycle, and a successful recovery suppresses
        duplicate work for the rest of that base window.
        """
        if str(preflight.get("status") or "") != "ALREADY_PROCESSED":
            return None
        result_path_raw = str(preflight.get("result_path") or "").strip()
        if not result_path_raw:
            return None
        result_path = Path(result_path_raw)
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        prior_status = str(prior.get("status") or "").upper()
        retryable_statuses = {"ERROR", "DEGRADED", "RECOVERY_INVALIDATED"}
        if prior_status not in retryable_statuses:
            return None

        base_cycle = str(preflight.get("cycle") or "").strip()
        base_epoch = base_cycle.rsplit("-", 1)[-1]
        state_root = getattr(self, "state_root", None)
        if not base_cycle or not base_epoch.isdigit() or state_root is None:
            return None

        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        prefix = f"v2-recovery-{base_epoch}-"
        completed: list[tuple[Path, str, str, str | None]] = []
        if cycles_root.exists():
            for folder in sorted(cycles_root.glob(prefix + "*")):
                result_file = folder / "result.json"
                if not result_file.exists():
                    continue
                try:
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                runtime_revision = None
                recovery_file = folder / "recovery.json"
                if recovery_file.exists():
                    try:
                        recovery_payload = json.loads(
                            recovery_file.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, json.JSONDecodeError):
                        recovery_payload = None
                    if isinstance(recovery_payload, dict):
                        value = str(
                            recovery_payload.get("runtime_revision") or ""
                        ).strip()
                        runtime_revision = value or None
                completed.append((
                    folder,
                    str(payload.get("status") or "").upper(),
                    str(payload.get("error_type") or ""),
                    runtime_revision,
                ))

        for folder, status, _error_type, _revision in reversed(completed):
            if status not in retryable_statuses:
                return {
                    "status": "ALREADY_RECOVERED",
                    "recovery_of": base_cycle,
                    "recovery_cycle": folder.name,
                    "previous_status": prior_status,
                    "attempt_count": len(completed),
                }

        # Provider outages have their own durable cooldown/backoff evidence and
        # recovery scheduler. Counting those transient availability failures
        # against the content-recovery cap can permanently strand an active
        # owner goal after three provider windows, even when a route later
        # recovers. Keep the three-attempt guard for content/application
        # failures, but let provider recovery continue at its bounded cooldown
        # cadence.
        current_revision = self._runtime_revision()
        bounded_failures = [
            row
            for row in completed
            if row[2] != "LLMProviderError"
            and (
                current_revision == "unknown"
                or row[3] == current_revision
            )
        ]
        if len(bounded_failures) >= 3:
            return {
                "status": "RECOVERY_LIMIT_REACHED",
                "recovery_of": base_cycle,
                "previous_status": prior_status,
                "attempt_count": len(completed),
                "bounded_failure_attempt_count": len(bounded_failures),
            }

        attempt = len(completed) + 1
        suffix = int(now.timestamp())
        recovery_cycle = f"v2-recovery-{base_epoch}-{attempt}-{suffix}"
        while (cycles_root / recovery_cycle).exists():
            suffix += 1
            recovery_cycle = f"v2-recovery-{base_epoch}-{attempt}-{suffix}"
        metadata = {
            "schema_version": "content-cycle-recovery.v1",
            "status": "READY",
            "recovery_of": base_cycle,
            "recovery_cycle": recovery_cycle,
            "previous_status": prior_status,
            "attempt": attempt,
            "triggered_at": now.isoformat(),
            "preserves_failed_cycle": True,
            "runtime_revision": current_revision,
        }
        folder = cycles_root / recovery_cycle
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "recovery.json.tmp"
        tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(folder / "recovery.json")
        return metadata

    def _mission_followup_identity(
        self,
        *,
        prior: dict[str, Any],
        result_path: Path,
    ) -> dict[str, str] | None:
        """Resolve which external mission owns a no-op or interrupted discovery cycle."""
        error_path = result_path.parent / "mission_discovery_error.json"
        error: dict[str, Any] | None = None
        if error_path.exists():
            try:
                payload = json.loads(error_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                error = payload

        # An interrupted discovery artifact is authoritative for this cycle even
        # when the ordinary fallback path later published something. That
        # publication did not execute the mission-bound discovery, so it must
        # not erase the mission retry identity.
        if error is None:
            mind_plan = prior.get("mind_plan")
            if isinstance(mind_plan, dict):
                goal = mind_plan.get("goal")
                if isinstance(goal, dict) and str(goal.get("origin") or "") == "EXTERNAL_MISSION":
                    goal_id = str(goal.get("goal_id") or "").strip()
                    mission_id = str(mind_plan.get("external_mission_id") or "").strip()
                    if goal_id:
                        return {
                            "goal_id": goal_id,
                            "mission_id": mission_id,
                            "reason": "EXTERNAL_MISSION_NO_MATERIAL_CHANGE",
                        }
            return None
        mission_id = str(error.get("mission_id") or "").strip()
        step_id = str(error.get("roadmap_step_id") or "").strip()
        experiment_id = str(error.get("experiment_id") or "").strip()

        # Compatibility for discovery errors written before mission identity was
        # persisted in the error artifact.
        if not mission_id:
            state_root = getattr(self, "state_root", None)
            if state_root is None:
                return None
            try:
                from .x_growth_mission import x_growth_mission_context

                mission = x_growth_mission_context(Path(state_root))
            except Exception:  # noqa: BLE001
                return None
            if str(mission.get("status") or "") != "ACTIVE":
                return None
            hard_stop = mission.get("hard_stop")
            if isinstance(hard_stop, dict) and hard_stop.get("active") is True:
                return None
            mission_id = str(mission.get("mission_id") or "").strip()
            roadmap = mission.get("roadmap") if isinstance(mission.get("roadmap"), dict) else {}
            candidates = [
                row for row in list(roadmap.get("steps") or ())
                if isinstance(row, dict)
                and row.get("status") in {"RUNNING", "READY"}
                and "content_creation" in set(row.get("resource_tags") or ())
            ]
            candidates.sort(
                key=lambda row: (
                    0 if row.get("status") == "RUNNING" else 1,
                    str(row.get("step_id") or ""),
                )
            )
            if candidates:
                step_id = str(candidates[0].get("step_id") or "").strip()
            experiment = mission.get("active_experiment")
            if isinstance(experiment, dict):
                experiment_id = str(experiment.get("experiment_id") or "").strip()

        if not mission_id:
            return None
        anchor = step_id or experiment_id or "research"
        return {
            "goal_id": f"external-mission-retry:{mission_id}:{anchor}",
            "mission_id": mission_id,
            "reason": "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED",
        }

    def _content_mission_followup(
        self,
        *,
        preflight: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Derive a bounded fresh cycle when an external mission learned nothing useful."""
        if str(preflight.get("status") or "") != "ALREADY_PROCESSED":
            return None
        result_path_raw = str(preflight.get("result_path") or "").strip()
        if not result_path_raw:
            return None
        result_path = Path(result_path_raw)
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        identity = self._mission_followup_identity(
            prior=prior,
            result_path=result_path,
        )
        if identity is None:
            return None
        prior_status = str(prior.get("status") or "").upper()
        interrupted_discovery = (
            str(identity.get("reason") or "")
            == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
        )
        if prior_status != "COGNITION_ONLY" and not interrupted_discovery:
            return None

        source_cycle = str(preflight.get("cycle") or "").strip()
        goal_id = str(identity.get("goal_id") or "").strip()
        mission_id = str(identity.get("mission_id") or "").strip()
        state_root = getattr(self, "state_root", None)
        if not source_cycle or not goal_id or state_root is None:
            return None

        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        completed: list[tuple[dict[str, Any], str]] = []
        if cycles_root.exists():
            for folder in cycles_root.glob("v2-mission-followup-*"):
                metadata_path = folder / "mission_followup.json"
                result_file = folder / "result.json"
                if not metadata_path.exists() or not result_file.exists():
                    continue
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if str(metadata.get("followup_of") or "") != source_cycle:
                    continue
                if str(metadata.get("goal_id") or "") != goal_id:
                    continue
                completed.append(
                    (metadata, str(payload.get("status") or "").upper())
                )
        completed.sort(key=lambda item: int(item[0].get("attempt") or 0))

        for metadata, status in reversed(completed):
            if status != "COGNITION_ONLY":
                return {
                    "status": "ALREADY_FOLLOWED_UP",
                    "followup_of": source_cycle,
                    "followup_cycle": metadata.get("followup_cycle"),
                    "goal_id": goal_id,
                    "mission_id": mission_id,
                    "attempt_count": len(completed),
                    "result_status": status,
                }

        if len(completed) >= 3:
            return {
                "status": "FOLLOWUP_LIMIT_REACHED",
                "followup_of": source_cycle,
                "goal_id": goal_id,
                "mission_id": mission_id,
                "attempt_count": len(completed),
            }

        attempt = len(completed) + 1
        suffix = int(now.timestamp())
        followup_cycle = f"v2-mission-followup-{attempt}-{suffix}"
        while (cycles_root / followup_cycle).exists():
            suffix += 1
            followup_cycle = f"v2-mission-followup-{attempt}-{suffix}"
        metadata = {
            "schema_version": "content-mission-followup.v1",
            "status": "READY",
            "followup_of": source_cycle,
            "followup_cycle": followup_cycle,
            "goal_id": goal_id,
            "mission_id": mission_id,
            "attempt": attempt,
            "triggered_at": now.isoformat(),
            "reason": str(identity.get("reason") or "EXTERNAL_MISSION_NO_MATERIAL_CHANGE"),
            "publication_authority": False,
        }
        folder = cycles_root / followup_cycle
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "mission_followup.json.tmp"
        tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(folder / "mission_followup.json")
        return metadata

    def _social_stage_backlog_candidate(self) -> dict[str, Any] | None:
        """Find one recent published cycle whose downstream social stage is incomplete."""
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return None
        cycles_root = (
            Path(state_root) / "content" / "continuous" / "cycles"
        )
        if not cycles_root.is_dir():
            return None
        try:
            folders = sorted(
                (path for path in cycles_root.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:200]
        except OSError:
            return None
        blocker = {}
        try:
            blocker = json.loads(
                (
                    Path(state_root)
                    / "content"
                    / "x"
                    / "publication_blocker.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            blocker = {}
        blocker_active = False
        provider_only_blocker = (
            isinstance(blocker, dict)
            and (
                str(blocker.get("blocker") or "") == "X_CREDITS_DEPLETED"
                or str(blocker.get("resource") or "") == "X_API_CREDITS"
            )
        )
        if (
            isinstance(blocker, dict)
            and str(blocker.get("status") or "") == "ACTIVE"
            and not provider_only_blocker
        ):
            try:
                retry_after = datetime.fromisoformat(
                    str(blocker.get("retry_after") or "").replace("Z", "+00:00")
                )
                blocker_active = retry_after.tzinfo is not None and datetime.now(timezone.utc) < retry_after.astimezone(timezone.utc)
            except ValueError:
                blocker_active = False
        published_candidate: dict[str, Any] | None = None
        for folder in folders:
            result_path = folder / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict):
                continue
            result_status = str(result.get("status") or "").upper()
            if result_status not in {"PUBLISHED", "HELD_BY_CHECKS"}:
                continue
            try:
                cognition = json.loads(
                    (folder / "cognition.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                cognition = {}
            if not bool(
                cognition.get("worth_saying")
                if isinstance(cognition, dict)
                else False
            ):
                continue
            if result_status == "HELD_BY_CHECKS":
                x_loop = getattr(self, "x_loop", None)
                growth = getattr(x_loop, "growth_mission", None)
                binding_method = getattr(growth, "publication_binding", None)
                if not callable(binding_method):
                    continue
                try:
                    binding = binding_method(
                        cycle=str(result.get("cycle") or folder.name)
                    )
                except Exception:  # noqa: BLE001
                    continue
                if (
                    not isinstance(binding, dict)
                    or not bool(binding.get("bound"))
                    or str(binding.get("status") or "") != "BOUND_X_EXPRESSION"
                ):
                    continue
                x_receipt_path = folder / "x_social_expression.json"
                try:
                    x_receipt = json.loads(
                        x_receipt_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    x_receipt = {}
                x_status = str(x_receipt.get("status") or "")
                if x_status in {
                    "POSTED", "ALREADY_EXPRESSED", "SKIPPED", "HELD_BY_X_SAFETY"
                }:
                    continue
                if blocker_active and x_status in {
                    "X_CREDITS_DEPLETED",
                    "X_RESOURCE_BLOCKED",
                }:
                    # A known external resource outage is not unfinished
                    # cognition.  Wait for the bounded retry window instead of
                    # burning another model turn and X API request every cycle.
                    continue
                return {
                    "cycle": str(result.get("cycle") or folder.name),
                    "result": result,
                    "reason": "MISSING_X_MISSION_EXPRESSION",
                }
            social_path = folder / "social_expression_drafts.json"
            try:
                social = json.loads(social_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                social = {}
            social_status = (
                str(social.get("status") or "")
                if isinstance(social, dict)
                else ""
            )
            if social_status in {"DRAFT_READY", "SKIPPED"}:
                continue
            if published_candidate is None:
                published_candidate = {
                    "cycle": str(result.get("cycle") or folder.name),
                    "result": result,
                    "reason": (
                        "MISSING_SOCIAL_DRAFT"
                        if not social_path.exists()
                        else f"SOCIAL_DRAFT_{social_status or 'INVALID'}"
                    ),
                }
            # Keep scanning: a mission-bound X-only expression backlog is a
            # stalled external commitment and takes precedence over generic
            # social drafting.
        return published_candidate

    def _run_social_stage(
        self,
        *,
        rows: dict[str, Any],
        continuous: dict[str, Any],
        now: datetime,
    ) -> None:
        """Run idempotent downstream social work for an already-published cycle."""
        social_loop = getattr(self, "social_expression_loop", None)
        if social_loop is not None:
            try:
                rows["social_expression_drafts"] = self._run_async(
                    social_loop.after_content_cycle(result=continuous, now=now)
                )
            except Exception as exc:  # noqa: BLE001
                rows["social_expression_drafts"] = self._error(exc)
                rows["status"] = "DEGRADED"
        for name, publisher in (
            ("x_browser_publication", getattr(self, "x_browser_publisher", None)),
            ("okx_orbit_publication", getattr(self, "okx_orbit_publisher", None)),
        ):
            if publisher is None:
                continue
            try:
                rows[name] = self._run_async(
                    publisher.after_content_cycle(
                        cycle=str(continuous.get("cycle") or ""),
                        now=now,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                rows[name] = self._error(exc)
                rows["status"] = "DEGRADED"
        x_loop = getattr(self, "x_loop", None)
        if x_loop is not None:
            try:
                x_result = self._run_async(
                    x_loop.after_content_cycle(result=continuous, now=now)
                )
                rows["x_social_expression"] = x_result
                state_root = getattr(self, "state_root", None)
                cycle = str(continuous.get("cycle") or "").strip()
                if state_root is not None and cycle and isinstance(x_result, dict):
                    receipt = (
                        Path(state_root) / "content" / "continuous" / "cycles"
                        / cycle / "x_social_expression.json"
                    )
                    receipt.parent.mkdir(parents=True, exist_ok=True)
                    tmp = receipt.with_suffix(".json.tmp")
                    tmp.write_text(
                        json.dumps(x_result, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    tmp.replace(receipt)
            except Exception as exc:  # noqa: BLE001
                rows["x_social_expression"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _recover_social_stage_backlog(
        self,
        *,
        rows: dict[str, Any],
        now: datetime,
        x_mission_only: bool = False,
    ) -> dict[str, Any] | None:
        backlog = self._social_stage_backlog_candidate()
        if backlog is None:
            return None
        if (
            x_mission_only
            and str(backlog.get("reason") or "") != "MISSING_X_MISSION_EXPRESSION"
        ):
            return None
        replay_rows: dict[str, Any] = {"status": "OK"}
        self._run_social_stage(
            rows=replay_rows,
            continuous=dict(backlog["result"]),
            now=now,
        )
        recovery = {
            "status": "REPLAYED",
            "cycle": str(backlog["cycle"]),
            "reason": str(backlog["reason"]),
            "results": replay_rows,
        }
        rows["social_stage_backlog_recovery"] = recovery
        return recovery

    def _owner_activated_external_goal_ids(self) -> set[str]:
        """Return external-mission goals with durable private-owner activation evidence."""
        return {
            goal_id
            for goal_id, row in self._owner_active_goal_proposals().items()
            if str(row.get("kind") or "") == "EXTERNAL_MISSION"
        }

    def _owner_active_goal_proposals(self) -> dict[str, dict[str, Any]]:
        """Latest canonical activated owner proposal for each backing runtime goal."""
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return {}
        proposals_root = (
            Path(state_root) / "application" / "operator_chat" / "goal_proposals"
        )
        if not proposals_root.is_dir():
            return {}
        rows: dict[str, tuple[str, dict[str, Any]]] = {}
        for path in proposals_root.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") != "ACTIVATED":
                continue
            semantic = str(row.get("semantic_status") or "CANONICAL")
            if semantic not in {"CANONICAL", "ACTIVE", ""}:
                continue
            goal_id = str(row.get("goal_id") or "").strip()
            if not goal_id:
                continue
            stamp = str(
                row.get("activated_at")
                or row.get("created_at")
                or ""
            )
            current = rows.get(goal_id)
            if current is None or stamp >= current[0]:
                rows[goal_id] = (stamp, dict(row))
        return {goal_id: row for goal_id, (_, row) in rows.items()}

    def _active_owner_goal_context(
        self,
        *,
        relevant_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        goals = list(getattr(runtime, "active_goals", ()) or ())
        seen_goal_ids = {
            str(getattr(goal, "goal_id", "") or "").strip()
            for goal in goals
        }
        for goal in (getattr(runtime, "goals", ()) or ()):
            status = str(
                getattr(
                    getattr(goal, "status", None),
                    "value",
                    getattr(goal, "status", ""),
                )
            )
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            if status == "ACTIVE" and goal_id not in seen_goal_ids:
                goals.append(goal)
                seen_goal_ids.add(goal_id)
        owner_proposals = self._owner_active_goal_proposals()
        owner_external_goal_ids = {
            goal_id
            for goal_id, row in owner_proposals.items()
            if str(row.get("kind") or "") == "EXTERNAL_MISSION"
        }
        # Runtime external missions are already durable operator-issued
        # commitments. Use them as an independent owner-authority source so a
        # stale/missing application proposal projection cannot demote an active
        # owner mission to an ordinary USER goal.
        for mission in (getattr(runtime, "external_missions", ()) or ()):
            mission_status = str(
                getattr(
                    getattr(mission, "status", None),
                    "value",
                    getattr(mission, "status", ""),
                )
            )
            issued_by = str(getattr(mission, "issued_by", "") or "").strip().lower()
            mission_goal_id = str(getattr(mission, "goal_id", "") or "").strip()
            if (
                mission_status == "ACTIVE"
                and issued_by == "operator"
                and mission_goal_id
            ):
                owner_external_goal_ids.add(mission_goal_id)
        candidates: list[tuple[Any, str]] = []
        for goal in goals:
            origin = str(
                getattr(
                    getattr(goal, "origin", None),
                    "value",
                    getattr(goal, "origin", ""),
                )
            )
            status = str(
                getattr(
                    getattr(goal, "status", None),
                    "value",
                    getattr(goal, "status", ""),
                )
            )
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            context_origin = ""
            if goal_id in owner_external_goal_ids and origin in {"USER", "EXTERNAL_MISSION"}:
                # Agent Runtime represents an external mission's backing Goal as
                # USER-origin. Durable owner proposal binding is the stronger
                # application-level fact for content routing.
                context_origin = "EXTERNAL_MISSION"
            elif origin == "USER":
                context_origin = "USER"
            if context_origin and status == "ACTIVE":
                candidates.append((goal, context_origin))
        if not candidates:
            return None
        preferred_ids = {
            str(ref).strip()
            for ref in relevant_refs
            if str(ref).strip()
            and not str(ref).startswith("cognition-request:")
        }
        if preferred_ids:
            preferred = [
                item
                for item in candidates
                if str(getattr(item[0], "goal_id", "") or "").strip()
                in preferred_ids
            ]
            if preferred:
                candidates = preferred

        episode = getattr(self, "owner_agent_episode", None)
        projection_for = getattr(episode, "projection_for", None)
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

        scored: list[dict[str, Any]] = []
        non_pending: list[dict[str, Any]] = []
        for goal, origin in candidates:
            goal_id = str(getattr(goal, "goal_id", "") or "").strip()
            priority = float(getattr(goal, "priority", 0.0) or 0.0)
            state = (
                projection_for(goal_id)
                if goal_id and callable(projection_for)
                else {}
            )
            updated_raw = str(
                state.get("updated_at")
                or state.get("completed_at")
                or state.get("created_at")
                or ""
            )
            try:
                updated_at = datetime.fromisoformat(
                    updated_raw.replace("Z", "+00:00")
                )
                if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                    raise ValueError
                age_seconds = max(
                    0.0,
                    (stamp - updated_at.astimezone(timezone.utc)).total_seconds(),
                )
            except ValueError:
                # Never-run goals must get a turn before already-active goals.
                age_seconds = 10**9
            row = {
                "goal_id": goal_id,
                "priority": priority,
                "age_seconds": age_seconds,
                "created_at": str(getattr(goal, "created_at", "") or ""),
                "goal": goal,
                "origin": origin,
            }
            scored.append(row)
            pending = bool(
                str(state.get("status") or "") == "RUNNING"
                and str(state.get("pending_request_id") or "").strip()
            )
            if not pending:
                non_pending.append(row)
        pool = non_pending or scored
        scheduler_receipt: dict[str, Any] | None = None
        if preferred_ids:
            # Explicit owner wake references are authority, not a policy trial.
            pool.sort(
                key=lambda row: (
                    float(row["age_seconds"])
                    + max(0.0, min(1.0, float(row["priority"]))) * 300.0,
                    float(row["priority"]),
                    str(row["created_at"]),
                ),
                reverse=True,
            )
            chosen = pool[0]
        else:
            scheduler = getattr(self, "goal_scheduler_evolution", None)
            if scheduler is not None:
                scheduler_receipt = scheduler.select(
                    [
                        {
                            "goal_id": row["goal_id"],
                            "priority": row["priority"],
                            "age_seconds": row["age_seconds"],
                            "created_at": row["created_at"],
                        }
                        for row in pool
                    ],
                    now=stamp,
                )
                selected_goal_id = str(scheduler_receipt.get("goal_id") or "")
                chosen = next(
                    row for row in pool
                    if str(row["goal_id"]) == selected_goal_id
                )
            else:
                pool.sort(
                    key=lambda row: (
                        float(row["age_seconds"])
                        + max(0.0, min(1.0, float(row["priority"]))) * 300.0,
                        float(row["priority"]),
                        str(row["created_at"]),
                    ),
                    reverse=True,
                )
                chosen = pool[0]
        goal = chosen["goal"]
        origin = str(chosen["origin"])
        parent_description = str(
            getattr(goal, "description", "") or ""
        ).strip()
        goal_id = str(getattr(goal, "goal_id", "") or "").strip()
        proposal = owner_proposals.get(goal_id) or {}
        proposal_description = str(
            proposal.get("description") or ""
        ).strip()
        description = proposal_description or parent_description
        if not goal_id or not description:
            return None
        return {
            "goal_id": goal_id,
            "origin": origin,
            "description": description,
            "parent_goal_description": parent_description,
            "owner_proposal_id": str(
                proposal.get("proposal_id") or ""
            ) or None,
            "owner_commitment_kind": str(
                proposal.get("kind") or ""
            ) or None,
            "owner_commitment_reason": str(
                proposal.get("reason") or ""
            ) or None,
            "reason": str(getattr(goal, "reason", "") or ""),
            "priority": float(getattr(goal, "priority", 0.0) or 0.0),
            "created_at": str(getattr(goal, "created_at", "") or ""),
            "selection_authority": (
                "EXACT_OWNER_GOAL_REF"
                if goal_id in preferred_ids
                else "FAIR_OWNER_GOAL_SCHEDULER"
            ),
            "goal_scheduler_decision_id": (
                str(scheduler_receipt.get("decision_id") or "")
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_arm": (
                str(scheduler_receipt.get("arm") or "")
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_priority_bonus_seconds": (
                float(scheduler_receipt.get("priority_bonus_seconds"))
                if scheduler_receipt is not None
                else None
            ),
            "goal_scheduler_awe_experiment_id": (
                (str(scheduler_receipt.get("awe_experiment_id") or "") or None)
                if scheduler_receipt is not None
                else None
            ),
        }

    def _owner_agent_mission_context(
        self,
        owner_goal: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        goal_id = str(owner_goal.get("goal_id") or "")
        for mission in list(getattr(runtime, "external_missions", ()) or ()):
            if str(getattr(mission, "goal_id", "") or "") != goal_id:
                continue
            status = str(
                getattr(
                    getattr(mission, "status", None),
                    "value",
                    getattr(mission, "status", ""),
                )
            )
            if status != "ACTIVE":
                continue
            current = trajectory = pressure = None
            controller = getattr(runtime, "missions", None)
            if controller is not None:
                try:
                    progress = controller.progress(
                        str(getattr(mission, "mission_id")),
                        now,
                    )
                    current = getattr(progress, "current_value", None)
                    trajectory = getattr(progress, "trajectory", None)
                    pressure = getattr(progress, "pressure", None)
                except Exception:
                    pass
            roadmap_context = None
            if controller is not None:
                latest_roadmap = getattr(controller, "latest_roadmap", None)
                if callable(latest_roadmap):
                    try:
                        roadmap = latest_roadmap(
                            str(getattr(mission, "mission_id"))
                        )
                    except Exception:
                        roadmap = None
                    if roadmap is not None:
                        schedule_by_step: dict[str, Any] = {}
                        portfolio_schedule = getattr(
                            controller, "portfolio_schedule", None
                        )
                        if callable(portfolio_schedule):
                            try:
                                schedule = portfolio_schedule(
                                    now=now,
                                    max_parallelism=2,
                                )
                                schedule_by_step = {
                                    str(item.step_id): item
                                    for item in list(
                                        getattr(schedule, "items", ()) or ()
                                    )
                                    if str(
                                        getattr(item, "mission_id", "") or ""
                                    )
                                    == str(getattr(mission, "mission_id"))
                                }
                            except Exception:
                                schedule_by_step = {}
                        roadmap_context = {
                            "roadmap_id": str(
                                getattr(roadmap, "roadmap_id", "") or ""
                            ),
                            "version": int(
                                getattr(roadmap, "version", 0) or 0
                            ),
                            "rationale": str(
                                getattr(roadmap, "rationale", "") or ""
                            )[:800],
                            "steps": [
                                {
                                    "step_id": str(
                                        getattr(step, "step_id", "") or ""
                                    ),
                                    "title": str(
                                        getattr(step, "title", "") or ""
                                    )[:240],
                                    "objective": str(
                                        getattr(step, "objective", "") or ""
                                    )[:500],
                                    "depends_on": list(
                                        getattr(
                                            step, "depends_on_step_ids", ()
                                        )
                                        or ()
                                    )[:8],
                                    "status": str(
                                        getattr(
                                            getattr(
                                                schedule_by_step.get(
                                                    str(
                                                        getattr(
                                                            step,
                                                            "step_id",
                                                            "",
                                                        )
                                                        or ""
                                                    )
                                                ),
                                                "status",
                                                "",
                                            ),
                                            "value",
                                            getattr(
                                                schedule_by_step.get(
                                                    str(
                                                        getattr(
                                                            step,
                                                            "step_id",
                                                            "",
                                                        )
                                                        or ""
                                                    )
                                                ),
                                                "status",
                                                "",
                                            ),
                                        )
                                        or (
                                            "COMPLETED"
                                            if getattr(step, "completed_at", None)
                                            else "CANCELLED"
                                            if getattr(step, "cancelled_at", None)
                                            else "RUNNING"
                                            if getattr(step, "started_at", None)
                                            else "PLANNED"
                                        )
                                    ),
                                }
                                for step in list(
                                    getattr(roadmap, "steps", ()) or ()
                                )[:16]
                            ],
                        }
            return {
                "mission_id": str(getattr(mission, "mission_id", "") or ""),
                "goal_id": goal_id,
                "description": str(getattr(mission, "description", "") or "")[:1200],
                "metric": str(getattr(mission, "metric", "") or ""),
                "baseline": getattr(mission, "baseline", None),
                "target": getattr(mission, "target", None),
                "current": current,
                "trajectory": trajectory,
                "pressure": pressure,
                "deadline": (
                    getattr(mission, "deadline").isoformat()
                    if hasattr(getattr(mission, "deadline", None), "isoformat")
                    else None
                ),
                "constraints": [
                    str(item)[:500]
                    for item in list(getattr(mission, "constraints", ()) or ())[:8]
                ],
                "roadmap": roadmap_context,
            }
        return {}

    def _owner_agent_control(
        self,
        *,
        owner_goal: dict[str, Any],
        now: datetime,
        resume_request_id: str | None = None,
    ) -> dict[str, Any]:
        episode = getattr(self, "owner_agent_episode", None)
        domain_runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        if episode is None or domain_runtime is None:
            return {"status": "UNAVAILABLE"}
        goal_id = str(owner_goal.get("goal_id") or "").strip()
        active_blocker = episode.cognition_blocker_for(
            goal_id,
            now=now,
        )
        if active_blocker is not None:
            return {
                "status": "OWNER_COGNITION_PROVIDER_UNAVAILABLE",
                "goal_id": goal_id,
                **active_blocker,
            }
        mission_context = self._owner_agent_mission_context(owner_goal, now)
        cognition_runtime = getattr(self, "owner_cognition_runtime", None)
        if cognition_runtime is None:
            from .runtime_backend import load_owner_cognition_runtime

            cognition_runtime = load_owner_cognition_runtime(
                state_root=Path(self.state_root)
                if getattr(self, "state_root", None) is not None
                else None,
                behavioral_context_provider=self._owner_behavioral_context,
            )
            self.owner_cognition_runtime = cognition_runtime

        async def execute_tool(decision: dict[str, Any]) -> dict[str, Any]:
            action = str(decision.get("action") or "")
            if action == "INSPECT_MISSION":
                return {
                    "status": "OK",
                    "mission": mission_context,
                    "owner_goal": owner_goal,
                }
            if action == "SEARCH_WORLD":
                query = str(decision.get("query") or "").strip()
                feed = getattr(
                    getattr(self, "continuous_loop", None),
                    "world_feed",
                    None,
                )
                searcher = getattr(feed, "web_searcher", None)
                if not query or not callable(searcher):
                    return {"status": "UNAVAILABLE", "query": query}
                try:
                    found = await searcher(query, now)
                except Exception as exc:
                    return {
                        "status": "ERROR",
                        "query": query,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:300],
                    }
                rows = []
                for item in list(found or ())[:8]:
                    if not isinstance(item, dict):
                        continue
                    row = {
                        key: item.get(key)
                        for key in (
                            "evidence_id", "source_kind", "source", "title",
                            "summary", "url", "published_at", "observed_at",
                            "source_revision", "evidence_refs", "publisher_feed",
                            "search_query", "search_rank", "learning_kind",
                        )
                        if item.get(key) is not None
                    }
                    if not str(row.get("evidence_id") or "").strip():
                        row["evidence_id"] = "owner-search:" + sha256(
                            json.dumps(
                                {
                                    "url": row.get("url"),
                                    "title": row.get("title"),
                                    "summary": row.get("summary"),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()[:32]
                    row.setdefault("source_kind", "web-search")
                    row.setdefault(
                        "source_revision",
                        str(item.get("source_revision") or row["evidence_id"]),
                    )
                    row.setdefault("evidence_refs", [row["evidence_id"]])
                    rows.append(row)
                return {"status": "OK", "query": query, "items": rows}
            if action == "CHECK_X":
                loop = getattr(self, "x_loop", None)
                if loop is None:
                    return {"status": "DISABLED"}
                try:
                    result = await loop.observe(now=now, allow_remote_actions=False)
                    self.wakeful.emit_x_mentions(
                        count=int(result.get("new_mentions") or 0),
                        observed_at=now,
                    )
                    return {"status": "OK", "x_social_observation": result}
                except Exception as exc:
                    return {"status": "DEGRADED", **self._error(exc)}
            if action == "CHECK_OUTCOMES":
                return self._outcomes(now)
            return {"status": "UNSUPPORTED", "action": action}

        from .cognition_context import owner_cognition_scope

        with owner_cognition_scope({
            "goal_id": str(owner_goal.get("goal_id") or ""),
            "mission_id": str(mission_context.get("mission_id") or ""),
            "description": str(owner_goal.get("description") or ""),
        }):
            try:
                return self._run_async(
                    episode.run(
                        runtime=cognition_runtime,
                        owner_goal=owner_goal,
                        mission_context=mission_context,
                        tool_executor=execute_tool,
                        now=now,
                        resume_request_id=resume_request_id,
                    )
                )
            except Exception as exc:
                from .openai_public_chat import OpenAIPublicChatError

                if not isinstance(exc, OpenAIPublicChatError):
                    raise
                retry_seconds = 300 if "HTTP_429" in str(exc) else 120
                blocked = episode.mark_cognition_blocked(
                    goal_id=goal_id,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retry_at=now + timedelta(seconds=retry_seconds),
                    now=now,
                )
                schedule = getattr(self.wakeful, "schedule_goal_followup", None)
                if callable(schedule):
                    schedule(
                        now,
                        seconds=retry_seconds,
                        relevant_refs=(goal_id,),
                    )
                return {
                    **blocked,
                    "goal_id": goal_id,
                    "retry_seconds": retry_seconds,
                }

    @staticmethod
    def _owner_agent_seed_evidence(control: dict[str, Any]) -> list[dict[str, Any]]:
        """Carry read-only evidence gathered by the controller into its executor."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for step in list(control.get("steps") or ()):
            if not isinstance(step, dict):
                continue
            decision = step.get("decision")
            result = step.get("result")
            if (
                not isinstance(decision, dict)
                or str(decision.get("action") or "") != "SEARCH_WORLD"
                or not isinstance(result, dict)
            ):
                continue
            for item in list(result.get("items") or ())[:8]:
                if not isinstance(item, dict):
                    continue
                evidence_id = str(item.get("evidence_id") or "").strip()
                title = str(item.get("title") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if not evidence_id or not title or not summary or evidence_id in seen:
                    continue
                seen.add(evidence_id)
                rows.append(dict(item))
        return rows

    def _content(
        self,
        now: datetime,
        *,
        focus_topic: str | None,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        result, rows = self._runtime_tick(now)
        rows.update({"task": "CONTENT_COGNITION", "status": "OK" if result is not None else "DEGRADED"})
        if result is None or not result.generation_ready:
            return rows
        # Finish already-grounded external X work before asking the owner
        # controller what to do next. Otherwise a fresh owner cognition can
        # perpetually preempt a mission expression that is one downstream
        # action away from changing the world.
        if self.continuous_loop is not None:
            self._recover_social_stage_backlog(
                rows=rows,
                now=now,
                x_mission_only=True,
            )
        owner_goal = self._active_owner_goal_context(
            relevant_refs=relevant_refs,
            now=now,
        )
        owner_directed_cycle_id: str | None = None
        owner_episode_active = False
        owner_seed_evidence: list[dict[str, Any]] = []
        has_durable_resume = any(
            str(ref).startswith("cognition-request:")
            for ref in relevant_refs
        )
        owner_resume_request_id = next(
            (
                str(ref).split(":", 1)[1]
                for ref in reversed(relevant_refs)
                if str(ref).startswith("cognition-request:")
                and str(ref).split(":", 1)[1].strip()
            ),
            None,
        )
        owner_episode_running = bool(
            owner_goal is not None
            and getattr(self, "owner_agent_episode", None) is not None
            and self.owner_agent_episode.running_for(
                str(owner_goal.get("goal_id") or "")
            )
        )
        if (
            owner_goal is not None
            and focus_topic is None
            and (not has_durable_resume or owner_episode_running)
        ):
            control = self._owner_agent_control(
                owner_goal=owner_goal,
                now=now,
                resume_request_id=owner_resume_request_id,
            )
            rows["owner_agent_episode"] = control
            if (
                str(control.get("status") or "")
                == "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
            ):
                rows["status"] = "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
                rows["owner_cognition_blocker"] = control
                return rows
            if str(control.get("status") or "") == "UNRESOLVED_ACTION_REQUIRED":
                rows["status"] = "OWNER_ACTION_REQUIRED_BUT_UNRESOLVED"
                return rows
            if str(control.get("status") or "") == "DECIDED":
                decision = (
                    control.get("decision")
                    if isinstance(control.get("decision"), dict)
                    else {}
                )
                action = str(decision.get("action") or "")
                owner_episode_active = True
                owner_directed_cycle_id = str(
                    control.get("cycle_id") or ""
                ).strip() or None
                if action == "WAIT":
                    wait_seconds = int(decision.get("wait_seconds") or 300)
                    schedule_wait = getattr(self.wakeful, "schedule_owner_wait", None)
                    if callable(schedule_wait):
                        rows["owner_wait"] = schedule_wait(
                            now,
                            seconds=max(60, min(wait_seconds, 21600)),
                            relevant_refs=(str(owner_goal.get("goal_id") or ""),),
                        )
                    else:
                        schedule = getattr(self.wakeful, "schedule_goal_followup", None)
                        if callable(schedule):
                            schedule(
                                now,
                                seconds=max(60, min(wait_seconds, 21600)),
                                relevant_refs=(str(owner_goal.get("goal_id") or ""),),
                            )
                    wait_outcome = {
                        "status": "WAIT_SCHEDULED",
                        "wait_seconds": wait_seconds,
                    }
                    complete = getattr(self.owner_agent_episode, "complete", None)
                    if callable(complete):
                        rows["owner_agent_episode_completion"] = complete(
                            outcome=wait_outcome,
                            now=now,
                            goal_id=str(owner_goal.get("goal_id") or ""),
                        )
                    rows["status"] = "OWNER_AGENT_WAITING"
                    return rows
                if action == "RUN_CONTENT_PIPELINE":
                    focus_topic = str(decision.get("topic") or "").strip() or None
                    owner_seed_evidence = self._owner_agent_seed_evidence(control)
        if self.web_worker is not None:
            try:
                rows["web_editorial"] = self._run_async(self.web_worker.run_once(now=now))
            except Exception as exc:  # noqa: BLE001
                rows["web_editorial"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.continuous_loop is None:
            return rows
        if focus_topic is None:
            self._recover_social_stage_backlog(
                rows=rows,
                now=now,
            )
        # Strategy selection consumes traffic context, so refresh GSC immediately before it.
        # This preserves the pre-Wakeful ordering contract without restoring a monolithic poll loop.
        if self.gsc_collector is not None:
            try:
                rows["google_search_console"] = self.gsc_collector.collect(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["google_search_console"] = self._error(exc)
                rows["status"] = "DEGRADED"
        pending_chatgpt = (
            self._pending_chatgpt_cycle()
            if focus_topic is None and not owner_episode_active
            else None
        )
        focus_cycle_id = (
            owner_directed_cycle_id
            if owner_episode_active and focus_topic
            else f"v2-bootstrap-{int(now.timestamp())}" if focus_topic else None
        )
        directed_cycle_id = (
            str(pending_chatgpt["cycle"])
            if pending_chatgpt is not None
            else focus_cycle_id
        )
        preflight = self.continuous_loop.preflight(now=now, cycle_id=directed_cycle_id)
        base_cycle_id = str(preflight["cycle"])
        recovery = None
        mission_followup = None
        if pending_chatgpt is not None:
            rows["content_chatgpt_resume"] = {
                "status": "RESUMING",
                "cycle": str(pending_chatgpt["cycle"]),
                "request_id": str(pending_chatgpt["request_id"]),
            }
            cycle_folder = (
                Path(self.state_root)
                / "content"
                / "continuous"
                / "cycles"
                / str(pending_chatgpt["cycle"])
            )
            for filename, target in (
                ("recovery.json", "recovery"),
                ("mission_followup.json", "mission_followup"),
            ):
                path = cycle_folder / filename
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("status") or "").upper() != "READY":
                    continue
                if target == "recovery":
                    recovery = payload
                    rows["content_cycle_recovery"] = payload
                else:
                    mission_followup = payload
                    rows["content_mission_followup"] = payload
        elif focus_topic is None:
            recovery = self._content_recovery(preflight=preflight, now=now)
            if recovery is not None:
                rows["content_cycle_recovery"] = recovery
                if recovery["status"] == "RECOVERY_LIMIT_REACHED":
                    rows["resource_decision"] = {
                        "status": "SKIPPED_NOOP",
                        "decision": "NO_RESOURCE_ACTION",
                        "cycle": base_cycle_id,
                    }
                    rows["continuous_content"] = {
                        "status": recovery["status"],
                        "cycle": base_cycle_id,
                        "recovery_cycle": recovery.get("recovery_cycle"),
                        "previous_status": recovery.get("previous_status"),
                        "attempt_count": recovery.get("attempt_count"),
                    }
                    return rows
                if recovery["status"] == "ALREADY_RECOVERED":
                    recovered_cycle = str(recovery.get("recovery_cycle") or "")
                    recovered_preflight = self.continuous_loop.preflight(
                        now=now,
                        cycle_id=recovered_cycle,
                    )
                    mission_followup = self._content_mission_followup(
                        preflight=recovered_preflight,
                        now=now,
                    )
                    if mission_followup is None:
                        rows["resource_decision"] = {
                            "status": "SKIPPED_NOOP",
                            "decision": "NO_RESOURCE_ACTION",
                            "cycle": base_cycle_id,
                        }
                        rows["continuous_content"] = {
                            "status": recovery["status"],
                            "cycle": base_cycle_id,
                            "recovery_cycle": recovery.get("recovery_cycle"),
                            "previous_status": recovery.get("previous_status"),
                            "attempt_count": recovery.get("attempt_count"),
                        }
                        return rows
                elif recovery["status"] == "READY":
                    directed_cycle_id = str(recovery["recovery_cycle"])
                    preflight = self.continuous_loop.preflight(
                        now=now, cycle_id=directed_cycle_id
                    )
            else:
                mission_followup = self._content_mission_followup(
                    preflight=preflight,
                    now=now,
                )

            if mission_followup is not None:
                rows["content_mission_followup"] = mission_followup
                if mission_followup["status"] in {
                    "ALREADY_FOLLOWED_UP",
                    "FOLLOWUP_LIMIT_REACHED",
                }:
                    rows["resource_decision"] = {
                        "status": "SKIPPED_NOOP",
                        "decision": "NO_RESOURCE_ACTION",
                        "cycle": base_cycle_id,
                    }
                    rows["continuous_content"] = {
                        "status": mission_followup["status"],
                        "cycle": base_cycle_id,
                        "followup_cycle": mission_followup.get("followup_cycle"),
                        "followup_of": mission_followup.get("followup_of"),
                        "attempt_count": mission_followup.get("attempt_count"),
                        "result_status": mission_followup.get("result_status"),
                    }
                    return rows
                directed_cycle_id = str(mission_followup["followup_cycle"])
                preflight = self.continuous_loop.preflight(
                    now=now,
                    cycle_id=directed_cycle_id,
                )
        cycle_id = str(preflight["cycle"])
        if preflight["status"] == "ALREADY_PROCESSED":
            decision = {"status": "SKIPPED_NOOP", "decision": "NO_RESOURCE_ACTION", "cycle": cycle_id}
        else:
            try:
                decision = self.resource_economy.begin_cycle(now=now, cycle=cycle_id)
            except Exception as exc:  # noqa: BLE001
                decision = {"status": "UNAVAILABLE", "decision": "ASK_HUMAN", **self._error(exc)}
        rows["resource_decision"] = decision
        if decision.get("decision") not in {"EXECUTE", "NO_RESOURCE_ACTION"}:
            rows["continuous_content"] = {"status": "RESOURCE_DEFERRED", "cycle": cycle_id, "resource_decision": decision}
            rows["status"] = "DEFERRED"
            return rows
        if owner_goal is not None:
            rows["owner_goal_context"] = {
                "goal_id": owner_goal["goal_id"],
                "origin": owner_goal["origin"],
                "priority": owner_goal["priority"],
            }
        owner_content_cognition = None
        if owner_goal is not None:
            owner_content_cognition = getattr(
                self,
                "owner_cognition_runtime",
                None,
            )
            if owner_content_cognition is None:
                from .runtime_backend import load_owner_cognition_runtime

                owner_content_cognition = load_owner_cognition_runtime(
                    state_root=Path(self.state_root)
                    if getattr(self, "state_root", None) is not None
                    else None,
                    behavioral_context_provider=self._owner_behavioral_context,
                )
                self.owner_cognition_runtime = owner_content_cognition
        content_run_kwargs = dict(
            now=now,
            cycle_id=directed_cycle_id,
            focus_topic=focus_topic,
            owner_goal=owner_goal,
            seed_evidence=owner_seed_evidence,
        )
        if owner_goal is not None:
            content_run_kwargs["cognition_runtime"] = owner_content_cognition
        continuous = self._run_async(
            self.continuous_loop.run_once(**content_run_kwargs)
        )
        rows["continuous_content"] = continuous
        if str(continuous.get("status") or "").upper() == "WAITING_FOR_CHATGPT":
            rows["status"] = "WAITING_FOR_CHATGPT"
            request_id = str(continuous.get("request_id") or "").strip()
            resume_refs = tuple(relevant_refs)
            owner_goal_id = (
                str(owner_goal.get("goal_id") or "").strip()
                if isinstance(owner_goal, dict)
                else ""
            )
            if owner_goal_id:
                resume_refs = tuple(
                    dict.fromkeys((*resume_refs, owner_goal_id))
                )
            schedule_resume = getattr(
                self.wakeful, "schedule_chatgpt_resume", None
            )
            if request_id and callable(schedule_resume):
                try:
                    rows["chatgpt_resume"] = schedule_resume(
                        now,
                        request_id=request_id,
                        task="CONTENT_COGNITION",
                        relevant_refs=resume_refs,
                    )
                except Exception as exc:  # noqa: BLE001
                    rows["chatgpt_resume"] = self._error(exc)
            return rows
        if recovery is not None and recovery.get("status") == "READY":
            recovery_path = (
                Path(self.state_root)
                / "content"
                / "continuous"
                / "cycles"
                / str(recovery["recovery_cycle"])
                / "recovery.json"
            )
            finished = {
                **recovery,
                "status": (
                    "FAILED"
                    if str(continuous.get("status") or "").upper()
                    in {"ERROR", "DEGRADED"}
                    else "COMPLETED"
                ),
                "result_status": str(continuous.get("status") or "UNKNOWN"),
                "completed_at": datetime.now(now.tzinfo).isoformat(),
            }
            tmp = recovery_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    finished,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(recovery_path)
            rows["content_cycle_recovery"] = finished
        if mission_followup is not None and mission_followup.get("status") == "READY":
            followup_path = (
                Path(self.state_root)
                / "content"
                / "continuous"
                / "cycles"
                / str(mission_followup["followup_cycle"])
                / "mission_followup.json"
            )
            result_status = str(continuous.get("status") or "UNKNOWN").upper()
            if result_status in {"ERROR", "DEGRADED"}:
                followup_status = "FAILED"
            elif result_status == "COGNITION_ONLY":
                followup_status = "NO_MATERIAL_CHANGE"
            else:
                followup_status = "COMPLETED"
            finished_followup = {
                **mission_followup,
                "status": followup_status,
                "result_status": str(continuous.get("status") or "UNKNOWN"),
                "completed_at": datetime.now(now.tzinfo).isoformat(),
            }
            tmp = followup_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    finished_followup,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(followup_path)
            rows["content_mission_followup"] = finished_followup
        action_prediction = continuous.get("action_prediction") if isinstance(continuous, dict) else None
        prediction_id = str((action_prediction or {}).get("prediction_id") or "")
        if prediction_id:
            try:
                rows["neural_action_commitment"] = self.wakeful.emit_action_commitment(
                    prediction_id=prediction_id, observed_at=now,
                )
            except Exception as exc:  # noqa: BLE001
                rows["neural_action_commitment"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if decision.get("decision") == "EXECUTE":
            try:
                rows["resource_cycle_result"] = self.resource_economy.end_cycle(
                    now=datetime.now(now.tzinfo), cycle=cycle_id,
                    episode_id=str(decision["episode_id"]), before_balance=float(decision["before_balance"]),
                    result=continuous,
                )
            except Exception as exc:  # noqa: BLE001
                rows["resource_cycle_result"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if continuous.get("status") in {"ERROR", "RESOURCE_DEFERRED", "ALREADY_PROCESSED"}:
            if continuous.get("status") == "ERROR":
                rows["status"] = "DEGRADED"
                error_type = str(continuous.get("error_type") or "")
                if error_type == "LLMProviderError":
                    schedule_provider_recovery = getattr(
                        self.wakeful, "schedule_provider_recovery", None
                    )
                    if callable(schedule_provider_recovery):
                        try:
                            rows["provider_recovery"] = schedule_provider_recovery(now)
                        except Exception as exc:  # noqa: BLE001
                            rows["provider_recovery"] = self._error(exc)
                else:
                    state_root = getattr(self, "state_root", None)
                    cycle = str(continuous.get("cycle") or "").strip()
                    if state_root is not None and cycle:
                        result_path = (
                            Path(state_root)
                            / "content"
                            / "continuous"
                            / "cycles"
                            / cycle
                            / "result.json"
                        )
                        followup_identity = self._mission_followup_identity(
                            prior=continuous,
                            result_path=result_path,
                        )
                        if (
                            followup_identity is not None
                            and str(followup_identity.get("reason") or "")
                            == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
                        ):
                            schedule = getattr(
                                self.wakeful, "schedule_goal_followup", None
                            )
                            if callable(schedule):
                                owner_goal_id = str(
                                    continuous.get("owner_goal_id") or ""
                                ).strip()
                                schedule(
                                    now,
                                    seconds=5,
                                    relevant_refs=(
                                        (owner_goal_id,)
                                        if owner_goal_id
                                        else ()
                                    ),
                                )
                                rows["mission_followup_schedule"] = {
                                    "status": "SCHEDULED",
                                    "seconds": 5,
                                    "goal_id": str(
                                        followup_identity.get("goal_id") or ""
                                    ),
                                    "mission_id": str(
                                        followup_identity.get("mission_id") or ""
                                    ),
                                    "reason": str(
                                        followup_identity.get("reason") or ""
                                    ),
                                }
                self_maintenance = getattr(self, "self_maintenance", None)
                if self_maintenance is not None:
                    try:
                        rows["self_maintenance"] = self_maintenance.ingest_failure(continuous, now=now)
                        schedule = getattr(self.wakeful, "schedule_self_maintenance", None)
                        if callable(schedule):
                            schedule(now, seconds=60)
                    except Exception as exc:  # noqa: BLE001
                        rows["self_maintenance"] = self._error(exc)
            elif continuous.get("status") == "ALREADY_PROCESSED":
                result_path_raw = str(preflight.get("result_path") or "").strip()
                if result_path_raw:
                    try:
                        prior = json.loads(
                            Path(result_path_raw).read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, json.JSONDecodeError):
                        prior = {}
                    if str(prior.get("status") or "").upper() == "PUBLISHED":
                        self._run_social_stage(
                            rows=rows,
                            continuous=prior,
                            now=now,
                        )
                        rows["social_stage_replay"] = {
                            "status": "REPLAYED",
                            "cycle": str(prior.get("cycle") or cycle_id),
                            "reason": "PUBLISHED_CYCLE_DOWNSTREAM_RECOVERY",
                        }
                        self.wakeful.mark_cognition(now)
            if owner_episode_active:
                complete = getattr(self.owner_agent_episode, "complete", None)
                if callable(complete):
                    rows["owner_agent_episode_completion"] = complete(
                        outcome=continuous,
                        now=now,
                        goal_id=(
                            str(owner_goal.get("goal_id") or "")
                            if isinstance(owner_goal, dict)
                            else None
                        ),
                    )
            return rows
        self._run_social_stage(rows=rows, continuous=continuous, now=now)
        followup_identity = None
        state_root = getattr(self, "state_root", None)
        cycle = str(continuous.get("cycle") or "").strip()
        if state_root is not None and cycle:
            followup_identity = self._mission_followup_identity(
                prior=continuous,
                result_path=(
                    Path(state_root)
                    / "content"
                    / "continuous"
                    / "cycles"
                    / cycle
                    / "result.json"
                ),
            )
        continuous_status = str(continuous.get("status") or "").upper()
        followup_reason = (
            str(followup_identity.get("reason") or "")
            if followup_identity is not None
            else ""
        )
        should_schedule_followup = (
            followup_identity is not None
            and (
                continuous_status == "COGNITION_ONLY"
                or followup_reason == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
            )
        )
        if should_schedule_followup:
            schedule = getattr(self.wakeful, "schedule_goal_followup", None)
            if callable(schedule):
                owner_goal_id = str(
                    continuous.get("owner_goal_id") or ""
                ).strip()
                schedule(
                    now,
                    seconds=5,
                    relevant_refs=(
                        (owner_goal_id,)
                        if owner_goal_id
                        else ()
                    ),
                )
                rows["mission_followup_schedule"] = {
                    "status": "SCHEDULED",
                    "seconds": 5,
                    "goal_id": str(followup_identity.get("goal_id") or ""),
                    "mission_id": str(followup_identity.get("mission_id") or ""),
                    "reason": str(
                        followup_identity.get("reason")
                        or "EXTERNAL_MISSION_NO_MATERIAL_CHANGE"
                    ),
                }
        if owner_episode_active:
            complete = getattr(self.owner_agent_episode, "complete", None)
            if callable(complete):
                rows["owner_agent_episode_completion"] = complete(
                    outcome=continuous,
                    now=now,
                    goal_id=(
                        str(owner_goal.get("goal_id") or "")
                        if isinstance(owner_goal, dict)
                        else None
                    ),
                )
        self.wakeful.mark_cognition(now)
        return rows

    @staticmethod
    def _error(exc: Exception, **extra: Any) -> dict[str, Any]:
        return {"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:500], **extra}


__all__ = ["WakefulContentTaskRunner"]
