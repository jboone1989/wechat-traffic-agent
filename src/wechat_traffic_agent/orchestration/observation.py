"""Wakeful observation task logic; the host injects runner components."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class ObservationTaskMethods:
    """Observation capability handlers for the shared wakeful host."""

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
                rows["network_state_event"] = self.state_events.emit(
                    "NETWORK_EVIDENCE",
                    {"evidence_ref": evidence_ref},
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
                rows["network_state_event"] = self.state_events.emit(
                    "NETWORK_ERROR",
                    {"code": code or type(exc).__name__},
                    observed_at=now,
                )
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
                from wechat_traffic_agent.content_action_prediction import settle_action_predictions
                learning = settle_action_predictions(
                    state_root=self.state_root,
                    account=self.web_base_url,
                    as_of=now,
                    mind=self.content_mind,
                )
                rows["content_action_prediction_learning"] = learning
                feedback_rows = []
                for outcome in learning.get("recorded") or ():
                    feedback_rows.append(self.state_events.emit(
                        "PREDICTION_OUTCOME",
                        dict(outcome),
                        observed_at=now,
                    ))
                rows["neural_prediction_learning"] = {
                    "status": "QUEUED" if feedback_rows else "NO_NEW_OUTCOMES",
                    "count": len(feedback_rows),
                    "feedback": feedback_rows,
                    "publication_authority": False,
                }
                competition_rows = []
                if learning.get("recorded"):
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
                        competition_rows.append(self.state_events.emit(
                            "BRAIN_CORTEX_SETTLEMENT",
                            {
                                "cycle": cycle_id,
                                "utility": utility,
                                "evidence_refs": list(outcome.get("evidence_refs") or ()),
                            },
                            observed_at=observed_at,
                        ))
                rows["brain_vs_cortex_learning"] = {
                    "status": "QUEUED" if competition_rows else "NO_NEW_OUTCOMES",
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
                    retry_after = deferred[0].get("retry_after")
                    if retry_after:
                        retry_at = datetime.fromisoformat(
                            str(retry_after).replace("Z", "+00:00")
                        )
                        self.task_scheduler.schedule(
                            "VERIFY_PREDICTION",
                            retry_at,
                            reason="resume deferred prediction verification",
                        )
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
                    self.state_events.emit(
                        "PREDICTION_DUE",
                        {"prediction_ref": str(prediction.prediction_id)},
                        observed_at=now,
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
