from __future__ import annotations

from datetime import datetime, timezone
import json

from wechat_traffic_agent.generated_repair import PLANNER_VERSION
from wechat_traffic_agent.self_repair_executor import SelfRepairExecutor


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def _problem(state_root, *, risk="LOW"):
    root = state_root / "application" / "self_maintenance"
    folder = root / "problems"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "problem.json"
    payload = {
        "problem_id": "self-problem:abc123",
        "status": "REPAIR_PROPOSED",
        "symptom": "A known bounded cognition failure occurred.",
        "error_type": "ValueError",
        "failure_site": "content_loop.py:_structured",
        "severity": "LOW",
        "observed_world_impact": "One cognition cycle did not complete.",
        "evidence_refs": ["content-cycle:v2-1:result"],
        "repair_proposal": {
            "proposal_id": "repair-proposal:abc",
            "repair_kind": "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION",
            "risk": risk,
            "target_files": ["src/wechat_traffic_agent/content_goal_loop.py"],
        },
        "cycles": ["v2-1"],
        "occurrences": 1,
        "last_seen_at": NOW.isoformat(),
    }
    path.write_text(json.dumps(payload))
    return path


class RequestCapability:
    def __init__(self):
        self.calls = []

    def available_capabilities(self):
        return {"REQUEST_CODE_REPAIR"}

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, payload))
        return {"status": "RECEIVED", "requestId": "pi-repair-123"}


def _executor(state_root, capability_runtime=None):
    return SelfRepairExecutor(
        state_root=state_root,
        workspace_root=None,
        deployed_root=None,
        capability_runtime=capability_runtime,
    )


def test_executor_submits_request_only_and_records_receipt(tmp_path):
    problem_path = _problem(tmp_path)
    runtime = RequestCapability()

    result = _executor(tmp_path, runtime).run_once(now=NOW)

    assert result["status"] == "REQUESTED_TO_PI_REMOTE"
    assert result["repair_request"]["requestId"] == "pi-repair-123"
    assert len(runtime.calls) == 1
    capability, request = runtime.calls[0]
    assert capability == "REQUEST_CODE_REPAIR"
    assert request["risk"] == "LOW"
    assert request["sourceProblemId"] == "self-problem:abc123"
    assert "edits" not in request
    assert "command" not in request
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "REQUESTED_TO_PI_REMOTE"
    assert updated["repair_trial"]["status"] == "REQUESTED_TO_PI_REMOTE"


def test_executor_fails_closed_when_request_capability_is_missing(tmp_path):
    problem_path = _problem(tmp_path)

    result = _executor(tmp_path).run_once(now=NOW)

    assert result["status"] == "CAPABILITY_UNAVAILABLE"
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "CAPABILITY_UNAVAILABLE"
    assert updated["repair_trial"]["status"] == "CAPABILITY_UNAVAILABLE"


def test_non_low_risk_never_uses_automatic_request_path(tmp_path):
    _problem(tmp_path, risk="HIGH")
    runtime = RequestCapability()

    result = _executor(tmp_path, runtime).run_once(now=NOW)

    assert result["status"] == "NEEDS_HUMAN_REVIEW"
    assert runtime.calls == []


def test_capability_contract_problem_routes_to_pi_remote(tmp_path):
    root = tmp_path / "application" / "self_maintenance"
    folder = root / "problems"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "contract-problem.json"
    path.write_text(json.dumps({
        "problem_id": "self-problem:contract123",
        "status": "REPAIR_PROPOSED",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: repeated action has no effect",
        "error_type": "CAPABILITY_CONTRACT_VIOLATION",
        "failure_site": "wakeful_tasks.py:goal_effect_contract",
        "severity": "LOW",
        "observed_world_impact": "Repeated executed actions produced no goal-relevant effect.",
        "evidence_refs": ["task-result:RESOURCE_REVIEW:no-effect"],
        "repair_proposal": {
            "proposal_id": "repair-proposal:contract123",
            "repair_kind": "GENERATED_BOUNDED_EDIT",
            "risk": "LOW",
            "target_files": ["src/wechat_traffic_agent/wakeful_tasks.py"],
            "generated_recipe": {
                "hypothesis": "goal effect settlement is not exposed correctly",
                "rationale": "contract evidence localizes the effect boundary",
                "risk": "LOW",
                "confidence": 0.9,
                "target_files": [],
                "edits": [],
                "focused_tests": [],
                "implementation_markers": [],
            },
        },
        "cycles": ["resource-no-effect"],
        "occurrences": 1,
        "last_seen_at": NOW.isoformat(),
    }))
    runtime = RequestCapability()

    result = _executor(tmp_path, runtime).run_once(now=NOW)

    assert result["status"] == "REQUESTED_TO_PI_REMOTE"
    assert runtime.calls[0][0] == "REQUEST_CODE_REPAIR"
    payload = runtime.calls[0][1]
    assert payload["sourceProblemId"] == "self-problem:contract123"
    assert payload["failureSite"] == "wakeful_tasks.py:goal_effect_contract"
    assert payload["targetFiles"] == ["src/wechat_traffic_agent/wakeful_tasks.py"]


def test_startup_recovery_wakes_unlocalized_problem_after_localizer_upgrade(tmp_path):
    folder = tmp_path / "application" / "self_maintenance" / "problems"
    folder.mkdir(parents=True)
    path = folder / "unlocalized.json"
    path.write_text(json.dumps({
        "problem_id": "self-problem:felt-fault",
        "status": "AUTO_REPAIR_UNLOCALIZED",
        "error_type": "INTEROCEPTIVE_FAULT",
        "failure_site": "interoception:network",
        "task": "CHECK_X",
        "occurrences": 1,
        "planner_attempt": {
            "status": "UNLOCALIZED",
            "planner_version": "generated-repair.v9",
            "occurrences_seen": 1,
        },
    }))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = SelfRepairExecutor(
        state_root=tmp_path,
        workspace_root=workspace,
        deployed_root=None,
        planner_runtime=object(),
    )

    summary = executor.startup_recovery_summary(now=NOW)

    assert summary["status"] == "WORK_PENDING"
    assert summary["actionable_count"] == 1
    assert summary["problem_ids"] == ["self-problem:felt-fault"]
    assert summary["reasons"]["self-problem:felt-fault"] == (
        "LOCALIZER_UPGRADE_OR_NEW_EVIDENCE"
    )
    assert summary["planner_version"] == PLANNER_VERSION


def test_startup_recovery_does_not_rewake_same_unlocalized_evidence_forever(tmp_path):
    folder = tmp_path / "application" / "self_maintenance" / "problems"
    folder.mkdir(parents=True)
    (folder / "unlocalized.json").write_text(json.dumps({
        "problem_id": "self-problem:felt-fault",
        "status": "AUTO_REPAIR_UNLOCALIZED",
        "error_type": "INTEROCEPTIVE_FAULT",
        "failure_site": "interoception:network",
        "task": "CHECK_X",
        "occurrences": 1,
        "planner_attempt": {
            "status": "UNLOCALIZED",
            "planner_version": PLANNER_VERSION,
            "occurrences_seen": 1,
        },
    }))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = SelfRepairExecutor(
        state_root=tmp_path,
        workspace_root=workspace,
        deployed_root=None,
        planner_runtime=object(),
    )

    summary = executor.startup_recovery_summary(now=NOW)

    assert summary["status"] == "NO_REPAIR_WORK"
    assert summary["actionable_count"] == 0
