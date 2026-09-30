from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.interoceptive_fault import InteroceptiveFaultSystem
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


class _NeutralL1:
    def assess_health(self, *, task, deterministic, recent_observations=()):
        abnormal = bool(deterministic.get("abnormal"))
        return {
            "schema_version": "ferro-l1-health.v1",
            "status": "ASSESSED",
            "health_state": "UNKNOWN" if abnormal else "HEALTHY",
            "subsystem": deterministic.get("subsystem") if abnormal else None,
            "provider": "TEST",
            "model": "test",
            "confidence": 1.0,
            "probabilities": {},
            "latency_ms": 0,
            "decided_at": NOW.isoformat(),
        }


def _runner(tmp_path, wakeful):
    return WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
        interoceptive_faults=InteroceptiveFaultSystem(
            tmp_path,
            l1_decisions=_NeutralL1(),
        ),
    )


class _Wakeful:
    def __init__(self):
        self.signals = []
        self.reflections = []
        self.repairs = []

    def emit_interoceptive_fault(self, fault, *, observed_at=None):
        self.signals.append((dict(fault), observed_at))
        return {
            "status": "OBSERVED",
            "episode_id": fault.get("episode_id"),
            "magnitude": fault.get("severity"),
        }

    def schedule_reflection(self, now, *, seconds, reason, relevant_refs=()):
        row = {
            "status": "SCHEDULED",
            "at": now.isoformat(),
            "seconds": seconds,
            "reason": reason,
            "relevant_refs": list(relevant_refs),
        }
        self.reflections.append(row)
        return row

    def schedule_self_maintenance(self, now, *, seconds=60):
        row = {
            "status": "SCHEDULED",
            "at": now.isoformat(),
            "seconds": seconds,
            "task": "SELF_REPAIR",
        }
        self.repairs.append(row)
        return row


def test_run_wrapper_turns_chronic_fallback_into_neural_signal_and_diagnosis(tmp_path):
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)

    cycles = iter(["c1", "c2", "c3", "c4"])

    def run_unobserved(*_args, **_kwargs):
        return {
            "task": "CONTENT_COGNITION",
            "status": "OK",
            "cycle": next(cycles),
            "composition": {"status": "FALLBACK_USED"},
        }

    runner._run_unobserved = run_unobserved

    first = runner.run("CONTENT_COGNITION", now=NOW)
    second = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=1))
    third = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=2))

    assert first["interoception"]["primary_symptom"] == "fallback_dependency"
    assert second["interoception"]["severity"] > first["interoception"]["severity"]
    assert third["interoception"]["status"] in {"WATCH", "ILL"}
    assert len(wakeful.signals) == 3
    assert len(wakeful.reflections) == 1
    assert "diagnose felt runtime pathology" in wakeful.reflections[0]["reason"]
    assert third["maintenance_observation"]["status"] in {
        "RECORDED",
        "ALREADY_RECORDED",
    }
    assert runner.self_maintenance.review()["open_count"] >= 1
    first_problem = third["maintenance_observation"]["problem_id"]

    fourth = runner.run(
        "CONTENT_COGNITION",
        now=NOW + timedelta(minutes=3),
    )
    assert fourth["interoception"]["diagnosis_active"] is True
    assert fourth["maintenance_observation"]["status"] == "RECORDED"
    assert fourth["maintenance_observation"]["problem_id"] == first_problem
    stored = next(
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
        if json.loads(path.read_text()).get("problem_id") == first_problem
    )
    assert stored["occurrences"] == 2
    assert wakeful.repairs[-1]["task"] == "SELF_REPAIR"
    assert wakeful.repairs[-1]["seconds"] == 0


def test_silent_no_effect_contract_becomes_repair_diagnosis(tmp_path):
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)

    runner._run_unobserved = lambda *_args, **_kwargs: {
        "task": "RESOURCE_REVIEW",
        "status": "OK",
        "cycle": "resource-no-effect",
        "autonomous_goal_outcome": {
            "status": "EXECUTED",
            "world_utility": 0.0,
            "drive_feedback": {
                "no_effect_streak": 23,
                "last_outcome_status": "EXECUTED",
            },
        },
    }

    result = runner.run("RESOURCE_REVIEW", now=NOW)

    assert result["status"] == "OK"
    assert result["capability_contracts"]["status"] == "VIOLATED"
    assert result["interoception"]["primary_symptom"] == "contract_goal_effect_absent"
    assert result["maintenance_observation"]["status"] in {
        "RECORDED",
        "ALREADY_RECORDED",
    }
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    contract_problem = next(
        row for row in problems
        if row.get("error_type") == "CAPABILITY_CONTRACT_VIOLATION"
    )
    assert contract_problem["failure_site"] == "resource_review:survival_resources"
    assert contract_problem["status"] == "DIAGNOSING"
    assert len(wakeful.signals) == 1
    assert len(wakeful.reflections) == 1


