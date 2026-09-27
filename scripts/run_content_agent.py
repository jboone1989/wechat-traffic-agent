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


_ONE_SHOT_TASKS = (
    "OBSERVE_WORLD",
    "RESOURCE_REVIEW",
    "CHECK_OUTCOMES",
    "CONTENT_COGNITION",
    "REFLECT",
    "CHECK_X",
    "NEURAL_INTEROCEPTION",
    "NEURAL_PLAY",
    "NEURAL_DREAM",
    "NEURAL_TRANSFER",
    "NEURAL_DEVELOPMENT",
    "NEURAL_DEVELOPMENT_TRANSFER",
    "NEURAL_EVOLUTION_CONSOLIDATION",
)

_LOCAL_NEURAL_TASKS = frozenset({
    "NEURAL_INTEROCEPTION",
    "NEURAL_PLAY",
    "NEURAL_DREAM",
    "NEURAL_TRANSFER",
    "NEURAL_DEVELOPMENT",
    "NEURAL_DEVELOPMENT_TRANSFER",
    "NEURAL_EVOLUTION_CONSOLIDATION",
})

_DOMAIN_EXPERIENCE_TASKS = frozenset({
    "OBSERVE_WORLD",
    "RESOURCE_REVIEW",
    "CHECK_OUTCOMES",
    "VERIFY_PREDICTION",
    "CONTENT_COGNITION",
    "CHECK_X",
})

_TASK_RESULT_LOG_MAX_BYTES = 16 * 1024
_TASK_RESULT_LOG_SUMMARY_FIELDS = (
    "status",
    "task",
    "cycle",
    "error_type",
    "message",
    "mission_id",
    "experiment_id",
    "owner_goal_id",
)


def _task_result_for_log(result: dict) -> dict:
    """Keep stdout bounded without changing the durable task result."""
    payload = dict(result or {})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) <= _TASK_RESULT_LOG_MAX_BYTES:
        return payload

    summary: dict[str, object] = {
        "status": str(payload.get("status") or "UNKNOWN"),
        "log_truncated": True,
        "serialized_bytes": len(encoded),
        "top_level_keys": sorted(str(key) for key in payload.keys())[:64],
    }
    for field in _TASK_RESULT_LOG_SUMMARY_FIELDS:
        value = payload.get(field)
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            summary[field] = value

    sections: dict[str, dict[str, object]] = {}
    for key in sorted(payload.keys(), key=str)[:32]:
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        section = {
            field: value.get(field)
            for field in _TASK_RESULT_LOG_SUMMARY_FIELDS
            if value.get(field) is not None
            and not isinstance(value.get(field), (dict, list, tuple, set))
        }
        if section:
            sections[str(key)] = section
    if sections:
        summary["sections"] = sections
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="persistent Content Agent")
    parser.add_argument("--once", action="store_true", help="Run one content cognition tick and exit")
    parser.add_argument("--guardian-canary", action="store_true", help="Start an isolated, no-production-credentials Guardian canary runtime")
    parser.add_argument(
        "--task",
        choices=_ONE_SHOT_TASKS,
        help="Run exactly one allowlisted wakeful task through the production task runner and exit",
    )
    parser.add_argument("--health", action="store_true", help="Print persisted application health")
    parser.add_argument("--focus-topic", help="One-shot content focus slug; requires --once and does not replace the persisted autonomous goal")
    parser.add_argument("--watchdog-seconds", type=int, default=1800, help="Low-frequency liveness watchdog; never a cognition cadence")
    return parser


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _restore_owner_goal_followup(
    *,
    state_root: Path,
    runtime,
    wakeful_runtime,
    now: datetime,
) -> tuple[str, ...]:
    """Restore durable owner-goal wake provenance after wake plans are initialized."""
    from wechat_traffic_agent.operator_private_chat import OperatorConversationStore

    store = OperatorConversationStore(state_root)
    store.reconcile_pending_semantic_upgrades(runtime)
    refs = store.active_canonical_goal_refs(runtime)
    if refs:
        wakeful_runtime.schedule_goal_followup(
            now,
            seconds=1,
            relevant_refs=refs,
        )
    return refs


