from datetime import datetime, timezone

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)


class Daily:
    def maybe_generate(self, now):
        return {"status": "SKIPPED"}


class Maintenance:
    def review(self):
        return {"status": "OK", "open_count": 1}


class Repair:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def run_once(self, *, now):
        self.calls += 1
        result = {"status": self.status, "problem_id": "self-problem:test"}
        if self.status == "GENERATED_PLAN_DEFERRED":
            result["retry_seconds"] = 900
        if self.status in {
            "CANDIDATE_REJECTED_REPLAN_QUEUED",
            "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED",
        }:
            result["retry_seconds"] = 60
        return result


class Wakeful:
    def __init__(self):
        self.calls = []

    def schedule_self_maintenance(self, now, *, seconds=60):
        self.calls.append((now, seconds))


def _runner(status):
    wakeful = Wakeful()
    repair = Repair(status)
    runner = WakefulContentTaskRunner(
        daily_reflection=Daily(),
        self_maintenance=Maintenance(),
        self_repair=repair,
        wakeful=wakeful,
    )
    runner._evolution_deliberation = lambda now: {"status": "NO_OPEN_NEEDS"}
    return runner, wakeful, repair


def test_dedicated_self_repair_lane_advances_one_problem():
    runner, wakeful, repair = _runner("GENERATED_REPAIR_PROPOSED")

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert result["problem_id"] == "self-problem:test"
    assert repair.calls == 1
    assert wakeful.calls == [(NOW, 60)]


def test_self_repair_task_dispatches_directly_without_resource_or_reflection_lane():
    runner, wakeful, repair = _runner("GENERATED_REPAIR_PROPOSED")

    result = runner._run_unobserved("SELF_REPAIR", now=NOW)

    assert result["task"] == "SELF_REPAIR"
    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert repair.calls == 1
    assert wakeful.calls == [(NOW, 60)]


def test_reflection_does_not_execute_self_repair_anymore():
    runner, wakeful, repair = _runner("GENERATED_REPAIR_PROPOSED")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert "self_repair" not in result
    assert repair.calls == 0
    assert wakeful.calls == []


def test_deferred_generated_plan_schedules_dedicated_repair_retry_without_busy_loop():
    runner, wakeful, repair = _runner("GENERATED_PLAN_DEFERRED")

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "GENERATED_PLAN_DEFERRED"
    assert repair.calls == 1
    assert wakeful.calls == [(NOW, 900)]


def test_rejected_candidate_schedules_counterevidence_replan_on_repair_lane():
    runner, wakeful, repair = _runner("CANDIDATE_REJECTED_REPLAN_QUEUED")

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "CANDIDATE_REJECTED_REPLAN_QUEUED"
    assert repair.calls == 1
    assert wakeful.calls == [(NOW, 60)]


def test_other_self_repair_states_do_not_create_busy_loop():
    runner, wakeful, repair = _runner("OBSERVING_PRODUCTION")

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "OBSERVING_PRODUCTION"
    assert repair.calls == 1
    assert wakeful.calls == []


def test_replayed_candidate_schedules_fresh_execution_cycle_on_repair_lane():
    runner, wakeful, repair = _runner("CANDIDATE_REPLAY_PASSED_RETRY_QUEUED")

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED"
    assert repair.calls == 1
    assert wakeful.calls == [(NOW, 60)]
