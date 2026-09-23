import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_one_shot_task_cli_is_bounded_and_uses_normal_runner():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert '"--task"' in text
    assert "choices=_ONE_SHOT_TASKS" in text
    assert '"NEURAL_DEVELOPMENT_TRANSFER"' in text
    assert '"CHECK_X"' in text
    assert 'if args.once and args.task:' in text
    assert 'reason_task = args.task or "CONTENT_COGNITION"' in text
    assert 'if args.once or args.task:' in text
    assert "task_runner.run(task, now=now" in text
    assert "observe_development_transfer_outcome" in text


def test_heartbeat_records_failure_when_startup_raises(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_content_agent_under_test", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_content_agent.py"])

    def fail_initialization(*_args):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(module, "_run_initialized_agent", fail_initialization)
    with pytest.raises(RuntimeError, match="startup failed"):
        module.main()

    pulse = json.loads((tmp_path / "application" / "ferro-heartbeat.json").read_text())
    assert pulse["status"] == "FAILED"