def _shutdown_task_summary(task: str | None, result: dict | None) -> dict | None:
    if task is None:
        return None
    result = dict(result or {})
    return {
        "status": "SHUTDOWN_CHECKPOINT",
        "task": task,
        "reason": "SIGTERM_OR_SIGINT",
        "interrupted_status": str(result.get("status") or "UNKNOWN"),
        "interrupted_keys": sorted(str(key) for key in result.keys())[:24],
    }


def _write_shutdown_checkpoint(state_root: Path, payload: dict) -> None:
    path = Path(state_root) / "application" / "shutdown_checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)




_STARTUP_TASK_REQUEST_SCHEMA = "content-startup-task-batch.v1"


def _startup_task_paths(state_root: Path) -> tuple[Path, Path, Path]:
    application = Path(state_root) / "application"
    return (
        application / "startup_task_request.json",
        application / "startup_task_request.running.json",
        application / "startup_task_request.receipt.json",
    )


def _write_startup_task_receipt(
    state_root: Path,
    *,
    request_id: str,
    tasks: list[str],
    results: list[dict],
    status: str,
    error: str | None = None,
) -> None:
    _request, running, receipt = _startup_task_paths(state_root)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _STARTUP_TASK_REQUEST_SCHEMA,
        "request_id": request_id,
        "status": status,
        "tasks": list(tasks),
        "results": [dict(row) for row in results],
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "error": error,
    }
    tmp = receipt.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(receipt)
    running.unlink(missing_ok=True)


