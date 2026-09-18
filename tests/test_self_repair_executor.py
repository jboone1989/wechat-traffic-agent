from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from wechat_traffic_agent.self_repair_executor import (
    RepairEdit,
    RepairRecipe,
    SelfRepairExecutor,
)


NOW = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
KIND = "TEST_BOUNDED_REPAIR"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, *, implemented: bool) -> Path:
    root = tmp_path / ("implemented" if implemented else "workspace")
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "ferro@example.invalid")
    _git(root, "config", "user.name", "Ferro Self Repair")
    (root / "tests").mkdir()
    (root / "app.py").write_text(
        "VALUE = 2\n" if implemented else "VALUE = 1\n",
        encoding="utf-8",
    )
    (root / "tests/test_app.py").write_text(
        "from pathlib import Path\n"
        "def test_value():\n"
        "    assert 'VALUE = 2' in Path('app.py').read_text()\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _recipe() -> RepairRecipe:
    return RepairRecipe(
        repair_kind=KIND,
        target_files=("app.py",),
        implementation_markers=(("app.py", "VALUE = 2"),),
        edits=(RepairEdit("app.py", "VALUE = 1", "VALUE = 2"),),
        focused_tests=("tests/test_app.py",),
    )


def _problem(state: Path, *, status: str = "REPAIR_PROPOSED") -> Path:
    path = state / "application/self_maintenance/problems/problem.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:test",
        "status": status,
        "symptom": "test failure",
        "error_type": "ValueError",
        "occurrences": 2,
        "cycles": ["v2-100", "v2-200"],
        "last_seen_at": NOW.isoformat(),
        "repair_proposal": {
            "schema_version": "repair-proposal.v1",
            "proposal_id": "repair-proposal:test",
            "status": "PROPOSED",
            "repair_kind": KIND,
            "target_files": ["app.py"],
        },
    }))
    return path


def _success(state: Path) -> None:
    result = state / "content/continuous/cycles/v2-300/result.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps({"status": "COGNITION_ONLY", "cycle": "v2-300"}))


def test_existing_deployed_repair_is_verified_only_after_tests_and_later_outcome(tmp_path):
    state = tmp_path / "state"
    workspace = _repo(tmp_path, implemented=True)
    problem_path = _problem(state)
    _success(state)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _recipe()},
        python_executable=sys.executable,
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "ADOPTED_VERIFIED"
    assert result["production_outcome"]["cycle"] == "v2-300"
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "VERIFIED"
    assert updated["repair_proposal"]["status"] == "VERIFIED"
    assert _git(workspace, "status", "--porcelain") == ""


def test_unimplemented_repair_branches_edits_tests_and_commits_locally_without_push(tmp_path):
    state = tmp_path / "state"
    workspace = _repo(tmp_path, implemented=False)
    deployed = tmp_path / "deployed"
    deployed.mkdir()
    (deployed / "app.py").write_text("VALUE = 1\n")
    problem_path = _problem(state)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=deployed,
        recipes={KIND: _recipe()},
        python_executable=sys.executable,
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "CANDIDATE_COMMITTED"
    assert result["commit"]["pushed"] is False
    assert result["commit"]["branch"].startswith("agent/repair-")
    assert "app.py" in result["commit"]["changed_paths"]
    assert "VALUE = 2" in (workspace / "app.py").read_text()
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "CANDIDATE_COMMITTED"
    assert _git(workspace, "status", "--porcelain") == ""


def test_deployed_repair_waits_for_real_outcome_instead_of_self_declaring_success(tmp_path):
    state = tmp_path / "state"
    workspace = _repo(tmp_path, implemented=True)
    problem_path = _problem(state)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _recipe()},
        python_executable=sys.executable,
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "OBSERVING_PRODUCTION"
    assert json.loads(problem_path.read_text())["status"] == "REPAIR_PROPOSED"


def test_no_push_or_deploy_capability_is_exposed_by_executor(tmp_path):
    state = tmp_path / "state"
    workspace = _repo(tmp_path, implemented=False)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=None,
        recipes={KIND: _recipe()},
        python_executable=sys.executable,
    )
    provider = executor._provider(_recipe())

    inspected = provider.invoke("code.inspect", {})

    assert inspected["push_available"] is False
    assert inspected["deploy_available"] is False
