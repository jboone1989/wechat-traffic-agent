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
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "OBSERVING_PRODUCTION"
    assert updated["repair_proposal"]["status"] == "OBSERVING_PRODUCTION"


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



def test_new_repair_proposal_is_selected_before_observing_problem(tmp_path):
    state = tmp_path / "state"
    root = state / "application/self_maintenance/problems"
    root.mkdir(parents=True)
    (root / "observing.json").write_text(json.dumps({
        "problem_id": "self-problem:observing",
        "status": "OBSERVING_PRODUCTION",
        "symptom": "old",
        "error_type": "ValueError",
        "occurrences": 99,
        "last_seen_at": "2026-09-19T00:00:00+00:00",
        "cycles": ["v2-100"],
        "repair_proposal": {
            "proposal_id": "repair-proposal:old",
            "status": "OBSERVING_PRODUCTION",
            "repair_kind": KIND,
        },
    }))
    (root / "proposed.json").write_text(json.dumps({
        "problem_id": "self-problem:proposed",
        "status": "REPAIR_PROPOSED",
        "symptom": "new",
        "error_type": "ValueError",
        "occurrences": 1,
        "last_seen_at": "2026-09-18T00:00:00+00:00",
        "cycles": ["v2-200"],
        "repair_proposal": {
            "proposal_id": "repair-proposal:new",
            "status": "PROPOSED",
            "repair_kind": KIND,
        },
    }))
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=None,
        deployed_root=None,
        recipes={KIND: _recipe()},
        python_executable=sys.executable,
    )

    selected = executor._next_problem()

    assert selected["problem_id"] == "self-problem:proposed"



def _regression_repo(tmp_path: Path) -> Path:
    root = tmp_path / "regression-workspace"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "ferro@example.invalid")
    _git(root, "config", "user.name", "Ferro Self Repair")
    (root / "tests").mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests/test_focused.py").write_text(
        "def test_focused():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (root / "tests/test_regression.py").write_text(
        "from pathlib import Path\n"
        "def test_original_contract():\n"
        "    assert 'VALUE = 1' in Path('app.py').read_text()\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _regression_recipe() -> RepairRecipe:
    return RepairRecipe(
        repair_kind=KIND,
        target_files=("app.py",),
        implementation_markers=(("app.py", "VALUE = 2"),),
        edits=(RepairEdit("app.py", "VALUE = 1", "VALUE = 2"),),
        focused_tests=("tests/test_focused.py",),
    )


def test_failed_candidate_becomes_counterevidence_and_restores_clean_baseline(tmp_path):
    state = tmp_path / "state-counterevidence"
    workspace = _regression_repo(tmp_path)
    problem_path = _problem(state)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _regression_recipe()},
        python_executable=sys.executable,
    )
    baseline_sha = _git(workspace, "rev-parse", "HEAD")

    result = executor.run_once(now=NOW)

    assert result["status"] == "CANDIDATE_REJECTED_REPLAN_QUEUED"
    assert result["trial_status"] == "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED"
    assert result["baseline_status"] == "PASSED"
    assert any(
        "tests/test_regression.py::test_original_contract" in row
        for row in result["counterevidence"]["failed_tests"]
    )
    assert _git(workspace, "rev-parse", "HEAD") == baseline_sha
    assert _git(workspace, "status", "--porcelain") == ""
    assert _git(workspace, "branch", "--show-current") == ""
    assert (workspace / "app.py").read_text() == "VALUE = 1\n"

    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "DIAGNOSING"
    assert updated["repair_proposal"] is None
    assert updated["planner_attempt"]["status"] == "COUNTEREVIDENCE_READY"
    assert updated["planner_attempt"]["planner_version"] == "generated-repair.v6"
    assert updated["repair_trial"]["baseline_status"] == "PASSED"
    assert updated["repair_trial"]["previous_hypothesis"] is None
    assert any(
        "tests/test_regression.py::test_original_contract" in row
        for row in updated["repair_trial"]["failed_tests"]
    )


