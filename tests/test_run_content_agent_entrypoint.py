from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "run_content_agent.py"


def test_persistent_content_entrypoint_composes_frameworks_network_and_drafts():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "build_content_application" in text
    assert "ContentNetworkService" in text
    assert "network_service.start()" in text
    assert "execution_lock" in text
    assert "TradingResearchDraftWorker" in text
    assert "trading_draft_worker_enabled" in text
    assert "draft_worker.run_once" in text
    assert "BLOCKED_BY_NETWORK_TRUST" in text
    assert 'network_code in {"INVALID_SIGNATURE", "TAMPER_DETECTED"}' in text
    assert '"TAMPER_DETECTED"' in text
    assert '"UNSIGNED EVIDENCE"' in text
    assert "WebsiteExperimentEvaluator" in text
    assert "OKXOrbitPublisher" in text
    assert "okx_orbit_publisher.after_content_cycle" in text
    assert "website_experiment_evaluator.evaluate" in text
    assert 'add_argument("--once"' in text
    assert 'add_argument("--health"' in text
    assert 'add_argument("--focus-topic"' in text
    assert '--focus-topic requires --once' in text
    assert 'focus_cycle_id = f"v2-bootstrap-{int(now.timestamp())}" if args.focus_topic else None' in text
    assert 'continuous_loop.run_once(now=now, cycle_id=focus_cycle_id, focus_topic=args.focus_topic)' in text
    assert "app.status_observation()['publish_mode']" in text
    assert "build_live_agent_service" in text
    assert "live_agent_service.start()" in text
    assert "poll_seconds=args.poll_seconds" in text
