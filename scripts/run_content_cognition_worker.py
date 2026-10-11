#!/usr/bin/env python3
"""Independent CONTENT_COGNITION worker.

Owns only the content-production responsibility. Ferro internal state is read
and mutated through FerroStateClient/EventBus; scheduling is per-task.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.behavioral_state import build_behavioral_state, build_phenotype_prior
from wechat_traffic_agent.runtime_backend import load_independent_ferro_cognition_runtime
from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
from wechat_traffic_agent.okx_orbit import OKXOrbitPublisher
from wechat_traffic_agent.resource_economy import ContentResourceEconomy
from wechat_traffic_agent.runtime_app import build_content_application
from wechat_traffic_agent.social_expression import SocialExpressionDraftWorker
from wechat_traffic_agent.state_events import FerroStateClient
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat
from wechat_traffic_agent.x_browser import XBrowserPublisher


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (ROOT / "state" / "content_application").resolve()
    )


def _write_last_result(state_root: Path, result: dict) -> None:
    path = state_root / "application" / "workers" / "content-cognition-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "result": result,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _daily_schedule(
    posting_goal: DailyPostingGoal,
    scheduler: IndependentTaskScheduler,
    now: datetime,
) -> dict:
    status = posting_goal.status(now)
    if not status.get("enabled"):
        return status
    # Schedule even once the daily minimum is met: next_attempt_at carries
    # the goal-met hourly cadence set by DailyPostingGoal.observe, and this
    # is the only place a due CONTENT_COGNITION plan is (re)created between
    # boots — returning early here would strand the worker with nothing due
    # and no path to re-plan until the next restart.
    at = datetime.fromisoformat(
        str(status["next_attempt_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    scheduler.schedule(
        "CONTENT_COGNITION",
        at,
        reason="owner-required temporary daily original posting minimum",
        relevant_refs=(str(status["goal_id"]),),
        preemptible=False,
    )
    return status


def _commit_pending_reflections(state_root: Path, content_mind: ContentMind) -> int:
    root = state_root / "application" / "reflection_results"
    committed = 0
    for path in sorted(root.glob("reflection-*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if row.get("status") != "PENDING_CONTENT_MIND_COMMIT":
            continue
        content_mind.record_daily_reflection(
            target_date=str(row.get("target_date") or ""),
            reflection_text=str(row.get("reflection_text") or ""),
            # This persisted receipt proves the reflection happened; it is
            # self-memory provenance, never evidence for a world claim.
            evidence_refs=tuple(dict.fromkeys([
                str(path.relative_to(state_root)),
                *(str(ref) for ref in row.get("evidence_refs") or () if str(ref)),
            ])),
        )
        row["status"] = "COMMITTED"
        row["committed_at"] = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        committed += 1
    return committed


def main() -> int:
    state_root = _state_root()
    heartbeat = WorkerHeartbeat(state_root, "content-cognition")
    scheduler = IndependentTaskScheduler(state_root)
    state_client = FerroStateClient(state_root)
    posting_goal = DailyPostingGoal(state_root)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    app = build_content_application(
        root=state_root,
        manifest_path=ROOT / "agent.yaml",
        allow_missing_required_capabilities=False,
    )
    content_mind = ContentMind(
        root=state_root,
        subject_id=app.subject.subject_id,
    )
    resource_economy = ContentResourceEconomy(
        state_root=state_root,
        mind=content_mind,
    )
    resource_economy.sync_llm_routing_policy(
        now=datetime.now(timezone.utc).replace(microsecond=0)
    )

    from wechat_traffic_agent.cognition_context import bind_ferro_cognition_context

    bind_ferro_cognition_context(
        application=app,
        mind=content_mind,
        wakeful=state_client,
    )

    def publication_behavioral_context():
        behavioral_state = build_behavioral_state(state_client.behavioral_projection())
        prior = build_phenotype_prior(behavioral_state)
        return {
            "phenotype_prior": {key: prior[key] for key in (
                "schema_version", "stable_phenotype", "dynamic_modulation",
            )},
            "behavioral_state": {key: behavioral_state[key] for key in (
                "relationship", "behavioral_directives", "salient_expression_profile",
                "expression_policy",
            )},
        }

    publication_runtime = load_independent_ferro_cognition_runtime(
        state_root=state_root,
        behavioral_context_provider=publication_behavioral_context,
        session_id="ferro-x-expression",
    )
    social_expression_loop = SocialExpressionDraftWorker(
        runtime=publication_runtime,
        state_root=state_root,
    )
    okx_orbit_publisher = OKXOrbitPublisher(state_root=state_root)

    x_growth_mission = None
    x_loop = None
    if _enabled("CONTENT_X_ENABLED"):
        from wechat_traffic_agent.x_growth_mission import XGrowthMissionCoordinator
        from wechat_traffic_agent.x_social import XAutonomyLoop

        x_growth_mission = XGrowthMissionCoordinator.from_environment(
            runtime=app.domain_runtime,
            state_root=state_root,
        )
        x_loop = XAutonomyLoop(
            runtime=app.runtime,
            model_runtime=publication_runtime,
            state_root=state_root,
            base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
            growth_mission=x_growth_mission,
            social_observer=state_client.observe_social_interaction,
            social_context_provider=state_client.relationship,
        )

    x_browser_publisher = XBrowserPublisher(
        state_root=state_root,
        growth_mission=x_growth_mission,
        mission_baseline_observer=(
            x_loop.capture_mission_publication_baseline
            if x_loop is not None
            else None
        ),
    )

    web_worker = None
    if _enabled("CONTENT_WEB_DRAFTS_ENABLED"):
        from wechat_traffic_agent.web_editorial import WebEditorialWorker

        web_worker = WebEditorialWorker(
            runtime=app.domain_runtime,
            state_root=state_root,
            artifacts_path=Path(
                os.environ.get(
                    "CONTENT_WEB_ARTIFACTS",
                    str(ROOT / "content" / "starter"),
                )
            ),
            site_root=Path(os.environ["CONTENT_WEB_SITE_ROOT"]),
            base_url=os.environ["CONTENT_WEB_BASE_URL"],
        )

    gsc_collector = None
    if _enabled("CONTENT_GSC_ENABLED"):
        from wechat_traffic_agent.gsc_search_console import GoogleSearchConsoleCollector

        gsc_collector = GoogleSearchConsoleCollector.from_environment(
            state_root=state_root,
            account=os.environ["CONTENT_WEB_BASE_URL"],
        )

    content_evolution_bridge = None
    web_base_url = os.environ.get("CONTENT_WEB_BASE_URL", "").strip()
    if web_base_url and _enabled("CONTENT_CONTINUOUS_ENABLED"):
        from wechat_traffic_agent.evolution_bridge import ContentEvolutionBridge

        content_evolution_bridge = ContentEvolutionBridge(
            runtime=app.domain_runtime,
            state_root=state_root,
            account=web_base_url,
        )

    continuous_loop = None
    if _enabled("CONTENT_CONTINUOUS_ENABLED"):
        from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop

        publisher = None
        if _enabled("CONTENT_AUTO_PUBLISH_ENABLED"):
            from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
            from wechat_traffic_agent.channels.web import StaticWebsiteConfig

            config = StaticWebsiteConfig.model_validate_json(
                Path(os.environ["CONTENT_AUTO_SITE_CONFIG"]).read_text()
            )
            publisher = AutomaticWebsitePublisher(
                state_root=state_root,
                public_root=Path(os.environ["CONTENT_AUTO_PUBLIC_ROOT"]),
                starter_path=ROOT / "content" / "starter",
                config=config,
            )

        continuous_loop = ContinuousContentLoop(
            runtime=app.domain_runtime,
            state_root=state_root,
            artifacts_path=ROOT / "content" / "starter",
            site_root=Path(os.environ["CONTENT_WEB_SITE_ROOT"]),
            base_url=os.environ["CONTENT_WEB_BASE_URL"],
            curriculum_path=ROOT / "content" / "learning" / "writing-practice.md",
            feeds=json.loads(os.environ["CONTENT_NEWS_FEEDS"]),
            interval_hours=int(
                os.environ.get("CONTENT_CONTINUOUS_INTERVAL_HOURS", "6")
            ),
            publisher=publisher,
            mind=content_mind,
            capability_runtime=app.runtime,
            website_experiments=_enabled("CONTENT_WEBSITE_EXPERIMENTS_ENABLED"),
            neural_runtime=state_client,
            evolution_bridge=content_evolution_bridge,
        )

    runner = WakefulContentTaskRunner(
        app=app,
        state_root=state_root,
        state_client=state_client,
        content_mind=content_mind,
        resource_economy=resource_economy,
        network_service=None,
        draft_worker=None,
        web_worker=web_worker,
        gsc_collector=gsc_collector,
        continuous_loop=continuous_loop,
        social_expression_loop=social_expression_loop,
        x_loop=x_loop,
        x_browser_publisher=x_browser_publisher,
        okx_orbit_publisher=okx_orbit_publisher,
        evolution_bridge=content_evolution_bridge,
        wakeful=None,
        web_base_url=web_base_url,
        stop_requested=lambda: stopping,
    )

    # The old monolith owned HTTP 8104 (public pages/operator) and 8103
    # (signed Agent Network). The independent worker split must retain them.
    # Reuse this already-open application; never create a second neural owner.
    from wechat_traffic_agent.live_agent import build_live_agent_service, live_agent_enabled
    from wechat_traffic_agent.network_service import ContentNetworkService, network_enabled

    live_agent_service = None
    network_service = None
    if live_agent_enabled():
        live_agent_service = build_live_agent_service(
            state_root=state_root,
            application=app,
            mind=content_mind,
            wakeful=state_client,
        )
        live_agent_service.start()
        print(f"Ferro public HTTP ready at 127.0.0.1:{live_agent_service.port}", flush=True)

    if network_enabled():
        network_service = ContentNetworkService.from_environment(
            root=state_root,
            application=app,
        )
        try:
            network_service.start()
        except Exception as exc:
            print(
                json.dumps({
                    "public_network_startup": "FAILED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:400],
                }),
                file=sys.stderr,
                flush=True,
            )
            network_service = None
        else:
            print(f"Ferro Agent Network ready at 127.0.0.1:{network_service.port}", flush=True)

    runner.network_service = network_service

    now = datetime.now(timezone.utc)
    _daily_schedule(posting_goal, scheduler, now)
    heartbeat.write(status="RUNNING")
    from wechat_traffic_agent.square_website_syndication import SquareWebsiteSyndication
    square_syndication = SquareWebsiteSyndication(state_root)

    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            square_result = asyncio.run(square_syndication.run_once(
                runtime=publication_runtime, now=now))
            if square_result is not None:
                summary = {key: square_result.get(key) for key in
                           ('status', 'cycle', 'draft_id', 'rationale', 'error', 'retry_at')}
                print(json.dumps({"square_website_syndication": summary},
                                 ensure_ascii=False, default=str), flush=True)
            wake = scheduler.claim_due("CONTENT_COGNITION", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(0.5)
                continue

            heartbeat.write(status="RUNNING", detail="producing")
            _commit_pending_reflections(state_root, content_mind)
            try:
                result = runner.run(
                    "CONTENT_COGNITION",
                    now=now,
                    relevant_refs=tuple(wake.relevant_refs),
                )
            except Exception as exc:
                result = {
                    "task": "CONTENT_COGNITION",
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
                scheduler.schedule(
                    "CONTENT_COGNITION",
                    now + timedelta(seconds=300),
                    reason="retry failed content cognition",
                    relevant_refs=tuple(wake.relevant_refs),
                )

            posting_goal.observe("CONTENT_COGNITION", result, datetime.now(timezone.utc))
            posting_goal.reconcile(datetime.now(timezone.utc))
            _daily_schedule(
                posting_goal,
                scheduler,
                datetime.now(timezone.utc),
            )
            _write_last_result(state_root, result)
            heartbeat.write(
                status="RUNNING",
                detail=str(result.get("status") or "UNKNOWN"),
            )
        return 0
    finally:
        if live_agent_service is not None:
            live_agent_service.stop()
        if network_service is not None:
            network_service.stop()
        app.close()
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