def test_existing_quarantined_trial_is_migrated_to_counterevidence_without_reapplying_edit(tmp_path):
    state = tmp_path / "state-existing-quarantine"
    workspace = _regression_repo(tmp_path)
    problem_path = _problem(state)
    baseline_sha = _git(workspace, "rev-parse", "HEAD")
    _git(workspace, "switch", "-c", "agent/repair-self-problem-test")
    (workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    trial_path = (
        state
        / "application/self_maintenance/repair_trials/self-problem-test.json"
    )
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_text(json.dumps({
        "schema_version": "self-repair-trial.v1",
        "problem_id": "self-problem:test",
        "proposal_id": "repair-proposal:test",
        "status": "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
        "observed_at": NOW.isoformat(),
        "repair_kind": KIND,
        "full": {
            "ok": False,
            "profile": "repair-full",
            "results": [{
                "exit_code": 1,
                "stdout": (
                    "FAILED tests/test_regression.py::test_original_contract - "
                    "AssertionError: assert 'VALUE = 1'\n"
                    "E       AssertionError: original contract changed\n"
                ),
                "stderr": "",
            }],
        },
    }), encoding="utf-8")
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _regression_recipe()},
        python_executable=sys.executable,
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "CANDIDATE_REJECTED_REPLAN_QUEUED"
    assert _git(workspace, "rev-parse", "HEAD") == baseline_sha
    assert _git(workspace, "status", "--porcelain") == ""
    assert (workspace / "app.py").read_text() == "VALUE = 1\n"
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "DIAGNOSING"
    assert updated["repair_proposal"] is None
    assert updated["repair_trial"]["baseline_status"] == "PASSED"



def test_self_repair_test_profile_uses_explicit_test_pythonpath(tmp_path):
    state = tmp_path / "state-test-pythonpath"
    workspace = _regression_repo(tmp_path)
    deps = tmp_path / "test-deps"
    deps.mkdir()
    (deps / "external_test_helper.py").write_text(
        "READY = True\n",
        encoding="utf-8",
    )
    (workspace / "tests/test_focused.py").write_text(
        "import external_test_helper\n"
        "def test_focused():\n"
        "    assert external_test_helper.READY is True\n",
        encoding="utf-8",
    )
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _regression_recipe()},
        python_executable=sys.executable,
        test_pythonpath=f"{deps}:src",
    )

    provider = executor._provider(_regression_recipe())
    result = provider.invoke("code.test", {"profile": "repair-focused"})

    assert result["ok"] is True
    command = result["results"][0]["command"]
    assert command[:2] == ["env", "-i"]
    assert "PYTHONPATH=" + str(deps) + ":src" in command
    assert any(part.startswith("HOME=") for part in command)
    assert "LANG=C.UTF-8" in command


def test_paused_baseline_retries_after_test_environment_recovers(tmp_path):
    state = tmp_path / "state-paused-recovery"
    workspace = _regression_repo(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["status"] = "REPAIR_PAUSED_BASELINE_UNHEALTHY"
    problem["repair_proposal"]["status"] = "PAUSED_BASELINE_UNHEALTHY"
    problem_path.write_text(json.dumps(problem), encoding="utf-8")
    trial_path = (
        state
        / "application/self_maintenance/repair_trials/self-problem-test.json"
    )
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_text(json.dumps({
        "schema_version": "self-repair-trial.v1",
        "problem_id": "self-problem:test",
        "proposal_id": "repair-proposal:test",
        "status": "REPAIR_PAUSED_BASELINE_UNHEALTHY",
        "observed_at": NOW.isoformat(),
        "repair_kind": KIND,
        "full": {
            "ok": False,
            "profile": "repair-full",
            "results": [{
                "exit_code": 1,
                "stdout": "FAILED environment-only failure\n",
                "stderr": "",
            }],
        },
    }), encoding="utf-8")
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _regression_recipe()},
        python_executable=sys.executable,
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "CANDIDATE_REJECTED_REPLAN_QUEUED"
    assert result["baseline_status"] == "PASSED"
    assert result["candidate_replay_status"] == "FAILED"
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "DIAGNOSING"
    assert updated["repair_proposal"] is None


def _safe_replay_recipe() -> RepairRecipe:
    return RepairRecipe(
        repair_kind=KIND,
        target_files=("app.py",),
        implementation_markers=(("app.py", "# safe candidate"),),
        edits=(
            RepairEdit(
                "app.py",
                "VALUE = 1\n",
                "VALUE = 1\n# safe candidate\n",
            ),
        ),
        focused_tests=("tests/test_focused.py",),
    )


