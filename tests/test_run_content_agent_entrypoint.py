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
    assert "task_runner.run(" in text
    assert "relevant_refs=" in text
    assert "observe_development_transfer_outcome" in text
    assert "_LOCAL_NEURAL_TASKS" in text
    assert "allow_missing_required_capabilities=" in text


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



def test_restore_owner_goal_followup_runs_after_plan_initialization(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_content_agent_owner_wake_test", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Store:
        def __init__(self, state_root):
            assert state_root == tmp_path

        def reconcile_pending_semantic_upgrades(self, runtime):
            assert runtime is runtime_obj
            return []

        def active_canonical_goal_refs(self, runtime):
            assert runtime is runtime_obj
            return (
                "goal_owner",
                "mission_owner",
                "goal-proposal:owner",
            )

    class Wakeful:
        def __init__(self):
            self.calls = []

        def schedule_goal_followup(self, now, *, seconds=5, relevant_refs=()):
            self.calls.append((now, seconds, tuple(relevant_refs)))

    runtime_obj = object()
    wakeful = Wakeful()
    monkeypatch.setattr(
        "wechat_traffic_agent.operator_private_chat.OperatorConversationStore",
        Store,
    )
    now = module.datetime.now(module.timezone.utc).replace(microsecond=0)

    refs = module._restore_owner_goal_followup(
        state_root=tmp_path,
        runtime=runtime_obj,
        wakeful_runtime=wakeful,
        now=now,
    )

    assert refs == ("goal_owner", "mission_owner", "goal-proposal:owner")
    assert wakeful.calls == [
        (
            now,
            1,
            ("goal_owner", "mission_owner", "goal-proposal:owner"),
        )
    ]


def test_entrypoint_restores_owner_goal_followup_after_initial_plans():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "_restore_owner_goal_followup(" in text
    assert text.index("wakeful_runtime.ensure_initial_plans(now)") < text.index(
        "owner_goal_refs = _restore_owner_goal_followup("
    )



def test_task_result_logging_keeps_small_results_intact_and_bounds_large_results():
    spec = importlib.util.spec_from_file_location("run_content_agent_log_test", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)

    small = {"status": "OK", "task": "CHECK_X", "count": 3}
    assert module._task_result_for_log(small) == small

    large = {
        "status": "OK",
        "task": "RESOURCE_REVIEW",
        "resource_observation": {"status": "OBSERVED", "huge": ["x" * 512] * 100},
        "survival_resource_after_actions": {"status": "OK", "candidates": ["y" * 512] * 100},
    }
    summarized = module._task_result_for_log(large)

    assert summarized["status"] == "OK"
    assert summarized["task"] == "RESOURCE_REVIEW"
    assert summarized["log_truncated"] is True
    assert summarized["serialized_bytes"] > module._TASK_RESULT_LOG_MAX_BYTES
    assert summarized["sections"]["resource_observation"]["status"] == "OBSERVED"
    assert summarized["sections"]["survival_resource_after_actions"]["status"] == "OK"
    assert "huge" not in json.dumps(summarized)
    assert len(json.dumps(summarized).encode("utf-8")) < module._TASK_RESULT_LOG_MAX_BYTES


def test_autonomous_goal_settlement_reenters_capability_contracts():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "observe_autonomous_goal_outcome(" in text
    assert "task_runner.observe_settled_capability_outcome(" in text
    assert text.index("observe_autonomous_goal_outcome(") < text.index(
        "task_runner.observe_settled_capability_outcome("
    )
    assert "autonomous_goal_capability_contract" in text


def test_entrypoint_does_not_double_ingest_completed_task_outcome():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    # WakefulContentTaskRunner._finalize_task_result owns normal task
    # self-maintenance. The supervisor only records exceptions.
    assert "maintenance.ingest_task_outcome(task, task_result, now=now)" not in text


def test_entrypoint_binds_rsi_worker_before_runtime_capability_composition():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    section = text.split("def _run_initialized_agent", 1)[1]
    workspace_bind = 'os.environ.setdefault("PI_REMOTE_REPAIR_REPO_ROOT", repair_workspace_raw)'
    python_bind = 'os.environ.setdefault("PI_REMOTE_REPAIR_PYTHON", sys.executable)'
    build = "app = build_content_application("
    assert workspace_bind in section
    assert python_bind in section
    assert section.index(workspace_bind) < section.index(build)
    assert section.index(python_bind) < section.index(build)


def test_startup_wires_x_publisher_against_its_real_constructor(tmp_path):
    import ast
    from types import SimpleNamespace
    from wechat_traffic_agent.x_browser import XBrowserPublisher

    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    assignments = [node for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "x_browser_publisher"
            for target in node.targets)]
    assert len(assignments) == 1
    async def baseline_observer(**kwargs):
        return {"status": "RECORDED"}
    namespace = {"XBrowserPublisher": XBrowserPublisher, "state_root": tmp_path,
        "x_growth_mission": None, "x_loop": SimpleNamespace(
            capture_mission_publication_baseline=baseline_observer)}
    module = ast.Module(body=assignments, type_ignores=[])
    exec(compile(module, str(ENTRYPOINT), "exec"), namespace)
    publisher = namespace["x_browser_publisher"]
    assert publisher.state_root == tmp_path
    assert publisher.mission_baseline_observer is baseline_observer
