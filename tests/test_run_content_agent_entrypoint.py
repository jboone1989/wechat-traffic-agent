from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "run_content_agent.py"
TASKS = ROOT / "src" / "wechat_traffic_agent" / "wakeful_tasks.py"


def test_persistent_content_entrypoint_uses_wakeful_core_not_fixed_polling():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    tasks = TASKS.read_text(encoding="utf-8")
    assert "build_content_application" in text
    assert "ContentWakefulRuntime" in text
    assert "WakefulContentTaskRunner" in text
    assert "wakeful_runtime.wait" in text
    assert 'add_argument("--watchdog-seconds"' in text
    assert "args.poll_seconds" not in text
    assert "sleep(300)" not in text
    assert "time.monotonic() + args.poll_seconds" not in text
    assert "CONTENT_COGNITION" in text
    assert "RESOURCE_REVIEW" in tasks
    assert "CHECK_OUTCOMES" in tasks
    assert "CHECK_X" in tasks
    assert "OBSERVE_WORLD" in tasks


def test_network_is_capability_not_life_dependency_and_existing_actions_remain():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    tasks = TASKS.read_text(encoding="utf-8")
    assert "network_service.start()" in text
    assert "agent_network" in text and "UNAVAILABLE" in text
    assert "DISABLED_NETWORK_UNAVAILABLE" in text
    assert "live_agent_service.start()" in text
    assert text.index("live_agent_service.start()") < text.index("network_service.start()")
    assert "execution_lock" in tasks
    assert "draft_worker.run_once" in tasks
    assert "BLOCKED_BY_NETWORK_TRUST" in tasks
    assert '"UNSIGNED EVIDENCE"' in tasks
    assert "website_experiment_evaluator.evaluate" in tasks
    assert "okx_orbit_publisher" in text
    assert "x_browser_publisher" in text
    assert "continuous_loop.run_once" in tasks
    assert "web_worker.run_once" in tasks
    assert "stop_requested=lambda: stopping" in text
    assert "SHUTDOWN_CHECKPOINT" in tasks
    assert "shutdown_checkpoint.json" in text
    assert "live_agent_service.request_stop()" in text
    assert "network_service.request_stop()" in text
    assert "app.status_observation()['publish_mode']" in text
