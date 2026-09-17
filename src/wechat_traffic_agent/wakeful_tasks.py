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
        try:
            observation = self.resource_economy.observe(now)
            rows["resource_observation"] = observation
            projection = self.content_mind.public_projection().get("resource_awareness") or {}
            current = projection.get("current") or {}
            self.wakeful.set_resource_pressure(str(current.get("pressure") or "UNKNOWN"))
        except Exception as exc:  # noqa: BLE001
            rows["resource_observation"] = self._error(exc)
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
        return rows

    def _reflect(self, now: datetime) -> dict[str, Any]:
        try:
            result = self.daily_reflection.maybe_generate(now)
            return {"task": "REFLECT", "status": "OK", "daily_reflection": result}
        except Exception as exc:  # noqa: BLE001
            return {"task": "REFLECT", "status": "DEGRADED", "daily_reflection": self._error(exc)}

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
