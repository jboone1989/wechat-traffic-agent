"""Task-granular execution for the Content Agent Wakeful Core adopter."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime
from typing import Any


class WakefulContentTaskRunner:
    def __init__(self, **components: Any) -> None:
        self.__dict__.update(components)

    def run(self, task: str, *, now: datetime, focus_topic: str | None = None) -> dict[str, Any]:
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

    def _resource(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "RESOURCE_REVIEW", "status": "OK"}
        current: dict[str, Any] = {}
        recovery_status = "NOT_EVALUATED"
        offload_status = "UNAVAILABLE"
        try:
            observation = self.resource_economy.observe(now)
            rows["resource_observation"] = observation
            projection = self.content_mind.public_projection().get("resource_awareness") or {}
            current = projection.get("current") or {}
            self.wakeful.set_resource_pressure(str(current.get("pressure") or "UNKNOWN"))
        except Exception as exc:  # noqa: BLE001
            rows["resource_observation"] = self._error(exc)
            rows["status"] = "DEGRADED"
        host_recovery = getattr(self, "host_recovery", None)
        if host_recovery is not None:
            try:
                reasons = {str(item) for item in current.get("pressure_reasons") or ()}
                audit = host_recovery.audit(now=now)
                rows["host_recovery_audit"] = audit
                if (
                    str(current.get("pressure") or "") in {"WATCH", "CONSERVE", "CRITICAL"}
                    and any(item.startswith("disk_") for item in reasons)
                ):
                    recovery = host_recovery.optimize(now=now)
                    rows["host_recovery"] = recovery
                    recovery_status = str(recovery.get("status") or "UNKNOWN")
                else:
                    recovery_status = "NOT_NEEDED"
            except Exception as exc:  # noqa: BLE001
                rows["host_recovery"] = self._error(exc)
                recovery_status = "FAILED"
                rows["status"] = "DEGRADED"
        compute_scout = getattr(self, "compute_resource_scout", None)
        survival_nodes = getattr(self, "survival_nodes", None)
        if compute_scout is not None and survival_nodes is not None:
            try:
                pressure = str(current.get("pressure") or "UNKNOWN")
                rows["compute_resource_scout"] = compute_scout.maybe_discover(
                    now=now,
                    urgent=pressure == "CRITICAL" and recovery_status not in {"RECOVERED", "NOT_NEEDED"},
                )
                rows["compute_resource_verification"] = compute_scout.verify_pending(now=now, limit=6)
            except Exception as exc:  # noqa: BLE001
                rows["compute_survival"] = self._error(exc)
                rows["status"] = "DEGRADED"
        placement = getattr(self, "habitat_placement", None)
        if placement is not None:
            try:
                network_service = getattr(self, "network_service", None)
                capability_ids = tuple(
                    str(item) for item in (getattr(network_service, "capability_ids", ()) or ())
                    if str(item).strip()
                )
                placement_review = placement.review(
                    now=now,
                    resource_pressure=str(current.get("pressure") or "UNKNOWN"),
                    capability_ids=capability_ids,
                )
                rows["habitat_placement"] = placement_review
                offload_status = str(placement_review.get("offload_status") or "UNAVAILABLE")
                habitat_learning = getattr(self, "habitat_learning", None)
                if habitat_learning is not None:
                    habitat_state = habitat_learning.observe_habitat(
                        now=now,
                        current_resource=current,
                        placement_review=placement_review,
                    )
                    rows["habitat_learning"] = {
                        "status": "OBSERVED",
                        "state": habitat_state.payload(),
                        "candidate_catalog": list(habitat_learning.candidate_catalog()),
                    }
            except Exception as exc:  # noqa: BLE001
                rows["habitat_placement"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if survival_nodes is not None:
            try:
                rows["migration_intent"] = survival_nodes.migration_intent(
                    resource_pressure=str(current.get("pressure") or "UNKNOWN"),
                    recovery_status=recovery_status,
                    offload_status=offload_status,
                )
            except Exception as exc:  # noqa: BLE001
                rows["migration_intent"] = self._error(exc)
                rows["status"] = "DEGRADED"
        try:
            rows["resource_scout"] = self.resource_scout.maybe_discover(now=now)
        except Exception as exc:  # noqa: BLE001
            rows["resource_scout"] = self._error(exc)
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
        return rows

    def _reflect(self, now: datetime) -> dict[str, Any]:
        rows: dict[str, Any] = {"task": "REFLECT", "status": "OK"}
        try:
            rows["daily_reflection"] = self.daily_reflection.maybe_generate(now)
        except Exception as exc:  # noqa: BLE001
            rows["daily_reflection"] = self._error(exc)
            rows["status"] = "DEGRADED"
        try:
            rows["evolution_deliberation"] = self._evolution_deliberation(now)
        except Exception as exc:  # noqa: BLE001
            rows["evolution_deliberation"] = self._error(exc)
            rows["status"] = "DEGRADED"
        return rows

    def _evolution_deliberation(self, now: datetime) -> dict[str, Any]:
        runtime = getattr(self.app, "domain_runtime", None)
        deliberate = getattr(runtime, "deliberate_evolution_need", None)
        if runtime is None or not callable(deliberate):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}

        needs = list(getattr(runtime, "evolution_needs", ()) or ())
        if not needs:
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

        hypothesis = asyncio.run(
            deliberate(
                str(getattr(candidate, "need_id")),
                max_tokens=800,
            )
        )
        persist = getattr(runtime, "persist", None)
        if callable(persist):
            persist()
        expected_fitness = dict(
            getattr(hypothesis, "expected_fitness", {}) or {}
        )
        return {
            "status": "PROPOSED",
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
        }

    def _x(self, now: datetime) -> dict[str, Any]:
        if self.x_loop is None:
            return {"task": "CHECK_X", "status": "DISABLED"}
        try:
            result = asyncio.run(self.x_loop.observe(now=now))
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
            draft = asyncio.run(self.draft_worker.run_once(now=now))
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
            result = asyncio.run(self.continuous_loop.verify_due_predictions(now=now))
            return {"task": "VERIFY_PREDICTION", "status": "OK", "prediction_verification": result}
        except Exception as exc:  # noqa: BLE001
            return {"task": "VERIFY_PREDICTION", "status": "DEGRADED", "prediction_verification": self._error(exc)}

    def _content(self, now: datetime, *, focus_topic: str | None) -> dict[str, Any]:
        result, rows = self._runtime_tick(now)
        rows.update({"task": "CONTENT_COGNITION", "status": "OK" if result is not None else "DEGRADED"})
        if result is None or not result.generation_ready:
            return rows
        if self.web_worker is not None:
            try:
                rows["web_editorial"] = asyncio.run(self.web_worker.run_once(now=now))
            except Exception as exc:  # noqa: BLE001
                rows["web_editorial"] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.continuous_loop is None:
            return rows
        # Strategy selection consumes traffic context, so refresh GSC immediately before it.
        # This preserves the pre-Wakeful ordering contract without restoring a monolithic poll loop.
        if self.gsc_collector is not None:
            try:
                rows["google_search_console"] = self.gsc_collector.collect(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["google_search_console"] = self._error(exc)
                rows["status"] = "DEGRADED"
        focus_cycle_id = f"v2-bootstrap-{int(now.timestamp())}" if focus_topic else None
        preflight = self.continuous_loop.preflight(now=now, cycle_id=focus_cycle_id)
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
        continuous = asyncio.run(self.continuous_loop.run_once(
            now=now, cycle_id=focus_cycle_id, focus_topic=focus_topic,
        ))
        rows["continuous_content"] = continuous
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
            return rows
        try:
            rows["social_expression_drafts"] = asyncio.run(
                self.social_expression_loop.after_content_cycle(result=continuous, now=now)
            )
        except Exception as exc:  # noqa: BLE001
            rows["social_expression_drafts"] = self._error(exc)
            rows["status"] = "DEGRADED"
        for name, publisher in (
            ("x_browser_publication", self.x_browser_publisher),
            ("okx_orbit_publication", self.okx_orbit_publisher),
        ):
            try:
                rows[name] = asyncio.run(publisher.after_content_cycle(
                    cycle=str(continuous.get("cycle") or ""), now=now,
                ))
            except Exception as exc:  # noqa: BLE001
                rows[name] = self._error(exc)
                rows["status"] = "DEGRADED"
        if self.x_loop is not None:
            try:
                rows["x_social_expression"] = asyncio.run(self.x_loop.after_content_cycle(result=continuous, now=now))
            except Exception as exc:  # noqa: BLE001
                rows["x_social_expression"] = self._error(exc)
                rows["status"] = "DEGRADED"
        self.wakeful.mark_cognition(now)
        return rows

    @staticmethod
    def _error(exc: Exception, **extra: Any) -> dict[str, Any]:
        return {"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:500], **extra}


__all__ = ["WakefulContentTaskRunner"]
