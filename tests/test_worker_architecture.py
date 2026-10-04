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


def test_supervisor_has_one_process_per_production_responsibility():
    text = (ROOT / "scripts" / "run_ferro_supervisor.py").read_text(encoding="utf-8")
    expected = {
        "ferro-state",
        "content-cognition",
        "check-x",
        "resource-review",
        "check-outcomes",
        "reflect",
        "self-repair",
    }
    for worker in expected:
        assert f'"{worker}"' in text
