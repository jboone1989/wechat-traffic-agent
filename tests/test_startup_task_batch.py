from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "run_content_agent.py"


def _module():
    spec = importlib.util.spec_from_file_location("ferro_run_content_agent_test", ENTRYPOINT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request_path(root: Path) -> Path:
    return root / "application" / "startup_task_request.json"


def test_claims_repeated_allowlisted_startup_batch_and_writes_receipt(tmp_path: Path):
    module = _module()
    path = _request_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tasks = ["NEURAL_DEVELOPMENT_TRANSFER", "CHECK_X"] * 7
    path.write_text(json.dumps({
        "schema_version": "content-startup-task-batch.v1",
        "request_id": "n33-production-closure",
        "tasks": tasks,
    }), encoding="utf-8")

    claim = module._claim_startup_task_request(tmp_path)

    assert claim is not None
    assert claim["request_id"] == "n33-production-closure"
    assert claim["tasks"] == tasks
    request, running, receipt = module._startup_task_paths(tmp_path)
    assert not request.exists()
    assert running.exists()
    assert not receipt.exists()

    module._write_startup_task_receipt(
        tmp_path,
        request_id=claim["request_id"],
        tasks=claim["tasks"],
        results=[{"task": task, "status": "OK"} for task in tasks],
        status="COMPLETED",
    )

    assert not running.exists()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETED"
    assert payload["tasks"] == tasks
    assert len(payload["results"]) == 14


def test_invalid_startup_task_is_rejected_without_execution(tmp_path: Path):
    module = _module()
    path = _request_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "content-startup-task-batch.v1",
        "request_id": "bad",
        "tasks": ["SHELL"],
    }), encoding="utf-8")

    claim = module._claim_startup_task_request(tmp_path)

    assert claim is None
    request, running, receipt = module._startup_task_paths(tmp_path)
    assert not request.exists()
    assert not running.exists()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "REJECTED"
    assert payload["error"] == "ValueError"


def test_stale_running_request_blocks_automatic_replay(tmp_path: Path):
    module = _module()
    request, running, _receipt = module._startup_task_paths(tmp_path)
    request.parent.mkdir(parents=True, exist_ok=True)
    running.write_text("{}", encoding="utf-8")
    request.write_text(json.dumps({
        "schema_version": "content-startup-task-batch.v1",
        "request_id": "second",
        "tasks": ["CHECK_X"],
    }), encoding="utf-8")

    claim = module._claim_startup_task_request(tmp_path)

    assert claim is None
    assert request.exists()
    assert running.exists()
