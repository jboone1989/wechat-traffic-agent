"""Task-granular execution for the Content Agent Wakeful Core adopter."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any


class _ShutdownRequested(BaseException):
    """Internal cooperative-cancellation signal; bypass broad Exception handlers."""


class WakefulContentTaskRunner:
    def __init__(self, **components: Any) -> None:
        self.__dict__.update(components)
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

    def run(self, task: str, *, now: datetime, focus_topic: str | None = None) -> dict[str, Any]:
        try:
            self._raise_if_stopping()
            regulator = getattr(self, "resource_regulator", None)
            if regulator is not None:
                admission = regulator.claim(task, now)
                if not admission["allowed"]:
                    return {
                        "task": task, "status": "RESOURCE_DEFERRED",
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
                return self._reflect(now)
            if task == "CHECK_X":
                return self._x(now)
            if task == "CHECK_OUTCOMES":
                return self._outcomes(now)
            if task == "OBSERVE_WORLD":
                return self._observe(now)
            if task == "VERIFY_PREDICTION":
                return self._verify_prediction(now)
            if task == "CONTENT_COGNITION":
                return self._content(now, focus_topic=focus_topic)
            return {"status": "NOOP", "task": task}
        except _ShutdownRequested:
            return {
                "status": "SHUTDOWN_CHECKPOINT",
                "task": task,
                "reason": "SIGTERM_OR_SIGINT",
            }

    def _neural_interoception(self, now: datetime) -> dict[str, Any]:
        projection = self.wakeful.refresh_neural_life(now)
        return {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK" if projection.get("status") != "DISABLED" else "DISABLED",
            "neural_life": projection,
        }

    def _neural_play(self, now: datetime) -> dict[str, Any]:
        result = self.wakeful.run_brain_lab(now, steps=12)
        return {
            "task": "NEURAL_PLAY",
            **result,
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
        try:
            observation = self.resource_economy.observe(now)
            rows["resource_observation"] = observation
            projection = self.content_mind.public_projection().get("resource_awareness") or {}
            current = projection.get("current") or {}
            pressure = str(current.get("pressure") or "UNKNOWN")
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

        model_reputation = getattr(self, "model_reputation", None)
        if model_reputation is not None:
            try:
                rows["model_reputation"] = model_reputation.observe(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["model_reputation"] = self._error(exc)
                rows["status"] = "DEGRADED"
        nvidia_catalog = getattr(self, "nvidia_catalog", None)
        if nvidia_catalog is not None:
            try:
                rows["nvidia_capability_catalog"] = nvidia_catalog.observe(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["nvidia_capability_catalog"] = self._error(exc)
                rows["status"] = "DEGRADED"
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
            if ready is not None:
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
            if callable(probe_windfall):
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

            top = plans[0] if plans else None
            if (
                not autonomous_pending
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

    def _reflect(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "REFLECT", "status": "OK"}
        try:
            rows["daily_reflection"] = self.daily_reflection.maybe_generate(now)
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
        self_repair = getattr(self, "self_repair", None)
        if self_repair is not None:
            try:
                rows["self_repair"] = self_repair.run_once(now=now)
                repair_status = rows["self_repair"].get("status")
                schedule = getattr(self.wakeful, "schedule_self_maintenance", None)
                if repair_status == "GENERATED_REPAIR_PROPOSED" and callable(schedule):
                    schedule(now, seconds=60)
                elif repair_status in {
                    "CANDIDATE_REJECTED_REPLAN_QUEUED",
                    "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED",
                } and callable(schedule):
                    schedule(
                        now,
                        seconds=int(rows["self_repair"].get("retry_seconds") or 60),
                    )
                elif repair_status == "GENERATED_PLAN_DEFERRED" and callable(schedule):
                    schedule(
                        now,
                        seconds=int(rows["self_repair"].get("retry_seconds") or 900),
                    )
            except Exception as exc:  # noqa: BLE001
                rows["self_repair"] = self._error(exc)
                rows["status"] = "DEGRADED"
        try:
            rows["evolution_deliberation"] = self._evolution_deliberation(now)
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

        pending_windfall = self._load_windfall_pending()
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
        if state_root is not None:
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
        if result is None or not result.generation_ready or self.draft_worker is None:
            return rows
        try:
            draft = self._run_async(self.draft_worker.run_once(now=now))
            draft_payload = draft.to_payload()
            rows["draft_worker"] = draft_payload
            evidence_ref = str(
                draft_payload.get("artifact_id") or draft_payload.get("contract_id")
                or draft_payload.get("task_id") or f"trading-draft:{int(now.timestamp())}"
            )
            self.wakeful.emit_network_evidence(evidence_ref=evidence_ref, observed_at=now)
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
        try:
            ledger = getattr(self.continuous_loop, "predictions", None)
            if ledger is not None and callable(getattr(ledger, "due", None)):
                for prediction in ledger.due(now):
                    self.wakeful.emit_prediction_due(
                        prediction_ref=str(prediction.prediction_id), observed_at=now,
                    )
            result = self._run_async(self.continuous_loop.verify_due_predictions(now=now))
            return {"task": "VERIFY_PREDICTION", "status": "OK", "prediction_verification": result}
        except Exception as exc:  # noqa: BLE001
            return {"task": "VERIFY_PREDICTION", "status": "DEGRADED", "prediction_verification": self._error(exc)}

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
        completed: list[tuple[Path, str]] = []
        if cycles_root.exists():
            for folder in sorted(cycles_root.glob(prefix + "*")):
                result_file = folder / "result.json"
                if not result_file.exists():
                    continue
                try:
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                completed.append((folder, str(payload.get("status") or "").upper()))

        for folder, status in reversed(completed):
            if status not in retryable_statuses:
                return {
                    "status": "ALREADY_RECOVERED",
                    "recovery_of": base_cycle,
                    "recovery_cycle": folder.name,
                    "previous_status": prior_status,
                    "attempt_count": len(completed),
                }

        if len(completed) >= 3:
            return {
                "status": "RECOVERY_LIMIT_REACHED",
                "recovery_of": base_cycle,
                "previous_status": prior_status,
                "attempt_count": len(completed),
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
        for folder in folders:
            result_path = folder / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "").upper() != "PUBLISHED":
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
            return {
                "cycle": str(result.get("cycle") or folder.name),
                "result": result,
                "reason": (
                    "MISSING_SOCIAL_DRAFT"
                    if not social_path.exists()
                    else f"SOCIAL_DRAFT_{social_status or 'INVALID'}"
                ),
            }
        return None

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
                rows["x_social_expression"] = self._run_async(
                    x_loop.after_content_cycle(result=continuous, now=now)
                )
            except Exception as exc:  # noqa: BLE001
                rows["x_social_expression"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _active_owner_goal_context(self) -> dict[str, Any] | None:
        runtime = getattr(getattr(self, "app", None), "domain_runtime", None)
        goals = list(getattr(runtime, "active_goals", ()) or ())
        candidates: list[Any] = []
        for goal in goals:
            origin = getattr(getattr(goal, "origin", None), "value", getattr(goal, "origin", ""))
            status = getattr(getattr(goal, "status", None), "value", getattr(goal, "status", ""))
            if str(origin) == "USER" and str(status) == "ACTIVE":
                candidates.append(goal)
        if not candidates:
            return None
        candidates.sort(
            key=lambda goal: str(getattr(goal, "created_at", "") or ""),
            reverse=True,
        )
        goal = candidates[0]
        description = str(getattr(goal, "description", "") or "").strip()
        goal_id = str(getattr(goal, "goal_id", "") or "").strip()
        if not goal_id or not description:
            return None
        return {
            "goal_id": goal_id,
            "origin": "USER",
            "description": description,
            "reason": str(getattr(goal, "reason", "") or ""),
            "priority": float(getattr(goal, "priority", 0.0) or 0.0),
            "created_at": str(getattr(goal, "created_at", "") or ""),
        }

    def _content(self, now: datetime, *, focus_topic: str | None) -> dict[str, Any]:
        result, rows = self._runtime_tick(now)
        rows.update({"task": "CONTENT_COGNITION", "status": "OK" if result is not None else "DEGRADED"})
        if result is None or not result.generation_ready:
            return rows
        if self.web_worker is not None:
            try:
                rows["web_editorial"] = self._run_async(self.web_worker.run_once(now=now))
            except Exception as exc:  # noqa: BLE001
                rows["web_editorial"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.continuous_loop is None:
            return rows
        if focus_topic is None:
            backlog = self._social_stage_backlog_candidate()
            if backlog is not None:
                replay_rows: dict[str, Any] = {"status": "OK"}
                self._run_social_stage(
                    rows=replay_rows,
                    continuous=dict(backlog["result"]),
                    now=now,
                )
                rows["social_stage_backlog_recovery"] = {
                    "status": "REPLAYED",
                    "cycle": str(backlog["cycle"]),
                    "reason": str(backlog["reason"]),
                    "results": replay_rows,
                }
        # Strategy selection consumes traffic context, so refresh GSC immediately before it.
        # This preserves the pre-Wakeful ordering contract without restoring a monolithic poll loop.
        if self.gsc_collector is not None:
            try:
                rows["google_search_console"] = self.gsc_collector.collect(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["google_search_console"] = self._error(exc)
                rows["status"] = "DEGRADED"
        focus_cycle_id = f"v2-bootstrap-{int(now.timestamp())}" if focus_topic else None
        directed_cycle_id = focus_cycle_id
        preflight = self.continuous_loop.preflight(now=now, cycle_id=directed_cycle_id)
        base_cycle_id = str(preflight["cycle"])
        recovery = None
        mission_followup = None
        if focus_topic is None:
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
        owner_goal = self._active_owner_goal_context()
        if owner_goal is not None:
            rows["owner_goal_context"] = {
                "goal_id": owner_goal["goal_id"],
                "origin": owner_goal["origin"],
                "priority": owner_goal["priority"],
            }
        continuous = self._run_async(self.continuous_loop.run_once(
            now=now,
            cycle_id=directed_cycle_id,
            focus_topic=focus_topic,
            owner_goal=owner_goal,
        ))
        rows["continuous_content"] = continuous
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
                if str(continuous.get("error_type") or "") == "LLMProviderError":
                    schedule_provider_recovery = getattr(
                        self.wakeful, "schedule_provider_recovery", None
                    )
                    if callable(schedule_provider_recovery):
                        try:
                            rows["provider_recovery"] = schedule_provider_recovery(now)
                        except Exception as exc:  # noqa: BLE001
                            rows["provider_recovery"] = self._error(exc)
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
                schedule(now, seconds=5)
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
        self.wakeful.mark_cognition(now)
        return rows

    @staticmethod
    def _error(exc: Exception, **extra: Any) -> dict[str, Any]:
        return {"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:500], **extra}


__all__ = ["WakefulContentTaskRunner"]
