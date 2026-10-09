"""Wakeful resources task logic; the host injects runner components."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class ResourceTaskMethods:
    """Resources capability handlers for the shared wakeful host."""

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

    def _resource(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "RESOURCE_REVIEW", "status": "OK"}
        pressure, chatgpt_only = self._observe_model_pressure(rows, now)
        infra_pressure, initial_pressure = self._observe_infrastructure_pressure(rows, now)
        pressure, pressure_rank = self._emit_resource_pressure(rows, now, pressure, infra_pressure)
        self._discover_resource_options(rows, now, chatgpt_only, pressure)
        self._verify_resource_options(rows, now, chatgpt_only)
        self._trial_resource_options(rows, now, chatgpt_only)
        self._record_resource_world_effect(
            rows, initial_pressure, infra_pressure, pressure_rank,
        )
        return rows

    def _observe_model_pressure(self, rows, now):
        pressure = "UNKNOWN"
        from wechat_traffic_agent.runtime_backend import chatgpt_only_mode
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

        return pressure, chatgpt_only

    def _observe_infrastructure_pressure(self, rows, now):
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
                from wechat_traffic_agent.survival_resources import SurvivalResourceManager
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

        return infrastructure_pressure, initial_infrastructure_pressure

    def _emit_resource_pressure(self, rows, now, pressure, infrastructure_pressure):
        pressure_rank = {"UNKNOWN": 0, "NORMAL": 1, "WATCH": 2, "CONSERVE": 3, "CRITICAL": 4}
        combined_pressure = max(
            (pressure, infrastructure_pressure),
            key=lambda value: pressure_rank.get(str(value).upper(), 0),
        )
        pressure = str(combined_pressure).upper()
        rows["resource_pressure_state_event"] = self.state_events.emit(
            "SET_RESOURCE_PRESSURE",
            {"pressure": pressure},
            observed_at=now,
        )

        return pressure, pressure_rank

    def _discover_resource_options(self, rows, now, chatgpt_only, pressure):
        rows["llm_provider_mode"] = (
            "AGENT_RUNTIME_CHATGPT_ONLY" if chatgpt_only else "MULTI_PROVIDER"
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

    def _verify_resource_options(self, rows, now, chatgpt_only):
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

    def _trial_resource_options(self, rows, now, chatgpt_only):
        if chatgpt_only:
            rows["provider_trial_plans"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["provider_trials"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            rows["capability_windfall"] = {"status": "SKIPPED_CHATGPT_ONLY"}
            return
        try:
            trial_plans, plans, local_status, autonomous_pending = self._plan_provider_trials(rows, now)
            ready = self._run_planned_provider_trials(rows, now, trial_plans, plans, chatgpt_only)
            self._probe_provider_windfall(rows, now, chatgpt_only)
            self._request_provider_credentials(
                rows, now, plans, local_status, autonomous_pending, ready, trial_plans, chatgpt_only,
            )
        except Exception as exc:  # noqa: BLE001
            rows["provider_trial_plans"] = self._error(exc)
            rows["status"] = "DEGRADED"

    def _plan_provider_trials(self, rows, now):
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
            rows["resource_followup"] = {
                **self.task_scheduler.schedule(
                    "RESOURCE_REVIEW",
                    now + timedelta(seconds=300),
                    reason="unverified autonomous resource/provider evidence remains",
                ),
                "pending_verification_count": int(trial_plans.get("pending_verification_count") or 0),
                "pending_provider_verification_count": int(trial_plans.get("pending_provider_verification_count") or 0),
                "delay_seconds": 300,
            }

        return trial_plans, plans, local_status, autonomous_pending

    def _run_planned_provider_trials(self, rows, now, trial_plans, plans, chatgpt_only):
        ready = next((item for item in plans if item.get("status") == "READY_TO_TRIAL"), None)
        if ready is not None and not chatgpt_only:
            trial_result = self.resource_scout.run_ready_trials(now=now, provider_limit=2)
            rows["provider_trials"] = trial_result
            if bool(trial_result.get("replan_required")):
                rows["resource_followup"] = {
                    **self.task_scheduler.schedule(
                        "RESOURCE_REVIEW",
                        now + timedelta(seconds=300),
                        reason="provider trial evidence requires alternate routes",
                    ),
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

        return ready

    def _probe_provider_windfall(self, rows, now, chatgpt_only):
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

    def _request_provider_credentials(
        self, rows, now, plans, local_status, autonomous_pending, ready, trial_plans, chatgpt_only,
    ):
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

    def _record_resource_world_effect(self, rows, initial_infrastructure_pressure, infrastructure_pressure, pressure_rank):
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
        immediate = {
            "GENERATED_REPAIR_PROPOSED",
            "REQUESTED_TO_PI_REMOTE",
            "CANDIDATE_REJECTED_REPLAN_QUEUED",
            "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED",
        }
        if repair_statuses & immediate:
            retry_values = [
                int(item.get("retry_seconds") or 60)
                for item in nested
                if isinstance(item, dict)
                and str(item.get("status") or "") in immediate
            ]
            self.task_scheduler.schedule(
                "SELF_REPAIR",
                now + timedelta(seconds=min(retry_values or [60])),
                reason="continue independent self-repair",
            )
        elif "GENERATED_PLAN_DEFERRED" in repair_statuses:
            retry_values = [
                int(item.get("retry_seconds") or 900)
                for item in nested
                if isinstance(item, dict)
                and str(item.get("status") or "") == "GENERATED_PLAN_DEFERRED"
            ]
            self.task_scheduler.schedule(
                "SELF_REPAIR",
                now + timedelta(seconds=min(retry_values or [900])),
                reason="resume deferred independent self-repair",
            )
        return result

    def _reflect(
        self,
        now: datetime,
        *,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProviderError

        rows: dict[str, Any] = {"task": "REFLECT", "status": "OK"}
        continuous = getattr(self.daily_reflection, "reflect_now", None)
        if callable(continuous):
            try:
                rows["continuous_reflection"] = self._run_async(
                    continuous(now, relevant_refs=relevant_refs)
                )
            except AgentRuntimeChatGPTProviderError:
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
        except AgentRuntimeChatGPTProviderError:
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
        except AgentRuntimeChatGPTProviderError:
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
        from wechat_traffic_agent.runtime_backend import chatgpt_only_mode
        pending_windfall = None if chatgpt_only_mode() else self._load_windfall_pending()
        needs = list(getattr(runtime, "evolution_needs", ()) or ())
        if not needs:
            if pending_windfall is not None:
                return self._windfall_deliberation(runtime, pending_windfall, now)
            return {"status": "NO_OPEN_NEEDS"}
        candidate = self._select_unproposed_need(runtime, needs)
        if candidate is None:
            if pending_windfall is not None:
                return self._windfall_deliberation(runtime, pending_windfall, now)
            return {"status": "ALREADY_PROPOSED", "open_need_count": len(needs)}
        if not bool(getattr(runtime, "has_advisory_backend", False)):
            return {
                "status": "BACKEND_UNAVAILABLE",
                "need_id": str(getattr(candidate, "need_id", "") or ""),
                "pressure": float(getattr(candidate, "pressure", 0.0) or 0.0),
            }
        windfall_active = self._evolution_windfall_active()
        return self._propose_evolution_need(
            runtime, deliberate, candidate, pending_windfall, windfall_active, now,
        )

    @staticmethod
    def _select_unproposed_need(runtime, needs):
        active_ids = set()
        for hypothesis in list(getattr(runtime, "evolution_hypotheses", ()) or ()):
            status = getattr(hypothesis, "status", "")
            status_value = str(getattr(status, "value", status))
            if status_value not in {"PROPOSED", "SUPPORTED"}:
                continue
            need_id = str(getattr(hypothesis, "need_id", "") or "")
            if need_id:
                active_ids.add(need_id)
        ordered = sorted(
            needs,
            key=lambda item: (
                float(getattr(item, "pressure", 0.0) or 0.0),
                int(getattr(item, "created_cycle", 0) or 0),
            ),
            reverse=True,
        )
        return next(
            (
                item for item in ordered
                if str(getattr(item, "need_id", "") or "") not in active_ids
            ),
            None,
        )

    def _evolution_windfall_active(self) -> bool:
        from wechat_traffic_agent.runtime_backend import chatgpt_only_mode
        state_root = getattr(self, "state_root", None)
        if state_root is None or chatgpt_only_mode():
            return False
        policy_path = state_root / "application" / "llm_resource_pool" / "policy.json"
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            return bool(isinstance(policy, dict) and policy.get("windfall_active"))
        except (OSError, ValueError, TypeError):
            return False

    def _propose_evolution_need(
        self, runtime, deliberate, candidate, pending_windfall, windfall_active, now,
    ):
        token_budget = 1800 if windfall_active else 800
        fallback_reason = None
        try:
            hypothesis = self._run_async(deliberate(
                str(getattr(candidate, "need_id")), max_tokens=token_budget,
            ))
        except ValueError as exc:
            if not str(exc).startswith("evolution "):
                raise
            hypothesis = self._fallback_evolution_hypothesis(runtime, candidate, exc)
            fallback_reason = type(exc).__name__ + ":" + str(exc)[:180]
        persist = getattr(runtime, "persist", None)
        if callable(persist):
            persist()
        hypothesis_id = str(getattr(hypothesis, "hypothesis_id", "") or "")
        if pending_windfall is not None and windfall_active:
            self._consume_windfall_pending(pending_windfall, hypothesis_id, now)
        expected_fitness = dict(getattr(hypothesis, "expected_fitness", {}) or {})
        return {
            "status": "PROPOSED_FALLBACK" if fallback_reason else "PROPOSED",
            "need_id": str(getattr(candidate, "need_id", "") or ""),
            "hypothesis_id": hypothesis_id,
            "confidence": float(getattr(hypothesis, "confidence", 0.0) or 0.0),
            "fitness_dimensions": sorted(expected_fitness),
            "pressure": float(getattr(candidate, "pressure", 0.0) or 0.0),
            "observed_at": now.isoformat(),
            "windfall_used": windfall_active,
            "token_budget": token_budget,
            "fallback_reason": fallback_reason,
        }
