#!/usr/bin/env python3
"""Independent outcome collection and settlement worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.content_action_prediction import settle_action_predictions
from wechat_traffic_agent.state_events import FerroStateEventBus
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


INTERVAL_SECONDS = 3600


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _write_result(state_root: Path, result: dict) -> None:
    path = state_root / "application" / "workers" / "check-outcomes-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _prediction_cycles(state_root: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    root = state_root / "content" / "continuous" / "cycles"
    for path in root.glob("*/action_prediction.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        prediction_id = str(payload.get("prediction_id") or "")
        cycle = str(payload.get("cycle") or "")
        if prediction_id and cycle:
            rows[prediction_id] = cycle
    return rows


def main() -> int:
    state_root = _state_root()
    scheduler = IndependentTaskScheduler(state_root)
    events = FerroStateEventBus(state_root)
    heartbeat = WorkerHeartbeat(state_root, "check-outcomes")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    gsc = None
    if _enabled("CONTENT_GSC_ENABLED"):
        from wechat_traffic_agent.gsc_search_console import GoogleSearchConsoleCollector
        gsc = GoogleSearchConsoleCollector.from_environment(
            state_root=state_root,
            account=os.environ["CONTENT_WEB_BASE_URL"],
        )

    web = None
    if _enabled("CONTENT_WEB_OUTCOMES_ENABLED"):
        from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
        web = WebsiteOutcomeCollector(
            state_root=state_root,
            access_log=Path(os.environ.get("CONTENT_NGINX_ACCESS_LOG", "/var/log/nginx/access.log")),
            account=os.environ["CONTENT_WEB_BASE_URL"],
        )

    engagement = None
    if _enabled("CONTENT_WEB_ENGAGEMENT_ENABLED"):
        from wechat_traffic_agent.website_engagement import WebsiteEngagementOutcomeCollector
        engagement = WebsiteEngagementOutcomeCollector(
            state_root=state_root,
            account=os.environ["CONTENT_WEB_BASE_URL"],
        )

    evaluator = None
    if _enabled("CONTENT_WEBSITE_EXPERIMENTS_ENABLED"):
        from wechat_traffic_agent.website_experiment_evaluation import WebsiteExperimentEvaluator
        evaluator = WebsiteExperimentEvaluator(
            state_root=state_root,
            account=os.environ["CONTENT_WEB_BASE_URL"],
        )

    if not scheduler.pending("CHECK_OUTCOMES"):
        scheduler.schedule("CHECK_OUTCOMES", datetime.now(timezone.utc), reason="initial outcome collection")

    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("CHECK_OUTCOMES", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(1.0)
                continue

            heartbeat.write(status="RUNNING", detail="collecting")
            result: dict = {"task": "CHECK_OUTCOMES", "status": "OK"}
            for name, collector in (
                ("google_search_console", gsc),
                ("website_outcomes", web),
                ("website_engagement", engagement),
            ):
                if collector is None:
                    continue
                try:
                    result[name] = collector.collect(now=now)
                except Exception as exc:
                    result[name] = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                    result["status"] = "DEGRADED"

            if web is not None:
                try:
                    learning = settle_action_predictions(
                        state_root=state_root,
                        account=os.environ.get("CONTENT_WEB_BASE_URL", ""),
                        as_of=now,
                        mind=None,
                    )
                    result["content_action_prediction_learning"] = learning
                    cycles = _prediction_cycles(state_root)
                    queued = []
                    for outcome in learning.get("recorded") or ():
                        queued.append(events.emit(
                            "PREDICTION_OUTCOME",
                            dict(outcome),
                            observed_at=now,
                        ))
                        cycle = cycles.get(str(outcome.get("prediction_id") or ""))
                        if cycle:
                            expected = float(outcome.get("expected_pageviews") or 0.0)
                            actual = float(outcome.get("actual_pageviews") or 0.0)
                            reliability = float(outcome.get("reliability") or 0.0)
                            utility = math.tanh(
                                (actual - expected) / max(1.0, expected + 1.0)
                            ) * reliability
                            queued.append(events.emit(
                                "BRAIN_CORTEX_SETTLEMENT",
                                {
                                    "cycle": cycle,
                                    "utility": utility,
                                    "evidence_refs": list(outcome.get("evidence_refs") or ()),
                                },
                                observed_at=now,
                            ))
                    result["state_events"] = queued
                except Exception as exc:
                    result["content_action_prediction_learning"] = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                    result["status"] = "DEGRADED"

            if evaluator is not None:
                try:
                    result["website_experiment_evaluation"] = evaluator.evaluate(as_of=now)
                except Exception as exc:
                    result["website_experiment_evaluation"] = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                    result["status"] = "DEGRADED"

            _write_result(state_root, result)
            scheduler.schedule(
                "CHECK_OUTCOMES",
                datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS),
                reason="periodic independent outcome collection",
            )
            heartbeat.write(status="RUNNING", detail=str(result.get("status") or "UNKNOWN"))
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