def _recover_stale_startup_task_request(
    state_root: Path,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Archive a startup batch left RUNNING by a previous process.

    This is called exactly once during process initialization, before this
    process can claim a startup batch. A pre-existing .running file therefore
    belongs to an interrupted predecessor and must not permanently block newer
    operator work. The interrupted batch is preserved as evidence and is never
    replayed automatically.
    """
    _request, running, _receipt = _startup_task_paths(state_root)
    if not running.exists():
        return None
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    archive = running.with_name(f"startup_task_request.interrupted-{stamp}.json")
    suffix = 1
    while archive.exists():
        archive = running.with_name(
            f"startup_task_request.interrupted-{stamp}-{suffix}.json"
        )
        suffix += 1
    running.replace(archive)
    return archive


def _claim_startup_task_request(state_root: Path) -> dict | None:
    request, running, _receipt = _startup_task_paths(state_root)
    if running.exists() or not request.exists():
        return None
    running.parent.mkdir(parents=True, exist_ok=True)
    request.replace(running)
    try:
        payload = json.loads(running.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("startup task request must be an object")
        if payload.get("schema_version") != _STARTUP_TASK_REQUEST_SCHEMA:
            raise ValueError("unsupported startup task request schema")
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id or len(request_id) > 128:
            raise ValueError("startup task request_id is required and bounded")
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 32:
            raise ValueError("startup task batch must contain 1..32 tasks")
        normalized = [str(task) for task in tasks]
        if any(task not in _ONE_SHOT_TASKS for task in normalized):
            raise ValueError("startup task batch contains a non-allowlisted task")
        return {
            "request_id": request_id,
            "tasks": normalized,
            "claimed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _write_startup_task_receipt(
            state_root,
            request_id="invalid",
            tasks=[],
            results=[],
            status="REJECTED",
            error=type(exc).__name__,
        )
        return None

def _run_guardian_canary() -> int:
    """Start the smallest real Ferro runtime suitable for external Guardian health checks."""
    nonce = os.environ.get("FERRO_GUARDIAN_HEALTH_NONCE", "").strip()
    if not nonce:
        raise ValueError("FERRO_GUARDIAN_HEALTH_NONCE is required for guardian canary")
    raw_root = os.environ.get("FERRO_CANDIDATE_STATE_ROOT", "").strip()
    if not raw_root:
        raise ValueError("FERRO_CANDIDATE_STATE_ROOT is required for guardian canary")
    state_root = Path(raw_root).expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("FERRO_CANDIDATE_PORT", "18103"))
    if port <= 0 or port > 65535:
        raise ValueError("FERRO_CANDIDATE_PORT is invalid")

    app = build_content_application(
        root=state_root,
        manifest_path=ROOT / "agent.yaml",
        allow_missing_required_capabilities=True,
    )
    service = ContentNetworkService(
        root=state_root,
        application=app,
        seed_urls=("http://127.0.0.1:1/a2a",),
        bind_host="127.0.0.1",
        advertise_host="127.0.0.1",
        port=port,
        startup_timeout_seconds=10.0,
        join_retry_seconds=0.5,
    )
    stopping = False

    def _stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        service.request_stop()

    previous_term = signal.signal(signal.SIGTERM, _stop)
    previous_int = signal.signal(signal.SIGINT, _stop)
    try:
        service.start()
        print(json.dumps({
            "guardian_canary": {
                "status": "READY",
                "health_url": f"http://127.0.0.1:{port}/guardian-health",
                "state_root": str(state_root),
                "production_credentials_inherited": False,
            }
        }, sort_keys=True), flush=True)
        while not stopping:
            time.sleep(0.1)
        return 0
    finally:
        try:
            service.stop()
        finally:
            app.close()
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)


def main() -> int:
    parser = _parser(); args = parser.parse_args()
    if args.health: return _show_health()
    if args.guardian_canary:
        if args.once or args.task or args.focus_topic:
            parser.error("--guardian-canary cannot be combined with normal task execution")
        return _run_guardian_canary()
    if args.once and args.task:
        parser.error("--once and --task are mutually exclusive")
    if args.focus_topic and not (args.once or args.task == "CONTENT_COGNITION"):
        parser.error("--focus-topic requires --once or --task CONTENT_COGNITION")
    if args.watchdog_seconds < 60: parser.error("watchdog-seconds must be at least 60")

    state_root = _state_root()
    from wechat_traffic_agent.guardian_heartbeat import FerroHeartbeat
    heartbeat = FerroHeartbeat(state_root)
    terminal_status = "FAILED"
    try:
        heartbeat.start()
        result = _run_initialized_agent(args, state_root, heartbeat)
        terminal_status = "STOPPED" if result == 0 else "FAILED"
        return result
    finally:
        heartbeat.stop(status=terminal_status)


def _run_initialized_agent(args, state_root: Path, heartbeat) -> int:
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
    app = build_content_application(
        root=state_root,
        manifest_path=ROOT / "agent.yaml",
        allow_missing_required_capabilities=(
            bool(args.task) and str(args.task) in _LOCAL_NEURAL_TASKS
        ),
    )
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
    from wechat_traffic_agent.model_reputation import ExternalModelReputationObserver
    model_reputation = ExternalModelReputationObserver(state_root=state_root)
    from wechat_traffic_agent.nvidia_catalog import NvidiaCapabilityCatalog
    nvidia_catalog = NvidiaCapabilityCatalog(state_root=state_root)
    from wechat_traffic_agent.capability_evolution import ContentCapabilityEvolution
    capability_evolution = ContentCapabilityEvolution(
        state_root=state_root,
        capability_runtime=app.runtime,
    )
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
    daily_reflection = DailyReflectionService(
        state_root=state_root,
        mind=content_mind,
        runtime=app.domain_runtime,
        notifier=private_notifier,
    )
    from wechat_traffic_agent.self_maintenance import SelfMaintenanceService
    self_maintenance = SelfMaintenanceService(
        state_root=state_root,
        evolution_runtime=app.domain_runtime,
        capability_runtime=app.runtime,
    )
    self_maintenance_now = datetime.now(timezone.utc).replace(microsecond=0)
    self_maintenance_backfill = self_maintenance.backfill_recent_failures(
        now=self_maintenance_now
    )
    if self_maintenance_backfill.get("recorded"):
        print(json.dumps({"self_maintenance_backfill": self_maintenance_backfill}, sort_keys=True))
    self_maintenance_reindex = self_maintenance.reindex_legacy_problems_by_callsite(
        now=self_maintenance_now
    )
    if self_maintenance_reindex.get("legacy_rows"):
        print(json.dumps({"self_maintenance_reindex": self_maintenance_reindex}, sort_keys=True))
    self_maintenance_reconcile = self_maintenance.reconcile_legacy_duplicates(
        now=self_maintenance_now
    )
    if self_maintenance_reconcile.get("archived_duplicates"):
        print(json.dumps({"self_maintenance_reconcile": self_maintenance_reconcile}, sort_keys=True))
    self_maintenance_review = self_maintenance.review()
    if (
        self_maintenance_review.get("repair_requested_count")
        or self_maintenance_review.get("repair_proposed_count")
    ):
        print(json.dumps({"self_maintenance_review": self_maintenance_review}, sort_keys=True))
    from wechat_traffic_agent.self_repair_executor import SelfRepairExecutor
    repair_workspace_raw = os.environ.get("CONTENT_SELF_REPAIR_WORKSPACE", "").strip()
    repair_test_pythonpath = os.environ.get(
        "CONTENT_SELF_REPAIR_TEST_PYTHONPATH", ""
    ).strip()
    self_repair = SelfRepairExecutor(
        state_root=state_root,
        workspace_root=Path(repair_workspace_raw) if repair_workspace_raw else None,
        deployed_root=ROOT,
        python_executable=sys.executable,
        planner_runtime=app.domain_runtime,
        test_pythonpath=repair_test_pythonpath,
    )
    from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime, WakefulState
    wakeful_runtime = ContentWakefulRuntime(state_root=state_root, content_interval_hours=int(os.environ.get("CONTENT_CONTINUOUS_INTERVAL_HOURS", "6")), watchdog_seconds=args.watchdog_seconds)
    from wechat_traffic_agent.cognition_context import bind_ferro_cognition_context
    bind_ferro_cognition_context(
        application=app,
        mind=content_mind,
        wakeful=wakeful_runtime,
    )
    try:
        from wechat_traffic_agent.public_persona import ensure_public_persona
        persona = asyncio.run(ensure_public_persona(runtime=app.domain_runtime, state_root=state_root))
        print(json.dumps({"public_persona": {"status": "ACTIVE", "name": persona.name, "affiliation": persona.affiliation}}, ensure_ascii=False))
    except Exception as exc:  # identity bootstrap must not take the agent offline
        print(json.dumps({"public_persona": {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "message": str(exc)[:300]}}, ensure_ascii=False), file=sys.stderr)
    social_expression_loop = SocialExpressionDraftWorker(runtime=app.domain_runtime, state_root=state_root)
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
            runtime=app.runtime, model_runtime=app.domain_runtime, state_root=state_root,
            base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
            growth_mission=x_growth_mission,
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

    content_evolution_bridge = None
    web_base_url = os.environ.get("CONTENT_WEB_BASE_URL", "").strip()
    if web_base_url and _enabled("CONTENT_CONTINUOUS_ENABLED"):
        from wechat_traffic_agent.evolution_bridge import ContentEvolutionBridge
        content_evolution_bridge = ContentEvolutionBridge(
            runtime=app.domain_runtime, state_root=state_root, account=web_base_url
        )
        try:
            evolution_bootstrap = content_evolution_bridge.maybe_bootstrap_historical_learning(
                as_of=datetime.now(timezone.utc).replace(microsecond=0)
            )
            print(json.dumps({"evolution_engine_bootstrap": evolution_bootstrap}, ensure_ascii=False, sort_keys=True))
        except Exception as exc:
            print(json.dumps({
                "evolution_engine_bootstrap": {
                    "status": "DEGRADED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            }, ensure_ascii=False, sort_keys=True), file=sys.stderr)

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
            website_experiments=_enabled("CONTENT_WEBSITE_EXPERIMENTS_ENABLED"),
            neural_runtime=wakeful_runtime, evolution_bridge=content_evolution_bridge)

    stopping = False
    stop_requested_at: datetime | None = None

    def _stop(_signum, _frame):
        nonlocal stopping, stop_requested_at
        if stopping:
            return
        stopping = True
        stop_requested_at = datetime.now(timezone.utc).replace(microsecond=0)
        wakeful_runtime.core.interrupt()
        if live_agent_service is not None:
            live_agent_service.request_stop()
        if network_service is not None:
            network_service.request_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
    task_runner = WakefulContentTaskRunner(
        app=app,
        state_root=state_root,
        content_mind=content_mind,
        resource_economy=resource_economy,
        resource_scout=resource_scout,
        model_reputation=model_reputation,
        nvidia_catalog=nvidia_catalog,
        capability_evolution=capability_evolution,
        daily_reflection=daily_reflection,
        self_maintenance=self_maintenance,
        self_repair=self_repair,
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
        evolution_bridge=content_evolution_bridge,
        wakeful=wakeful_runtime,
        resource_regulator=wakeful_runtime.resource_regulator,
        web_base_url=os.environ.get("CONTENT_WEB_BASE_URL", ""),
        stop_requested=lambda: stopping,
    )
    recovered_startup_request = _recover_stale_startup_task_request(state_root)
    if recovered_startup_request is not None:
        print(json.dumps({
            "startup_task_recovery": {
                "status": "INTERRUPTED_BATCH_ARCHIVED",
                "archive": recovered_startup_request.name,
            }
        }, sort_keys=True))
    startup_request = _claim_startup_task_request(state_root)
    startup_tasks = list(startup_request.get("tasks") or ()) if startup_request else []
    startup_results: list[dict] = []
    print(f"{APPLICATION_VERSION} publish_mode={app.status_observation()['publish_mode']}")
    active_task: str | None = None
    active_cycle: str | None = None
    shutdown_task_result: dict | None = None
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        wakeful_runtime.ensure_initial_plans(now)
        try:
            owner_goal_refs = _restore_owner_goal_followup(
                state_root=state_root,
                runtime=app.domain_runtime,
                wakeful_runtime=wakeful_runtime,
                now=now,
            )
            if owner_goal_refs:
                print(json.dumps({
                    "owner_goal_followup": {
                        "status": "RESTORED",
                        "ref_count": len(owner_goal_refs),
                    }
                }, sort_keys=True))
        except Exception as exc:  # owner-goal restoration must not take Ferro offline
            print(json.dumps({
                "owner_goal_followup": {
                    "status": "DEGRADED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            }, sort_keys=True), file=sys.stderr)
        wakeful_runtime.refresh_neural_life(now)
        if startup_tasks:
            reason_task = startup_tasks.pop(0)
            wake_reason = None
        elif args.once or args.task:
            reason_task = args.task or "CONTENT_COGNITION"
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
            active_task = task
            heartbeat.task = task
            active_cycle = runtime_event_cycle
            wakeful_runtime.core.set_state(
                WakefulState.THINKING if task in {"CONTENT_COGNITION", "OBSERVE_WORLD"} else WakefulState.OBSERVING
            )
            emit_public_activity(
                state_root, cycle=runtime_event_cycle, phase=task,
                title=f"Awake · {task.replace('_', ' ').title()}",
                detail=(
                    wake_reason.detail
                    if wake_reason is not None
                    else "Startup task batch"
                    if startup_request is not None
                    else "One-shot content cognition"
                ),
            )
            if task == "EVENT_OBSERVE":
                task_result = {"task": task, "status": "NO_DEEP_COGNITION_JUSTIFIED"}
            else:
                try:
                    task_result = task_runner.run(
                        task,
                        now=now,
                        focus_topic=args.focus_topic,
                        relevant_refs=(
                            tuple(wake_reason.relevant_refs)
                            if wake_reason is not None
                            else ()
                        ),
                    )
                except Exception as exc:
                    maintenance = getattr(task_runner, "self_maintenance", None)
                    if maintenance is not None:
                        try:
                            maintenance.ingest_task_outcome(task, {
                                "status": "ERROR", "error_type": type(exc).__name__,
                                "cycle": runtime_event_cycle,
                            }, now=now)
                        except Exception:
                            pass  # Preserve the original task failure for the supervisor.
                    raise
            maintenance = getattr(task_runner, "self_maintenance", None)
            if maintenance is not None:
                try:
                    maintenance.ingest_task_outcome(task, task_result, now=now)
                except Exception:
                    pass  # Diagnosis cannot hide a completed task result.
            try:
                reflection_schedule = wakeful_runtime.schedule_reflection_from_outcome(
                    task,
                    task_result,
                    now,
                )
                if reflection_schedule.get("status") == "SCHEDULED":
                    print(json.dumps({
                        "reflection_followup": reflection_schedule,
                    }, ensure_ascii=False, sort_keys=True))
            except Exception as exc:
                print(json.dumps({
                    "reflection_followup": {
                        "status": "DEGRADED",
                        "error_type": type(exc).__name__,
                    }
                }, sort_keys=True), file=sys.stderr)
            print(json.dumps({
                "wake_reason": wake_reason.__dict__ if wake_reason else {"kind": "ONCE"},
                "task_result": _task_result_for_log(task_result),
            }, ensure_ascii=False, sort_keys=True))
            status = str(task_result.get("status") or "UNKNOWN")
            if startup_request is not None:
                startup_results.append({
                    "task": task,
                    "status": status,
                    "observed_at": now.isoformat(),
                })
            wake_refs = tuple(wake_reason.relevant_refs) if wake_reason is not None else ()
            transfer_learning = wakeful_runtime.observe_transfer_outcome(
                task=task,
                result=task_result,
                experiment_id=next((ref for ref in wake_refs if ref.startswith("transfer:")), None),
                observed_at=now,
            )
            if transfer_learning is not None:
                print(json.dumps({"neural_transfer_learning": transfer_learning}, ensure_ascii=False, sort_keys=True))
            development_transfer = wakeful_runtime.observe_development_transfer_outcome(
                task=task,
                result=task_result,
                trial_id=next((ref for ref in wake_refs if ref.startswith("development-transfer:")), None),
                observed_at=now,
            )
            if development_transfer is not None:
                print(json.dumps(
                    {"developmental_world_transfer_learning": development_transfer},
                    ensure_ascii=False,
                    sort_keys=True,
                ))
            motor_learning = wakeful_runtime.observe_motor_outcome(
                task=task,
                result=task_result,
                decision_id=next((ref for ref in wake_refs if ref.startswith("motor-decision:")), None),
                observed_at=now,
            )
            if motor_learning is not None:
                print(json.dumps({"neural_motor_learning": motor_learning}, ensure_ascii=False, sort_keys=True))
            autonomous_goal = wakeful_runtime.observe_autonomous_goal_outcome(
                task=task,
                result=task_result,
                goal_id=next((ref for ref in wake_refs if ref.startswith("autonomous-goal:")), None),
                observed_at=now,
            )
            if autonomous_goal is not None:
                print(json.dumps({"autonomous_goal_outcome": autonomous_goal}, ensure_ascii=False, sort_keys=True))
            if task in _DOMAIN_EXPERIENCE_TASKS:
                domain_goal_ref = next(
                    (
                        ref
                        for ref in wake_refs
                        if ref.startswith("autonomous-goal:")
                        or ref.startswith("goal_")
                    ),
                    None,
                )
                try:
                    domain_experience = app.observe_domain_task_result(
                        task=task,
                        result=task_result,
                        observed_at=now,
                        evidence_refs=wake_refs,
                        goal_id=domain_goal_ref,
                    )
                    print(json.dumps(
                        {"domain_runtime_experience": domain_experience},
                        ensure_ascii=False,
                        sort_keys=True,
                    ))
                except Exception as exc:
                    print(json.dumps({
                        "domain_runtime_experience": {
                            "status": "DEGRADED",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:300],
                        }
                    }, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            if stopping or status == "SHUTDOWN_CHECKPOINT":
                shutdown_task_result = _shutdown_task_summary(task, task_result)
                emit_public_activity(
                    state_root,
                    cycle=runtime_event_cycle,
                    phase="SHUTDOWN",
                    title="Awake · Shutdown checkpoint",
                    detail=f"Stopping safely during {task}; persisted work remains resumable.",
                    status="completed",
                )
                break
            if task != "NEURAL_INTEROCEPTION":
                wakeful_runtime.refresh_neural_life(now)
            wakeful_runtime.sync_prediction_wakes(now)
            if (
                task not in {"EVENT_OBSERVE", "NEURAL_INTEROCEPTION"}
                and status != "WAITING_FOR_CHATGPT"
            ):
                wakeful_runtime.core.mark_action(now)
            if wake_reason is not None and wake_reason.kind == "PLAN" and reason_task:
                wakeful_runtime.schedule_next(reason_task, now)
            wakeful_runtime.core.set_state(WakefulState.WAITING)
            next_state = wakeful_runtime.public_snapshot()
            emit_public_activity(
                state_root, cycle=runtime_event_cycle, phase="WAITING",
                title="Awake · Waiting",
                detail=(
                    (
                        "A durable ChatGPT cognition request is pending; "
                        "this cycle is suspended and will resume the same request. "
                    )
                    if status == "WAITING_FOR_CHATGPT"
                    else "No deeper cognition is currently justified. "
                ) + (
                    f"Next intentional wake: {next_state.get('next_wake_at') or 'event-driven'} · "
                    f"{next_state.get('next_wake_reason') or 'external stimulus'}"
                ),
                status="completed" if status not in {"ERROR", "DEGRADED"} else "stopped",
            )
            if startup_request is not None:
                if status in {"ERROR", "DEGRADED"}:
                    _write_startup_task_receipt(
                        state_root,
                        request_id=str(startup_request["request_id"]),
                        tasks=list(startup_request["tasks"]),
                        results=startup_results,
                        status="FAILED",
                        error=f"{task}:{status}",
                    )
                    return 1
                if startup_tasks:
                    active_task = None
                    active_cycle = None
                    reason_task = startup_tasks.pop(0)
                    wake_reason = None
                    continue
                _write_startup_task_receipt(
                    state_root,
                    request_id=str(startup_request["request_id"]),
                    tasks=list(startup_request["tasks"]),
                    results=startup_results,
                    status="COMPLETED",
                )
                return 0
            if args.once or args.task:
                return 1 if status in {"ERROR", "DEGRADED"} else 0
            active_task = None
            active_cycle = None
            wake_reason = wakeful_runtime.wait(stopping=lambda: stopping)
            reason_task = wake_reason.task if wake_reason.task else None

        return 0
    finally:
        if stop_requested_at is not None:
            _write_shutdown_checkpoint(state_root, {
                "schema_version": "content-agent-shutdown:v1",
                "status": "TEARDOWN_STARTED",
                "requested_at": stop_requested_at.isoformat(),
                "checkpointed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "active_task": active_task,
                "active_cycle": active_cycle,
                "task_result": shutdown_task_result,
            })
        if gsc_collector is not None:
            gsc_collector.close()
        if live_agent_service is not None:
            live_agent_service.stop()
        if network_service is not None:
            network_service.stop()
        app.close()
        if stop_requested_at is not None:
            _write_shutdown_checkpoint(state_root, {
                "schema_version": "content-agent-shutdown:v1",
                "status": "STOPPED",
                "requested_at": stop_requested_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "active_task": active_task,
                "active_cycle": active_cycle,
                "task_result": shutdown_task_result,
            })


if __name__ == "__main__":
    raise SystemExit(main())
