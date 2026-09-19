import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
BASE_CYCLE = "v2-1789808400"


class Tick:
    generation_ready = True

    def to_payload(self):
        return {"generation_ready": True}


class App:
    def run_tick(self, *, as_of_time):
        return Tick()


class Economy:
    def __init__(self):
        self.calls = []

    def begin_cycle(self, *, now, cycle):
        self.calls.append((now, cycle))
        return {
            "status": "SKIPPED_NO_RESOURCE_COST",
            "decision": "NO_RESOURCE_ACTION",
            "cycle": cycle,
        }


class Wakeful:
    def __init__(self):
        self.cognition_marks = []

    def mark_cognition(self, now):
        self.cognition_marks.append(now)


class AfterCycle:
    def __init__(self):
        self.calls = []

    async def after_content_cycle(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "OK"}


class Publisher:
    def __init__(self):
        self.calls = []

    async def after_content_cycle(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "SKIPPED"}


class Loop:
    def __init__(self, state_root: Path, *, result_status: str = "COGNITION_ONLY"):
        self.state_root = state_root
        self.result_status = result_status
        self.calls = []

    def _result_path(self, cycle):
        return (
            self.state_root
            / "content"
            / "continuous"
            / "cycles"
            / cycle
            / "result.json"
        )

    def preflight(self, *, now, cycle_id):
        cycle = BASE_CYCLE if cycle_id is None else str(cycle_id)
        path = self._result_path(cycle)
        return {
            "cycle": cycle,
            "status": "ALREADY_PROCESSED" if path.exists() else "READY",
            "result_path": str(path),
        }

    async def run_once(self, *, now, cycle_id, focus_topic):
        cycle = str(cycle_id)
        self.calls.append((now, cycle, focus_topic))
        payload = {"status": self.result_status, "cycle": cycle}
        path = self._result_path(cycle)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return payload


def _write_base_failure(state_root: Path):
    path = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / BASE_CYCLE
        / "result.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ERROR",
        "cycle": BASE_CYCLE,
        "error_type": "KeyError",
        "message": "external inquiry missing",
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path, payload


def _runner(tmp_path: Path, *, result_status="COGNITION_ONLY"):
    state_root = tmp_path / "state"
    base_path, base_payload = _write_base_failure(state_root)
    loop = Loop(state_root, result_status=result_status)
    economy = Economy()
    wakeful = Wakeful()
    social = AfterCycle()
    x_publisher = Publisher()
    okx_publisher = Publisher()
    runner = WakefulContentTaskRunner(
        app=App(),
        state_root=state_root,
        network_service=None,
        wakeful=wakeful,
        web_worker=None,
        gsc_collector=None,
        continuous_loop=loop,
        resource_economy=economy,
        social_expression_loop=social,
        x_browser_publisher=x_publisher,
        okx_orbit_publisher=okx_publisher,
        x_loop=None,
    )
    return (
        runner,
        loop,
        economy,
        wakeful,
        social,
        x_publisher,
        okx_publisher,
        base_path,
        base_payload,
    )


def test_failed_cycle_runs_fresh_immutable_recovery_cycle(tmp_path):
    (
        runner,
        loop,
        economy,
        wakeful,
        social,
        x_publisher,
        okx_publisher,
        base_path,
        base_payload,
    ) = _runner(tmp_path)

    result = runner.run("CONTENT_COGNITION", now=NOW)

    recovery = result["content_cycle_recovery"]
    assert recovery["status"] == "COMPLETED"
    assert recovery["recovery_of"] == BASE_CYCLE
    assert recovery["previous_status"] == "ERROR"
    recovery_cycle = recovery["recovery_cycle"]
    assert recovery_cycle.startswith("v2-recovery-1789808400-1-")
    assert loop.calls == [(NOW, recovery_cycle, None)]
    assert economy.calls == [(NOW, recovery_cycle)]
    assert json.loads(base_path.read_text()) == base_payload
    recovery_path = (
        tmp_path
        / "state"
        / "content"
        / "continuous"
        / "cycles"
        / recovery_cycle
        / "recovery.json"
    )
    persisted = json.loads(recovery_path.read_text())
    assert persisted["preserves_failed_cycle"] is True
    assert persisted["result_status"] == "COGNITION_ONLY"
    assert persisted["status"] == "COMPLETED"
    assert result["continuous_content"]["cycle"] == recovery_cycle
    assert wakeful.cognition_marks == [NOW]
    assert len(social.calls) == 1
    assert x_publisher.calls == [{"cycle": recovery_cycle, "now": NOW}]
    assert okx_publisher.calls == [{"cycle": recovery_cycle, "now": NOW}]


def test_successful_recovery_prevents_duplicate_work_for_same_base_cycle(tmp_path):
    runner, loop, economy, wakeful, *_ = _runner(tmp_path)

    first = runner.run("CONTENT_COGNITION", now=NOW)
    first_cycle = first["content_cycle_recovery"]["recovery_cycle"]
    second = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=1))

    assert second["content_cycle_recovery"]["status"] == "ALREADY_RECOVERED"
    assert second["continuous_content"] == {
        "status": "ALREADY_RECOVERED",
        "cycle": BASE_CYCLE,
        "recovery_cycle": first_cycle,
        "previous_status": "ERROR",
        "attempt_count": 1,
    }
    assert len(loop.calls) == 1
    assert len(economy.calls) == 1
    assert wakeful.cognition_marks == [NOW]
