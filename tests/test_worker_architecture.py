from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supervisor_imports_no_business_modules():
    path = ROOT / "scripts" / "run_ferro_supervisor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name.startswith("wechat_traffic_agent") for name in imported)
    assert not any(name.startswith("agent_runtime") for name in imported)
    assert not any(name.startswith("virtualbrain") for name in imported)


def test_business_workers_do_not_construct_wakeful_runtime():
    for name in (
        "run_content_cognition_worker.py",
        "run_check_x_worker.py",
        "run_resource_review_worker.py",
        "run_check_outcomes_worker.py",
        "run_reflect_worker.py",
        "run_self_repair_worker.py",
    ):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "ContentWakefulRuntime" not in text
        assert ".core.pop_ready(" not in text


def test_neural_worker_owns_only_live_neural_runtime():
    worker = (ROOT / "scripts" / "run_ferro_neural_worker.py").read_text(
        encoding="utf-8"
    )
    runtime = (
        ROOT / "src" / "wechat_traffic_agent" / "ferro_neural_runtime.py"
    ).read_text(encoding="utf-8")

    assert "FerroNeuralRuntime" in worker
    assert "ContentWakefulRuntime" not in worker
    assert "build_content_application(" not in worker

    for forbidden in (
        "FerroBrainLab(",
        "FerroDreamEngine(",
        "ResearchDreamEngine(",
        "CrossDomainTransferLab(",
        "DevelopmentalWorldTransferLab(",
        "OpenEndedEvolution(",
        "DevelopmentalArchitectureSearch(",
    ):
        assert forbidden not in runtime


def test_neural_worker_has_independent_systemd_resource_domain():
    supervisor = (ROOT / "scripts" / "run_ferro_supervisor.py").read_text(
        encoding="utf-8"
    )
    assert '"systemd-run"' in supervisor
    assert '"--user"' in supervisor
    assert '"--scope"' in supervisor
    assert '"ferro-neural"' in supervisor


def test_only_content_worker_owns_main_application_runtime():
    content = (ROOT / "scripts" / "run_content_cognition_worker.py").read_text(encoding="utf-8")
    assert "build_content_application(" in content

    for name in (
        "run_check_x_worker.py",
        "run_resource_review_worker.py",
        "run_check_outcomes_worker.py",
        "run_reflect_worker.py",
    ):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "build_content_application(" not in text

    repair = (ROOT / "scripts" / "run_self_repair_worker.py").read_text(encoding="utf-8")
    assert 'state_root / "worker_runtime" / "self_repair"' in repair


def test_check_x_worker_owns_publication_reconciliation_capability():
    text = (ROOT / "scripts" / "run_check_x_worker.py").read_text(encoding="utf-8")
    assert "XBrowserPublisher" in text
    assert "x_browser_publisher=x_publisher" in text
    assert "build_content_application(" not in text


def test_supervisor_has_one_process_per_production_responsibility():
    text = (ROOT / "scripts" / "run_ferro_supervisor.py").read_text(encoding="utf-8")
    expected = {
        "ferro-neural",
        "content-cognition",
        "check-x",
        "resource-review",
        "check-outcomes",
        "reflect",
        "self-repair",
    }
    for worker in expected:
        assert f'"{worker}"' in text


def test_social_drafting_uses_publication_runtime_not_full_cognition():
    tree = ast.parse((ROOT / "scripts/run_content_cognition_worker.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    for name, keyword in (("XAutonomyLoop", "model_runtime"),
                          ("SocialExpressionDraftWorker", "runtime")):
        call = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == name)
        value = next(kw.value for kw in call.keywords if kw.arg == keyword)
        assert isinstance(value, ast.Name) and value.id == "publication_runtime"
    factory = next(node for node in calls if isinstance(node.func, ast.Name)
                   and node.func.id == "load_independent_ferro_cognition_runtime")
    session = next(kw.value for kw in factory.keywords if kw.arg == "session_id")
    assert session.value == "ferro-x-expression"


def test_daily_schedule_keeps_planning_after_daily_minimum_is_met():
    import importlib.util
    from datetime import datetime, timezone

    spec = importlib.util.spec_from_file_location(
        "rccw_under_test", ROOT / "scripts" / "run_content_cognition_worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    planned = []

    class Scheduler:
        def schedule(self, task, at, **kwargs):
            planned.append((task, at, kwargs))

    class Goal:
        def __init__(self, status):
            self._status = status

        def status(self, now):
            return self._status

    now = datetime(2026, 10, 2, 20, tzinfo=timezone.utc)
    next_at = datetime(2026, 10, 2, 21, tzinfo=timezone.utc)
    met = {
        "enabled": True,
        "remaining": 0,
        "next_attempt_at": next_at.isoformat(),
        "goal_id": "ferro-daily-post:2026-10-03",
    }
    module._daily_schedule(Goal(met), Scheduler(), now)
    assert len(planned) == 1
    task, at, kwargs = planned[0]
    assert task == "CONTENT_COGNITION"
    assert at == next_at
    assert kwargs["preemptible"] is False
    assert kwargs["relevant_refs"] == ("ferro-daily-post:2026-10-03",)

    planned.clear()
    disabled = dict(met, enabled=False)
    assert module._daily_schedule(Goal(disabled), Scheduler(), now) is disabled
    assert planned == []
