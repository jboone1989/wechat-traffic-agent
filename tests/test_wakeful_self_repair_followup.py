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

    def run_once(self, *, now):
        result = {"status": self.status, "problem_id": "self-problem:test"}
        if self.status == "GENERATED_PLAN_DEFERRED":
            result["retry_seconds"] = 900
        if self.status == "CANDIDATE_REJECTED_REPLAN_QUEUED":
            result["retry_seconds"] = 60
        return result


class Wakeful:
    def __init__(self):
        self.calls = []

    def schedule_self_maintenance(self, now, *, seconds=60):
        self.calls.append((now, seconds))
def _runner(status):
    wakeful = Wakeful()
    runner = WakefulContentTaskRunner(
        daily_reflection=Daily(),
        self_maintenance=Maintenance(),
        self_repair=Repair(status),
        wakeful=wakeful,
    )
    runner._evolution_deliberation = lambda now: {"status": "NO_OPEN_NEEDS"}
    return runner, wakeful


def test_generated_repair_proposal_schedules_prompt_second_reflect():
    runner, wakeful = _runner("GENERATED_REPAIR_PROPOSED")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert result["self_repair"]["status"] == "GENERATED_REPAIR_PROPOSED"
    assert wakeful.calls == [(NOW, 60)]


def test_deferred_generated_plan_schedules_resource_retry_without_busy_loop():
    runner, wakeful = _runner("GENERATED_PLAN_DEFERRED")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert result["self_repair"]["status"] == "GENERATED_PLAN_DEFERRED"
    assert wakeful.calls == [(NOW, 900)]




def test_rejected_candidate_schedules_counterevidence_replan():
    runner, wakeful = _runner("CANDIDATE_REJECTED_REPLAN_QUEUED")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert result["self_repair"]["status"] == "CANDIDATE_REJECTED_REPLAN_QUEUED"
    assert wakeful.calls == [(NOW, 60)]


def test_other_self_repair_states_do_not_create_busy_loop():
    runner, wakeful = _runner("OBSERVING_PRODUCTION")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert wakeful.calls == []



def test_replayed_candidate_schedules_fresh_execution_cycle():
    runner, wakeful = _runner("CANDIDATE_REPLAY_PASSED_RETRY_QUEUED")

    result = runner._reflect(NOW)

    assert result["status"] == "OK"
    assert result["self_repair"]["status"] == "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED"
    assert wakeful.calls == [(NOW, 60)]