def test_environment_false_rejection_replays_clean_then_retries_candidate(tmp_path):
    state = tmp_path / "state-false-rejection"
    workspace = _regression_repo(tmp_path)
    problem_path = _problem(state)
    trial_path = (
        state
        / "application/self_maintenance/repair_trials/self-problem-test.json"
    )
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_text(json.dumps({
        "schema_version": "self-repair-trial.v1",
        "problem_id": "self-problem:test",
        "proposal_id": "repair-proposal:test",
        "status": "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
        "observed_at": NOW.isoformat(),
        "repair_kind": KIND,
        "full": {
            "ok": False,
            "profile": "repair-full",
            "results": [{
                "exit_code": 1,
                "stdout": "FAILED tests/test_fake_environment.py::test_missing_plugin\n",
                "stderr": "pytest plugin missing",
            }],
        },
    }), encoding="utf-8")
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _safe_replay_recipe()},
        python_executable=sys.executable,
    )

    first = executor.run_once(now=NOW)

    assert first["status"] == "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED"
    assert first["baseline_status"] == "PASSED"
    assert first["candidate_replay_status"] == "PASSED"
    assert _git(workspace, "status", "--porcelain") == ""
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "REPAIR_PROPOSED"
    assert updated["repair_proposal"]["status"] == "PROPOSED"
    assert updated["repair_trial"]["failed_tests"] == []

    second = executor.run_once(now=NOW)

    assert second["status"] == "CANDIDATE_COMMITTED"
    assert second["commit"]["pushed"] is False
    assert "# safe candidate" in (workspace / "app.py").read_text()



def test_self_repair_tests_do_not_inherit_production_content_environment(tmp_path, monkeypatch):
    state = tmp_path / "state-hermetic-env"
    workspace = _regression_repo(tmp_path)
    (workspace / "tests/test_focused.py").write_text(
        "import os\n"
        "def test_focused():\n"
        "    assert os.environ.get('CONTENT_PUBLIC_NAME') is None\n"
        "    assert os.environ.get('CONTENT_OPERATOR_CONSOLE_ENABLED') is None\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTENT_PUBLIC_NAME", "Ferro")
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "1")
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={KIND: _regression_recipe()},
        python_executable=sys.executable,
    )

    provider = executor._provider(_regression_recipe())
    result = provider.invoke("code.test", {"profile": "repair-focused"})

    assert result["ok"] is True
    command = result["results"][0]["command"]
    assert command[:2] == ["env", "-i"]



def test_authoritative_counterevidence_problem_is_prioritized_over_newer_diagnosis(tmp_path):
    state = tmp_path / "state-priority"
    problems = state / "application/self_maintenance/problems"
    problems.mkdir(parents=True)
    workspace = tmp_path / "workspace-priority"
    source_root = workspace / "src/wechat_traffic_agent"
    source_root.mkdir(parents=True)
    (source_root / "content_service.py").write_text(
        "def compose():\n    return None\n",
        encoding="utf-8",
    )
    (source_root / "content_mind.py").write_text(
        "def record_research_belief():\n    return None\n",
        encoding="utf-8",
    )

    (problems / "fresh.json").write_text(json.dumps({
        "problem_id": "self-problem:fresh",
        "status": "DIAGNOSING",
        "symptom": "fresh diagnosis",
        "error_type": "ValueError",
        "failure_site": "content_mind.py:record_research_belief",
        "occurrences": 99,
        "last_seen_at": "2026-09-19T23:00:00+00:00",
        "repair_proposal": None,
        "planner_attempt": {
            "status": "REJECTED",
            "planner_version": "generated-repair.v5",
            "occurrences_seen": 99,
            "reason": "ValueError:insufficient localization",
        },
    }), encoding="utf-8")
    (problems / "counter.json").write_text(json.dumps({
        "problem_id": "self-problem:counter",
        "status": "DIAGNOSING",
        "symptom": "replayed candidate regressed contracts",
        "error_type": "ValueError",
        "failure_site": "content_service.py:compose",
        "occurrences": 1,
        "last_seen_at": "2026-09-19T01:00:00+00:00",
        "repair_proposal": None,
        "planner_attempt": {
            "status": "REJECTED",
            "planner_version": "generated-repair.v5",
            "occurrences_seen": 1,
            "reason": "ValueError:selected an unapproved test",
        },
        "repair_trial": {
            "status": "REJECTED_BY_AUTHORITATIVE_REPLAY",
            "baseline_status": "PASSED",
            "candidate_replay_status": "FAILED",
            "failed_tests": [
                "tests/test_content_explorer.py::test_preserved_contract"
            ],
        },
    }), encoding="utf-8")

    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        recipes={},
        python_executable=sys.executable,
        planner_runtime=object(),
    )

    selected = executor._next_diagnosing_problem(now=NOW)

    assert selected is not None
    assert selected["problem_id"] == "self-problem:counter"