def test_repeated_contract_no_effect_updates_one_problem_and_wakes_repair(tmp_path):
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)
    streaks = iter([35, 36])
    cycles = iter(["resource-no-effect-35", "resource-no-effect-36"])

    runner._run_unobserved = lambda *_args, **_kwargs: {
        "task": "RESOURCE_REVIEW",
        "status": "OK",
        "cycle": next(cycles),
        "autonomous_goal_outcome": {
            "status": "EXECUTED",
            "world_utility": 0.0,
            "drive_feedback": {
                "no_effect_streak": next(streaks),
                "last_outcome_status": "EXECUTED",
            },
        },
    }

    first = runner.run("RESOURCE_REVIEW", now=NOW)
    second = runner.run("RESOURCE_REVIEW", now=NOW + timedelta(minutes=1))

    assert first["maintenance_observation"]["status"] == "RECORDED"
    assert second["maintenance_observation"]["status"] == "RECORDED"
    assert (
        first["maintenance_observation"]["problem_id"]
        == second["maintenance_observation"]["problem_id"]
    )
    problem_id = first["maintenance_observation"]["problem_id"]
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    matches = [row for row in problems if row.get("problem_id") == problem_id]
    assert len(matches) == 1
    assert matches[0]["occurrences"] == 2
    assert wakeful.repairs[-1]["task"] == "SELF_REPAIR"
    assert wakeful.repairs[-1]["seconds"] == 0


def test_settled_autonomous_goal_no_effect_is_felt_in_production_path(tmp_path):
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)

    result = runner.observe_settled_capability_outcome(
        "RESOURCE_REVIEW",
        {
            "status": "EXECUTED",
            "goal_id": "autonomous-goal:test",
            "close_evidence_ref": "task-result:settled-no-effect",
            "world_utility": 0.0,
            "drive_feedback": {
                "no_effect_streak": 23,
                "last_outcome_status": "EXECUTED",
            },
        },
        now=NOW,
    )

    assert result["status"] == "VIOLATED"
    assert result["capability_contracts"]["status"] == "VIOLATED"
    assert result["interoception"]["primary_symptom"] == "contract_goal_effect_absent"
    assert result["maintenance_observation"]["status"] in {
        "RECORDED",
        "ALREADY_RECORDED",
    }
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    problem = next(
        row for row in problems
        if row.get("error_type") == "CAPABILITY_CONTRACT_VIOLATION"
    )
    assert problem["failure_site"] == "resource_review:survival_resources"
    assert problem["status"] == "DIAGNOSING"
    assert len(wakeful.signals) == 1
    assert len(wakeful.reflections) == 1


def test_settled_world_outcome_can_expose_silent_goal_disease(tmp_path):
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)

    result = runner.observe_settled_capability_outcome(
        "RESOURCE_REVIEW",
        {
            "status": "EXECUTED",
            "goal_id": "goal:test",
            "world_utility": 0.0,
            "drive_feedback": {
                "no_effect_streak": 23,
                "last_outcome_status": "EXECUTED",
            },
        },
        now=NOW,
    )

    assert result["status"] == "VIOLATED"
    assert result["interoception"]["primary_symptom"] == "contract_goal_effect_absent"
    assert result["capability_contracts"]["violations"][0]["subsystem"] == "goal_execution"
    assert len(wakeful.signals) == 1


def test_neural_interoception_feels_persisted_chronic_no_effect(tmp_path):
    application = tmp_path / "application"
    application.mkdir(parents=True)
    drive_path = application / "drive_system.json"
    drive_path.write_text(json.dumps({
        "drives": {
            "resource": {
                "no_effect_streak": 23,
                "last_outcome_status": "EXECUTED",
                "last_outcome_utility": 0.0,
                "last_outcome_evidence_ref": "task-result:persisted-resource-no-effect",
                "last_attempted_at": NOW.isoformat(),
            },
        },
    }))
    wakeful = _Wakeful()
    runner = _runner(tmp_path, wakeful)

    result = runner._finalize_task_result(
        "NEURAL_INTEROCEPTION",
        {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK",
            "cycle": "body-scan-1",
        },
        now=NOW,
    )

    assert result["durable_capability_contracts"]["status"] == "VIOLATED"
    assert result["capability_contracts"]["surface"] == "durable_state"
    assert result["interoception"]["primary_symptom"] == "contract_goal_effect_absent"
    assert result["maintenance_observation"]["status"] == "RECORDED"
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    problem = next(
        row for row in problems
        if row.get("error_type") == "CAPABILITY_CONTRACT_VIOLATION"
    )
    assert problem["failure_site"] == "resource_review:survival_resources"
    assert problem["status"] == "DIAGNOSING"
    first_occurrences = int(problem.get("occurrences") or 0)

    repeated = runner._finalize_task_result(
        "NEURAL_INTEROCEPTION",
        {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK",
            "cycle": "body-scan-2",
        },
        now=NOW + timedelta(minutes=1),
    )
    assert repeated["interoception"]["primary_symptom"] == "contract_goal_effect_absent"
    assert repeated["maintenance_observation"]["status"] == "ALREADY_RECORDED"

    drive_path.write_text(json.dumps({
        "drives": {
            "resource": {
                "no_effect_streak": 24,
                "last_outcome_status": "EXECUTED",
                "last_outcome_utility": 0.0,
                "last_outcome_evidence_ref": "task-result:persisted-resource-no-effect-2",
                "last_attempted_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        },
    }))
    changed = runner._finalize_task_result(
        "NEURAL_INTEROCEPTION",
        {
            "task": "NEURAL_INTEROCEPTION",
            "status": "OK",
            "cycle": "body-scan-3",
        },
        now=NOW + timedelta(minutes=2),
    )
    assert changed["maintenance_observation"]["status"] == "RECORDED"
    problems = [
        json.loads(path.read_text())
        for path in (tmp_path / "application/self_maintenance/problems").glob("*.json")
    ]
    updated = next(
        row for row in problems
        if row.get("error_type") == "CAPABILITY_CONTRACT_VIOLATION"
    )
    assert int(updated.get("occurrences") or 0) == first_occurrences + 1
