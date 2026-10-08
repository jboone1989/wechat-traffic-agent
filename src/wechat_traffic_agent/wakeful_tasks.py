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


from .orchestration.owner import OwnerTaskMethods
from .orchestration.content import ContentTaskMethods
from .orchestration.social import SocialTaskMethods
from .orchestration.neural import NeuralTaskMethods
from .orchestration.resources import ResourceTaskMethods
from .orchestration.observation import ObservationTaskMethods

class WakefulContentTaskRunner(OwnerTaskMethods, ContentTaskMethods, SocialTaskMethods, NeuralTaskMethods, ResourceTaskMethods, ObservationTaskMethods):
    MAX_OWNER_PREDICTION_DEFER = timedelta(hours=1)

    def __init__(self, **components: Any) -> None:
        self.__dict__.update(components)
        # wakeful is an optional scheduling/signal hook for unit-sized task
        # runners. Keep the attribute explicit so absence does not turn an
        # otherwise valid interoception result into AttributeError.
        self.wakeful = components.get("wakeful")
        self.stop_requested = components.get("stop_requested") or (lambda: False)
        state_root = components.get("state_root")
        if state_root is not None and getattr(self, "state_events", None) is None:
            from .state_events import FerroStateClient, FerroStateEventBus

            self.state_events = FerroStateEventBus(Path(state_root))
            self.state_client = FerroStateClient(Path(state_root))
        if state_root is not None and getattr(self, "task_scheduler", None) is None:
            from .task_scheduler import IndependentTaskScheduler

            self.task_scheduler = IndependentTaskScheduler(Path(state_root))
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
            try:
                rows["interoception"]["repair_wake"] = {
                    **self.task_scheduler.schedule(
                        "SELF_REPAIR",
                        now,
                        reason="diagnose recorded task failure",
                    ),
                    "problem_id": maintenance_observation.get("problem_id"),
                }
            except Exception as exc:  # noqa: BLE001
                rows["interoception"]["repair_wake"] = self._error(exc)

        if observation.get("abnormal"):
            try:
                rows["interoception"]["neural_signal"] = self.state_events.emit(
                    "INTEROCEPTIVE_FAULT",
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
            try:
                episode_id = str(observation.get("episode_id") or "")
                rows["interoception"]["diagnostic_wake"] = self.task_scheduler.schedule(
                    "REFLECT",
                    now + timedelta(seconds=15),
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
            try:
                receipt["interoception"]["neural_signal"] = self.state_events.emit(
                    "INTEROCEPTIVE_FAULT",
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
            try:
                episode_id = str(observation.get("episode_id") or "")
                receipt["diagnostic_wake"] = self.task_scheduler.schedule(
                    "REFLECT",
                    now + timedelta(seconds=15),
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
        from .agent_runtime_chatgpt import AgentRuntimeChatGPTProviderError

        try:
            self._raise_if_stopping()
            if task == "SELF_REPAIR":
                return self._self_repair(now)
            regulator = getattr(self, "resource_regulator", None)
            if regulator is not None:
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
            if task == "FAST_DECISION":
                return self._fast_decision(
                    now,
                    relevant_refs=relevant_refs,
                )
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
            if task == "OKX_AI_MARKETPLACE":
                return self._okx_ai_marketplace(now)
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
        except AgentRuntimeChatGPTProviderError as exc:
            # A bounded ChatGPT dispatch/response timeout is an expected
            # provider-availability state, not a process-fatal error. Keep any
            # durable owner episode checkpoint intact and re-enter cognition
            # later instead of letting the exception escape to systemd.
            detail = str(exc)
            failure_code = (
                "CHATGPT_EPHEMERAL_NO_REPLY" if "CHATGPT_EPHEMERAL_NO_REPLY" in detail
                else "CHATGPT_REPLY_TIMEOUT" if "response exceeded" in detail
                else "CHATGPT_BROWSER_LOCK_TIMEOUT" if "profile lock exceeded" in detail
                else "CHATGPT_BROWSER_TARGET_LOST" if "No target with given id found" in detail
                else "CHATGPT_MODEL_TURN_UNRESOLVED" if "CARRIER_LOST_WITHOUT_DURABLE_CONVERSATION_URL" in detail
                else "CHATGPT_WEB_USAGE_LIMIT" if "CHATGPT_WEB_USAGE_LIMIT" in detail
                else "CHATGPT_BROWSER_TRANSPORT_ERROR" if "browser-harness command failed" in detail
                else "CHATGPT_PROVIDER_ERROR"
            )
            result = {
                "task": task,
                "status": "CHATGPT_UNAVAILABLE",
                "failure_code": failure_code,
                "retryable": True,
                "error_type": type(exc).__name__,
                "message": detail[:500],
            }
            try:
                result["provider_recovery"] = self.task_scheduler.schedule(
                    task,
                    now + timedelta(seconds=300),
                    reason=f"retry {task.lower()} after temporary LLM provider outage",
                    relevant_refs=tuple(ref for ref in relevant_refs),
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








































    @staticmethod
    def _error(exc: Exception, **extra: Any) -> dict[str, Any]:
        return {"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:500], **extra}


__all__ = ["WakefulContentTaskRunner"]
