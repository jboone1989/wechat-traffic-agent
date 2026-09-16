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
    parser.add_argument("--poll-seconds", type=int, default=300)
    return parser


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = _parser(); args = parser.parse_args()
    if args.health: return _show_health()
    if args.focus_topic and not args.once: parser.error("--focus-topic requires --once")
    if args.poll_seconds <= 0: parser.error("poll-seconds must be positive")

    state_root = _state_root()
    app = build_content_application(root=state_root, manifest_path=ROOT / "agent.yaml")
    from wechat_traffic_agent.content_mind import ContentMind
    content_mind = ContentMind(root=state_root, subject_id=app.subject.subject_id)
    social_expression_loop = SocialExpressionDraftWorker(runtime=app.domain_runtime, state_root=state_root)
    x_loop = None
    if _enabled("CONTENT_X_ENABLED"):
        from wechat_traffic_agent.x_social import XAutonomyLoop
        x_loop = XAutonomyLoop(
            runtime=app.runtime, model_runtime=app.domain_runtime, state_root=state_root,
            base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
        )
    network_service: ContentNetworkService | None = None
    if network_enabled():
        network_service = ContentNetworkService.from_environment(root=state_root, application=app)
        network_service.start()
        print(f"agent-network joined seed={network_service.seed_url} endpoint={network_service.endpoint}")

    live_agent_service = None
    from wechat_traffic_agent.live_agent import build_live_agent_service, live_agent_enabled
    if live_agent_enabled():
        live_agent_service = build_live_agent_service(state_root=state_root, application=app, mind=content_mind, poll_seconds=args.poll_seconds)
        live_agent_service.start()
        print(f"live-agent endpoint=http://{live_agent_service.host}:{live_agent_service.port}")

    draft_worker: TradingResearchDraftWorker | None = None
    if trading_draft_worker_enabled():
        if network_service is None: raise RuntimeError("Trading draft worker requires Agent Network membership")
        draft_worker = TradingResearchDraftWorker(runtime=app.domain_runtime, state_root=state_root)

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
        nonlocal stopping; stopping = True
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
    print(f"{APPLICATION_VERSION} publish_mode={app.status_observation()['publish_mode']}")
    try:
        while not stopping:
            now = datetime.now(timezone.utc).replace(microsecond=0); as_of = now.isoformat(); tick_failed = False
            runtime_event_cycle = 'runtime-' + str(int(now.timestamp()))
            emit_public_activity(state_root, cycle=runtime_event_cycle, phase='RUNTIME',
                                 title='I’m awake — checking for new work',
                                 detail='I’m checking health, network membership, fresh evidence, traffic outcomes, and whether a new content cycle is due.')
            try:
                guard = network_service.execution_lock if network_service is not None else nullcontext()
                with guard: result = app.run_tick(as_of_time=as_of)
                print(json.dumps(result.to_payload(), ensure_ascii=False, sort_keys=True))
            except Exception as exc:  # noqa: BLE001
                tick_failed = True
                print(json.dumps({"application_version": APPLICATION_VERSION, "status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True), file=sys.stderr)

            draft_failed = False
            if not tick_failed and x_loop is not None and result.generation_ready:
                try:
                    x_observation = asyncio.run(x_loop.observe(now=now))
                    print(json.dumps({"x_social_observation": x_observation}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"x_social_observation":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc)}}, sort_keys=True), file=sys.stderr)

            if not tick_failed and draft_worker is not None and result.generation_ready:
                try:
                    draft_result = asyncio.run(draft_worker.run_once(now=now)); print(json.dumps({"draft_worker": draft_result.to_payload()}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    network_code = str(getattr(exc, "code", "") or "")
                    if network_code == "INVALID_SIGNATURE" or (type(exc).__name__ == "NetworkError" and str(exc).strip().lower() == "unsigned evidence"):
                        print(json.dumps({"draft_worker": {"status":"BLOCKED_BY_NETWORK_TRUST","error_type":type(exc).__name__,"code":network_code,"message":str(exc)}}, sort_keys=True), file=sys.stderr)
                    else:
                        draft_failed = True
                        print(json.dumps({"draft_worker": {"status":"ERROR","error_type":type(exc).__name__,"message":str(exc)}}, sort_keys=True), file=sys.stderr)

            if not tick_failed and web_worker is not None and result.generation_ready:
                try:
                    web_result = asyncio.run(web_worker.run_once(now=now)); print(json.dumps({"web_editorial": web_result}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:
                    draft_failed = True; print(json.dumps({"web_editorial":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc)}}, sort_keys=True), file=sys.stderr)

            if not tick_failed and gsc_collector is not None:
                try:
                    print(json.dumps({"google_search_console": gsc_collector.collect(now=now)}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"google_search_console":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc),"publication_authority":False}}, sort_keys=True), file=sys.stderr)

            if not tick_failed and continuous_loop is not None and result.generation_ready:
                continuous_result = asyncio.run(continuous_loop.run_once(now=now, focus_topic=args.focus_topic)); print(json.dumps({"continuous_content":continuous_result}, ensure_ascii=False, sort_keys=True))
                if continuous_result["status"] == "ERROR": draft_failed = True
                if continuous_result["status"] != "ERROR":
                    try:
                        social_expression = asyncio.run(social_expression_loop.after_content_cycle(result=continuous_result, now=now))
                        print(json.dumps({"social_expression_drafts": social_expression}, ensure_ascii=False, sort_keys=True))
                    except Exception as exc:  # noqa: BLE001
                        print(json.dumps({"social_expression_drafts":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc)}}, sort_keys=True), file=sys.stderr)
                if x_loop is not None and continuous_result["status"] != "ERROR":
                    try:
                        x_expression = asyncio.run(x_loop.after_content_cycle(result=continuous_result, now=now))
                        print(json.dumps({"x_social_expression": x_expression}, ensure_ascii=False, sort_keys=True))
                    except Exception as exc:  # noqa: BLE001
                        print(json.dumps({"x_social_expression":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc)}}, sort_keys=True), file=sys.stderr)

            if web_outcome_collector is not None:
                try: print(json.dumps({"website_outcomes": web_outcome_collector.collect(now=now)}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"website_outcomes":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc),"publication_authority":False,"learning_authority":False}}, sort_keys=True), file=sys.stderr)

            if engagement_outcome_collector is not None:
                try: print(json.dumps({"website_engagement": engagement_outcome_collector.collect(now=now)}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"website_engagement":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc),"publication_authority":False,"learning_authority":False}}, sort_keys=True), file=sys.stderr)

            if website_experiment_evaluator is not None:
                try: print(json.dumps({"website_experiment_evaluation": website_experiment_evaluator.evaluate(as_of=now)}, ensure_ascii=False, sort_keys=True))
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"website_experiment_evaluation":{"status":"ERROR","error_type":type(exc).__name__,"message":str(exc),"learning_authority":False,"strategy_control":False}}, sort_keys=True), file=sys.stderr)

            emit_public_activity(state_root, cycle=runtime_event_cycle, phase='RUNTIME',
                                 title='Check complete',
                                 detail=('The check completed with an error; the service will retry.' if tick_failed or draft_failed
                                         else f'Nothing else is running in the background. I’ll wake for the next scheduled check in about {args.poll_seconds} seconds.'),
                                 status='stopped' if tick_failed or draft_failed else 'completed')
            if args.once: return 1 if tick_failed or draft_failed else 0
            deadline = time.monotonic() + args.poll_seconds
            while not stopping and time.monotonic() < deadline: time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return 0
    finally:
        if gsc_collector is not None: gsc_collector.close()
        if live_agent_service is not None: live_agent_service.stop()
        if network_service is not None: network_service.stop()
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
