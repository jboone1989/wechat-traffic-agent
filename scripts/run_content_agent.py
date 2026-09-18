#!/usr/bin/env python3
"""Run the persistent Content Agent with Runtime, VirtualBrain and Agent Network."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.network_service import ContentNetworkService, network_enabled  # noqa: E402
from wechat_traffic_agent.runtime_app import APPLICATION_VERSION, build_content_application  # noqa: E402
from wechat_traffic_agent.trading_draft_worker import TradingResearchDraftWorker, trading_draft_worker_enabled  # noqa: E402
from wechat_traffic_agent.public_activity import emit_public_activity  # noqa: E402
from wechat_traffic_agent.social_expression import SocialExpressionDraftWorker  # noqa: E402
from wechat_traffic_agent.okx_orbit import OKXOrbitPublisher  # noqa: E402
from wechat_traffic_agent.x_browser import XBrowserPublisher  # noqa: E402


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _health_path() -> Path:
    return _state_root() / "application" / "health.json"


def _show_health() -> int:
    path = _health_path()
    if not path.exists():
        print(json.dumps({"ok": False, "reason": "health_not_recorded"}))
        return 1
    print(path.read_text(encoding="utf-8").strip())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="persistent Content Agent")
    parser.add_argument("--once", action="store_true", help="Run one application tick and exit")
    parser.add_argument("--health", action="store_true", help="Print persisted application health")
    parser.add_argument("--focus-topic", help="One-shot content focus slug; requires --once and does not replace the persisted autonomous goal")
    parser.add_argument("--watchdog-seconds", type=int, default=1800, help="Low-frequency liveness watchdog; never a cognition cadence")
    return parser


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = _parser(); args = parser.parse_args()
    if args.health: return _show_health()
    if args.focus_topic and not args.once: parser.error("--focus-topic requires --once")
    if args.watchdog_seconds < 60: parser.error("watchdog-seconds must be at least 60")

    state_root = _state_root()
    from wechat_traffic_agent.llm_resources import sync_llm_resource_pool
    try:
        pool_bootstrap = sync_llm_resource_pool(
            state_root=state_root,
            now=datetime.now(timezone.utc).replace(microsecond=0),
        )
        print(json.dumps({"llm_resource_pool": pool_bootstrap}, sort_keys=True))
    except Exception as exc:
        print(json.dumps({
            "llm_resource_pool": {
                "status": "BOOTSTRAP_DEGRADED",
                "error_type": type(exc).__name__,
            }
        }, sort_keys=True), file=sys.stderr)
    app = build_content_application(root=state_root, manifest_path=ROOT / "agent.yaml")
    from wechat_traffic_agent.content_mind import ContentMind
    from wechat_traffic_agent.resource_economy import ContentResourceEconomy
    from wechat_traffic_agent.human_assistance import HumanAssistanceService, operator_notifier
    from wechat_traffic_agent.daily_reflection import DailyReflectionService
    content_mind = ContentMind(root=state_root, subject_id=app.subject.subject_id)
    resource_economy = ContentResourceEconomy(state_root=state_root, mind=content_mind)
    routing_policy = resource_economy.sync_llm_routing_policy(
        now=datetime.now(timezone.utc).replace(microsecond=0)
    )
    print(json.dumps({"llm_routing_policy": routing_policy}, sort_keys=True))
    from wechat_traffic_agent.resource_scout import ContentResourceScout
    resource_scout = ContentResourceScout(state_root=state_root, capability_runtime=app.runtime)
    private_notifier = operator_notifier()
    human_assistance = HumanAssistanceService(
        state_root=state_root,
        notifier=private_notifier,
        evolution_runtime=app.domain_runtime,
    )
    evolution_backfill = human_assistance.backfill_evolution_observations(
        now=datetime.now(timezone.utc).replace(microsecond=0)
    )
    if evolution_backfill.get("recorded") or evolution_backfill.get("failed"):
        print(json.dumps({"human_assistance_evolution_backfill": evolution_backfill}, sort_keys=True))
    daily_reflection = DailyReflectionService(state_root=state_root, mind=content_mind, notifier=private_notifier)
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime, WakefulState
    wakeful_runtime = ContentWakefulRuntime(state_root=state_root, content_interval_hours=int(os.environ.get("CONTENT_CONTINUOUS_INTERVAL_HOURS", "6")), watchdog_seconds=args.watchdog_seconds)
    try:
        from wechat_traffic_agent.public_persona import ensure_public_persona
        persona = asyncio.run(ensure_public_persona(runtime=app.domain_runtime, state_root=state_root))
        print(json.dumps({"public_persona": {"status": "ACTIVE", "name": persona.name, "affiliation": persona.affiliation}}, ensure_ascii=False))
    except Exception as exc:  # identity bootstrap must not take the agent offline
        print(json.dumps({"public_persona": {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "message": str(exc)[:300]}}, ensure_ascii=False), file=sys.stderr)
    social_expression_loop = SocialExpressionDraftWorker(runtime=app.domain_runtime, state_root=state_root)
    okx_orbit_publisher = OKXOrbitPublisher(state_root=state_root)
    x_browser_publisher = XBrowserPublisher(state_root=state_root)
    x_loop = None
    if _enabled("CONTENT_X_ENABLED"):
        from wechat_traffic_agent.x_social import XAutonomyLoop
        x_loop = XAutonomyLoop(
            runtime=app.runtime, model_runtime=app.domain_runtime, state_root=state_root,
            base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
        )
    live_agent_service = None
    from wechat_traffic_agent.live_agent import build_live_agent_service, live_agent_enabled
    if live_agent_enabled():
        live_agent_service = build_live_agent_service(state_root=state_root, application=app, mind=content_mind, wakeful=wakeful_runtime)
        live_agent_service.start()
        print(f"live-agent endpoint=http://{live_agent_service.host}:{live_agent_service.port}")

    network_service: ContentNetworkService | None = None
    if network_enabled():
        try:
            network_service = ContentNetworkService.from_environment(root=state_root, application=app)
            network_service.start()
            print(json.dumps({
                "agent_network": {
                    "status": "JOINED" if network_service.joined else "ENDPOINT_READY_JOIN_PENDING",
                    "endpoint": network_service.endpoint,
                    "seed_count": len(network_service.seed_urls),
                }
            }, sort_keys=True))
        except Exception as exc:  # network is a capability, never a life dependency
            print(json.dumps({"agent_network": {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "message": str(exc)[:300]}}, sort_keys=True), file=sys.stderr)
            wakeful_runtime.emit_network_error(code=type(exc).__name__)
            network_service = None

    draft_worker: TradingResearchDraftWorker | None = None
    if trading_draft_worker_enabled() and network_service is not None:
        draft_worker = TradingResearchDraftWorker(runtime=app.domain_runtime, state_root=state_root)
    elif trading_draft_worker_enabled():
        print(json.dumps({"draft_worker": {"status": "DISABLED_NETWORK_UNAVAILABLE"}}, sort_keys=True), file=sys.stderr)

    web_worker = None
    if _enabled("CONTENT_WEB_DRAFTS_ENABLED"):
        from wechat_traffic_agent.web_editorial import WebEditorialWorker
        web_worker = WebEditorialWorker(runtime=app.domain_runtime, state_root=state_root,
            artifacts_path=Path(os.environ.get("CONTENT_WEB_ARTIFACTS", str(ROOT / "content/starter"))),
            site_root=Path(os.environ["CONTENT_WEB_SITE_ROOT"]), base_url=os.environ["CONTENT_WEB_BASE_URL"])

    web_outcome_collector = None
    if _enabled("CONTENT_WEB_OUTCOMES_ENABLED"):
        from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
        web_outcome_collector = WebsiteOutcomeCollector(state_root=state_root,
            access_log=Path(os.environ.get("CONTENT_NGINX_ACCESS_LOG", "/var/log/nginx/access.log")),
            account=os.environ["CONTENT_WEB_BASE_URL"])

    engagement_outcome_collector = None
    if _enabled("CONTENT_WEB_ENGAGEMENT_ENABLED"):
        from wechat_traffic_agent.website_engagement import WebsiteEngagementOutcomeCollector
        engagement_outcome_collector = WebsiteEngagementOutcomeCollector(
            state_root=state_root, account=os.environ["CONTENT_WEB_BASE_URL"])

    website_experiment_evaluator = None
    if _enabled("CONTENT_WEBSITE_EXPERIMENTS_ENABLED"):
        from wechat_traffic_agent.website_experiment_evaluation import WebsiteExperimentEvaluator
        website_experiment_evaluator = WebsiteExperimentEvaluator(state_root=state_root, account=os.environ["CONTENT_WEB_BASE_URL"])

    gsc_collector = None
    if _enabled("CONTENT_GSC_ENABLED"):
        from wechat_traffic_agent.gsc_search_console import GoogleSearchConsoleCollector
        gsc_collector = GoogleSearchConsoleCollector.from_environment(state_root=state_root, account=os.environ["CONTENT_WEB_BASE_URL"])

    continuous_loop = None
    if _enabled("CONTENT_CONTINUOUS_ENABLED"):
        from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
        publisher = None
        if _enabled("CONTENT_AUTO_PUBLISH_ENABLED"):
            from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
            from wechat_traffic_agent.channels.web import StaticWebsiteConfig
            config = StaticWebsiteConfig.model_validate_json(Path(os.environ["CONTENT_AUTO_SITE_CONFIG"]).read_text())
            if config.base_url != os.environ["CONTENT_WEB_BASE_URL"].rstrip("/"):
                raise ValueError("automatic publisher and source inventory base URLs must match")
            publisher = AutomaticWebsitePublisher(state_root=state_root, public_root=Path(os.environ["CONTENT_AUTO_PUBLIC_ROOT"]),
                starter_path=ROOT / "content/starter", config=config)
        continuous_loop = ContinuousContentLoop(runtime=app.domain_runtime, state_root=state_root,
            artifacts_path=ROOT / "content/starter", site_root=Path(os.environ["CONTENT_WEB_SITE_ROOT"]),
            base_url=os.environ["CONTENT_WEB_BASE_URL"], curriculum_path=ROOT / "content/learning/writing-practice.md",
            feeds=json.loads(os.environ["CONTENT_NEWS_FEEDS"]), interval_hours=int(os.environ.get("CONTENT_CONTINUOUS_INTERVAL_HOURS", "6")),
            publisher=publisher, mind=content_mind, capability_runtime=app.runtime,
            website_experiments=_enabled("CONTENT_WEBSITE_EXPERIMENTS_ENABLED"))

    stopping = False
    def _stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        wakeful_runtime.core.interrupt()
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)

    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
    task_runner = WakefulContentTaskRunner(
        app=app,
        state_root=state_root,
        content_mind=content_mind,
        resource_economy=resource_economy,
        resource_scout=resource_scout,
        daily_reflection=daily_reflection,
        human_assistance=human_assistance,
        network_service=network_service,
        draft_worker=draft_worker,
        web_worker=web_worker,
        gsc_collector=gsc_collector,
        continuous_loop=continuous_loop,
        social_expression_loop=social_expression_loop,
        x_loop=x_loop,
        x_browser_publisher=x_browser_publisher,
        okx_orbit_publisher=okx_orbit_publisher,
        web_outcome_collector=web_outcome_collector,
        engagement_outcome_collector=engagement_outcome_collector,
        website_experiment_evaluator=website_experiment_evaluator,
        wakeful=wakeful_runtime,
        web_base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
    )
    print(f"{APPLICATION_VERSION} publish_mode={app.status_observation()['publish_mode']}")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        wakeful_runtime.ensure_initial_plans(now)
        if args.once:
            reason_task = "CONTENT_COGNITION"
            wake_reason = None
        else:
            wake_reason = wakeful_runtime.core.pop_ready()
            if wake_reason is None:
                wake_reason = wakeful_runtime.wait(stopping=lambda: stopping)
            reason_task = wake_reason.task if wake_reason.task else None
        while not stopping:

            if wake_reason is not None and wake_reason.kind == "STOP":
                break
            now = datetime.now(timezone.utc).replace(microsecond=0)
            task = reason_task or ("OBSERVE_WORLD" if wake_reason and wake_reason.cognition_required else "EVENT_OBSERVE")
            runtime_event_cycle = f"wake-{int(now.timestamp())}"
            wakeful_runtime.core.set_state(
                WakefulState.THINKING if task in {"CONTENT_COGNITION", "OBSERVE_WORLD"} else WakefulState.OBSERVING
            )
            emit_public_activity(
                state_root, cycle=runtime_event_cycle, phase=task,
                title=f"Awake · {task.replace('_', ' ').title()}",
                detail=(wake_reason.detail if wake_reason is not None else "One-shot content cognition"),
            )
            if task == "EVENT_OBSERVE":
                task_result = {"task": task, "status": "NO_DEEP_COGNITION_JUSTIFIED"}
            else:
                task_result = task_runner.run(task, now=now, focus_topic=args.focus_topic)
            print(json.dumps({"wake_reason": wake_reason.__dict__ if wake_reason else {"kind":"ONCE"}, "task_result": task_result}, ensure_ascii=False, sort_keys=True))
            status = str(task_result.get("status") or "UNKNOWN")
            wakeful_runtime.sync_prediction_wakes(now)
            if task not in {"EVENT_OBSERVE"}:
                wakeful_runtime.core.mark_action(now)
            if wake_reason is not None and wake_reason.kind == "PLAN" and reason_task:
                wakeful_runtime.schedule_next(reason_task, now)
            wakeful_runtime.core.set_state(WakefulState.WAITING)
            next_state = wakeful_runtime.public_snapshot()
            emit_public_activity(
                state_root, cycle=runtime_event_cycle, phase="WAITING",
                title="Awake · Waiting",
                detail=(
                    "No deeper cognition is currently justified. "
                    f"Next intentional wake: {next_state.get('next_wake_at') or 'event-driven'} · "
                    f"{next_state.get('next_wake_reason') or 'external stimulus'}"
                ),
                status="completed" if status not in {"ERROR", "DEGRADED"} else "stopped",
            )
            if args.once:
                return 1 if status in {"ERROR", "DEGRADED"} else 0
            wake_reason = wakeful_runtime.wait(stopping=lambda: stopping)
            reason_task = wake_reason.task if wake_reason.task else None

        return 0
    finally:
        if gsc_collector is not None: gsc_collector.close()
        if live_agent_service is not None: live_agent_service.stop()
        if network_service is not None: network_service.stop()
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
