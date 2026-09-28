import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
BASE_CYCLE = "v2-1789808400"


async def _owner_pipeline_decision(task, max_tokens, output_schema=None):
    return json.dumps({
        "action": "RUN_CONTENT_PIPELINE",
        "query": None,
        "topic": "livingruntime-remote",
        "wait_seconds": None,
        "rationale": "Exercise the owner-directed content path in this test.",
    })


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
        self.goal_followups = []
        self.provider_recoveries = []
        self.self_maintenance_retries = []

    def mark_cognition(self, now):
        self.cognition_marks.append(now)

    def schedule_goal_followup(self, now, *, seconds=5, relevant_refs=()):
        self.goal_followups.append((now, seconds))

    def schedule_provider_recovery(self, now, *, default_seconds=300):
        self.provider_recoveries.append((now, default_seconds))
        return {
            "status": "SCHEDULED",
            "retry_at": (now + timedelta(seconds=default_seconds)).isoformat(),
            "retry_seconds": default_seconds,
        }

    def schedule_self_maintenance(self, now, *, seconds=60):
        self.self_maintenance_retries.append((now, seconds))


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

    async def run_once(
        self, *, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None
    ):
        cycle = str(cycle_id)
        self.calls.append((now, cycle, focus_topic))
        payload = {"status": self.result_status, "cycle": cycle}
        if self.result_status == "ERROR":
            payload.update({
                "error_type": "LLMProviderError",
                "message": "no viable LLM resource is currently available",
            })
        path = self._result_path(cycle)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return payload


class MissionTimeoutLoop(Loop):
    async def run_once(
        self, *, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None
    ):
        cycle = str(cycle_id)
        self.calls.append((now, cycle, focus_topic))
        payload = {
            "status": "ERROR",
            "cycle": cycle,
            "error_type": "TimeoutError",
            "message": "legacy advisory backend exceeded max_duration_seconds",
            "mind_plan": {
                "cycle": cycle,
                "goal": {
                    "goal_id": "external-mission-pending:mission-growth-1",
                    "origin": "EXTERNAL_MISSION",
                },
                "external_mission_id": "mission-growth-1",
            },
        }
        folder = self._result_path(cycle).parent
        folder.mkdir(parents=True, exist_ok=True)
        self._result_path(cycle).write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
        (folder / "mission_discovery_error.json").write_text(
            json.dumps({
                "schema_version": "content-mission-discovery-error.v1",
                "error_type": "TimeoutError",
                "message": "legacy advisory backend exceeded max_duration_seconds",
                "mission_id": "mission-growth-1",
                "roadmap_step_id": "mission-step-community",
                "experiment_id": "mission-exp-growth",
                "publication_authority": False,
                "retryable": True,
            }) + "\n",
            encoding="utf-8",
        )
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


def test_recovery_invalidated_base_cycle_runs_fresh_immutable_recovery(tmp_path):
    runner, loop, economy, wakeful, *_, base_path, base_payload = _runner(tmp_path)
    invalidated = {
        "status": "RECOVERY_INVALIDATED",
        "cycle": BASE_CYCLE,
        "reason": "MODEL_PHASE_INPUT_CHANGED",
    }
    base_path.write_text(json.dumps(invalidated) + "\n", encoding="utf-8")

    result = runner.run("CONTENT_COGNITION", now=NOW)

    recovery = result["content_cycle_recovery"]
    assert recovery["status"] == "COMPLETED"
    assert recovery["previous_status"] == "RECOVERY_INVALIDATED"
    assert recovery["recovery_cycle"].startswith("v2-recovery-1789808400-1-")
    assert loop.calls == [(NOW, recovery["recovery_cycle"], None)]
    assert json.loads(base_path.read_text()) == invalidated
    assert wakeful.cognition_marks == [NOW]


def test_invalidated_recovery_attempt_does_not_suppress_next_attempt(tmp_path):
    runner, loop, economy, wakeful, *_ = _runner(tmp_path)
    first_recovery = (
        tmp_path
        / "state"
        / "content"
        / "continuous"
        / "cycles"
        / "v2-recovery-1789808400-1-1789808500"
    )
    first_recovery.mkdir(parents=True, exist_ok=True)
    (first_recovery / "result.json").write_text(
        json.dumps({
            "status": "RECOVERY_INVALIDATED",
            "cycle": first_recovery.name,
            "reason": "MODEL_PHASE_INPUT_CHANGED",
        }) + "\n",
        encoding="utf-8",
    )

    result = runner.run("CONTENT_COGNITION", now=NOW)

    recovery = result["content_cycle_recovery"]
    assert recovery["status"] == "COMPLETED"
    assert recovery["attempt"] == 2
    assert recovery["recovery_cycle"].startswith("v2-recovery-1789808400-2-")
    assert loop.calls == [(NOW, recovery["recovery_cycle"], None)]
    assert wakeful.cognition_marks == [NOW]


def test_provider_outage_schedules_content_recovery_wake(tmp_path):
    runner, loop, _economy, wakeful, *_ = _runner(
        tmp_path, result_status="ERROR"
    )

    result = runner.run("CONTENT_COGNITION", now=NOW)

    assert result["continuous_content"]["error_type"] == "LLMProviderError"
    assert result["provider_recovery"]["status"] == "SCHEDULED"
    assert wakeful.provider_recoveries == [(NOW, 300)]
    assert loop.calls


def test_retryable_mission_timeout_schedules_goal_followup_wake(tmp_path):
    state_root = tmp_path / "state"
    loop = MissionTimeoutLoop(state_root)
    wakeful = Wakeful()
    runner = WakefulContentTaskRunner(
        app=App(),
        state_root=state_root,
        network_service=None,
        wakeful=wakeful,
        web_worker=None,
        gsc_collector=None,
        continuous_loop=loop,
        resource_economy=Economy(),
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=None,
    )

    result = runner.run("CONTENT_COGNITION", now=NOW)

    assert result["continuous_content"]["error_type"] == "TimeoutError"
    assert result["mission_followup_schedule"] == {
        "status": "SCHEDULED",
        "seconds": 5,
        "goal_id": "external-mission-retry:mission-growth-1:mission-step-community",
        "mission_id": "mission-growth-1",
        "reason": "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED",
    }
    assert wakeful.goal_followups == [(NOW, 5)]
    assert wakeful.provider_recoveries == []


def test_provider_outages_do_not_exhaust_content_recovery_limit(tmp_path):
    runner, loop, _economy, wakeful, *_ = _runner(
        tmp_path, result_status="ERROR"
    )

    attempts = []
    for offset in (0, 310, 620, 930):
        result = runner.run(
            "CONTENT_COGNITION",
            now=NOW + timedelta(seconds=offset),
        )
        recovery = result["content_cycle_recovery"]
        assert recovery["status"] == "FAILED"
        attempts.append(recovery["attempt"])

    assert attempts == [1, 2, 3, 4]
    assert len(loop.calls) == 4
    assert len(wakeful.provider_recoveries) == 4


def test_non_provider_failures_still_stop_after_three_recovery_attempts(tmp_path):
    runner, loop, _economy, _wakeful, *_ = _runner(tmp_path)
    cycles_root = (
        tmp_path / "state" / "content" / "continuous" / "cycles"
    )
    for attempt in (1, 2, 3):
        folder = cycles_root / f"v2-recovery-1789808400-{attempt}-{1789808500 + attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(
            json.dumps({
                "status": "ERROR",
                "cycle": folder.name,
                "error_type": "ValueError",
                "message": "content logic failed",
            }) + "\n",
            encoding="utf-8",
        )

    result = runner.run("CONTENT_COGNITION", now=NOW)

    recovery = result["content_cycle_recovery"]
    assert recovery["status"] == "RECOVERY_LIMIT_REACHED"
    assert recovery["attempt_count"] == 3
    assert recovery["bounded_failure_attempt_count"] == 3
    assert result["continuous_content"]["status"] == "RECOVERY_LIMIT_REACHED"
    assert loop.calls == []


def test_new_runtime_revision_gets_fresh_bounded_recovery_budget(tmp_path):
    runner, loop, _economy, _wakeful, *_ = _runner(tmp_path)
    runner._runtime_revision = lambda: "new-runtime-revision"
    cycles_root = (
        tmp_path / "state" / "content" / "continuous" / "cycles"
    )
    for attempt in (1, 2, 3):
        folder = cycles_root / f"v2-recovery-1789808400-{attempt}-{1789808500 + attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(
            json.dumps({
                "status": "ERROR",
                "cycle": folder.name,
                "error_type": "ValueError",
                "message": "old runtime failed",
            }) + "\n",
            encoding="utf-8",
        )

    result = runner.run("CONTENT_COGNITION", now=NOW)

    recovery = result["content_cycle_recovery"]
    assert recovery["status"] == "COMPLETED"
    assert recovery["attempt"] == 4
    assert recovery["runtime_revision"] == "new-runtime-revision"
    assert recovery["recovery_cycle"].startswith("v2-recovery-1789808400-4-")
    assert loop.calls == [(NOW, recovery["recovery_cycle"], None)]


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


MISSION_GOAL = {
    "goal": {
        "goal_id": "external-mission-discovery:mission-growth-1:mission-step-community",
        "origin": "EXTERNAL_MISSION",
        "description": "Research the next useful evidence-backed mission angle.",
        "confidence": 1.0,
        "expected_value": 1.0,
        "interest_refs": ["persistent-ai-agents"],
        "inquiry_refs": [
            "external-mission-discovery:mission-growth-1:mission-step-community"
        ],
        "value_refs": ["mission-growth-1"],
    },
    "external_mission_id": "mission-growth-1",
}


class MissionLoop(Loop):
    def __init__(self, state_root: Path, *, result_status: str = "COGNITION_ONLY"):
        super().__init__(state_root, result_status=result_status)

    async def run_once(
        self, *, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None
    ):
        cycle = str(cycle_id)
        self.calls.append((now, cycle, focus_topic))
        payload = {
            "status": self.result_status,
            "cycle": cycle,
            "mind_plan": MISSION_GOAL,
        }
        if self.result_status == "COGNITION_ONLY":
            payload["cognition"] = {
                "cognition_state": "NO_MATERIAL_CHANGE",
                "worth_saying": False,
                "expression_kind": "NONE",
                "what_changed": "No durable new information emerged.",
                "what_matters": "Try a materially different evidence angle.",
                "belief_proposition": None,
                "rationale": "This angle did not produce information gain.",
            }
        path = self._result_path(cycle)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return payload


def _mission_runner(tmp_path: Path, *, base_status="COGNITION_ONLY"):
    state_root = tmp_path / "state"
    base_path = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / BASE_CYCLE
        / "result.json"
    )
    base_path.parent.mkdir(parents=True, exist_ok=True)
    if base_status == "ERROR":
        base_payload = {
            "status": "ERROR",
            "cycle": BASE_CYCLE,
            "error_type": "KeyError",
            "message": "external inquiry missing",
        }
    else:
        base_payload = {
            "status": "COGNITION_ONLY",
            "cycle": BASE_CYCLE,
            "mind_plan": MISSION_GOAL,
            "cognition": {
                "cognition_state": "NO_MATERIAL_CHANGE",
                "worth_saying": False,
                "expression_kind": "NONE",
                "what_changed": "No durable new information emerged.",
                "what_matters": "Try another evidence angle.",
                "belief_proposition": None,
                "rationale": "The selected angle repeated known information.",
            },
        }
    base_path.write_text(json.dumps(base_payload) + "\n", encoding="utf-8")
    loop = MissionLoop(state_root)
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
    return runner, loop, economy, wakeful, base_path, base_payload


def test_external_mission_noop_spawns_bounded_followup_cycle(tmp_path):
    runner, loop, economy, wakeful, base_path, base_payload = _mission_runner(tmp_path)

    first = runner.run("CONTENT_COGNITION", now=NOW)

    followup = first["content_mission_followup"]
    assert followup["status"] == "NO_MATERIAL_CHANGE"
    assert followup["followup_of"] == BASE_CYCLE
    assert followup["attempt"] == 1
    assert followup["mission_id"] == "mission-growth-1"
    assert loop.calls[0][1].startswith("v2-mission-followup-1-")
    assert json.loads(base_path.read_text()) == base_payload
    assert wakeful.goal_followups == [(NOW, 5)]

    second_now = NOW + timedelta(seconds=10)
    second = runner.run("CONTENT_COGNITION", now=second_now)
    assert second["content_mission_followup"]["attempt"] == 2
    assert second["content_mission_followup"]["status"] == "NO_MATERIAL_CHANGE"
    assert wakeful.goal_followups[-1] == (second_now, 5)

    loop.result_status = "DRAFT_CREATED"
    third_now = NOW + timedelta(seconds=20)
    third = runner.run("CONTENT_COGNITION", now=third_now)
    assert third["content_mission_followup"]["attempt"] == 3
    assert third["content_mission_followup"]["status"] == "COMPLETED"
    assert third["continuous_content"]["status"] == "DRAFT_CREATED"

    fourth = runner.run("CONTENT_COGNITION", now=NOW + timedelta(seconds=30))
    assert fourth["content_mission_followup"]["status"] == "ALREADY_FOLLOWED_UP"
    assert fourth["continuous_content"]["status"] == "ALREADY_FOLLOWED_UP"
    assert len(loop.calls) == 3
    assert len(economy.calls) == 3


def test_external_mission_followup_stops_after_three_noop_attempts(tmp_path):
    runner, loop, economy, wakeful, *_ = _mission_runner(tmp_path)

    for offset in (0, 10, 20):
        result = runner.run(
            "CONTENT_COGNITION",
            now=NOW + timedelta(seconds=offset),
        )
        assert result["content_mission_followup"]["status"] == "NO_MATERIAL_CHANGE"

    limited = runner.run("CONTENT_COGNITION", now=NOW + timedelta(seconds=30))
    assert limited["content_mission_followup"]["status"] == "FOLLOWUP_LIMIT_REACHED"
    assert limited["content_mission_followup"]["attempt_count"] == 3
    assert limited["continuous_content"]["status"] == "FOLLOWUP_LIMIT_REACHED"
    assert len(loop.calls) == 3
    assert len(economy.calls) == 3
    assert len(wakeful.goal_followups) == 3


def test_recovered_external_mission_noop_can_continue_into_followup(tmp_path):
    runner, loop, economy, wakeful, base_path, base_payload = _mission_runner(
        tmp_path,
        base_status="ERROR",
    )

    recovered = runner.run("CONTENT_COGNITION", now=NOW)
    assert recovered["content_cycle_recovery"]["status"] == "COMPLETED"
    assert recovered["continuous_content"]["status"] == "COGNITION_ONLY"
    recovery_cycle = recovered["continuous_content"]["cycle"]
    assert recovery_cycle.startswith("v2-recovery-1789808400-1-")
    assert wakeful.goal_followups == [(NOW, 5)]

    followed = runner.run(
        "CONTENT_COGNITION",
        now=NOW + timedelta(seconds=10),
    )
    assert followed["content_cycle_recovery"]["status"] == "ALREADY_RECOVERED"
    assert followed["content_mission_followup"]["status"] == "NO_MATERIAL_CHANGE"
    assert followed["content_mission_followup"]["followup_of"] == recovery_cycle
    assert followed["content_mission_followup"]["attempt"] == 1
    assert json.loads(base_path.read_text()) == base_payload
    assert len(loop.calls) == 2
    assert len(economy.calls) == 2


def _write_mission_discovery_error(
    state_root: Path,
    *,
    with_identity: bool,
    result_status: str = "PUBLISHED",
):
    folder = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / BASE_CYCLE
    )
    folder.mkdir(parents=True, exist_ok=True)
    result = {
        "status": result_status,
        "cycle": BASE_CYCLE,
        "mind_plan": {
            "cycle": BASE_CYCLE,
            "goal": None,
            "required_topic": None,
        },
    }
    (folder / "result.json").write_text(
        json.dumps(result) + "\n",
        encoding="utf-8",
    )
    error = {
        "schema_version": "content-mission-discovery-error.v1",
        "error_type": "ValueError",
        "message": "model call interrupted without receipt; skip this cycle",
        "publication_authority": False,
    }
    if with_identity:
        error.update({
            "mission_id": "mission-growth-1",
            "roadmap_step_id": "mission-step-community",
            "experiment_id": "mission-exp-growth",
            "retryable": True,
        })
    (folder / "mission_discovery_error.json").write_text(
        json.dumps(error) + "\n",
        encoding="utf-8",
    )
    return folder


def _write_active_growth_mission_for_retry(state_root: Path):
    path = state_root / "content" / "x" / "growth_mission.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": "content-agent-x-growth-mission.v1",
            "status": "ACTIVE",
            "mission_id": "mission-growth-1",
            "metric": "x.followers",
            "current": 1837.0,
            "target": 5000.0,
            "trajectory": "ON_TRACK",
            "hard_stop": {"active": False},
            "roadmap": {
                "steps": [{
                    "step_id": "mission-step-community",
                    "status": "READY",
                    "resource_tags": ["content_creation", "x_publication_gated"],
                }],
            },
            "active_experiment": {
                "experiment_id": "mission-exp-growth",
                "status": "PROPOSED",
            },
        }) + "\n",
        encoding="utf-8",
    )


def test_interrupted_mission_discovery_identity_creates_followup_even_if_fallback_published(tmp_path):
    state_root = tmp_path / "state"
    folder = _write_mission_discovery_error(
        state_root,
        with_identity=True,
        result_status="PUBLISHED",
    )
    runner = WakefulContentTaskRunner(state_root=state_root)

    identity = runner._mission_followup_identity(
        prior=json.loads((folder / "result.json").read_text()),
        result_path=folder / "result.json",
    )
    assert identity == {
        "goal_id": "external-mission-retry:mission-growth-1:mission-step-community",
        "mission_id": "mission-growth-1",
        "reason": "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED",
    }

    followup = runner._content_mission_followup(
        preflight={
            "cycle": BASE_CYCLE,
            "status": "ALREADY_PROCESSED",
            "result_path": str(folder / "result.json"),
        },
        now=NOW,
    )
    assert followup["status"] == "READY"
    assert followup["mission_id"] == "mission-growth-1"
    assert followup["reason"] == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"


def test_legacy_mission_discovery_error_recovers_identity_from_active_mission(tmp_path):
    state_root = tmp_path / "state"
    folder = _write_mission_discovery_error(
        state_root,
        with_identity=False,
        result_status="COGNITION_ONLY",
    )
    _write_active_growth_mission_for_retry(state_root)
    runner = WakefulContentTaskRunner(state_root=state_root)

    identity = runner._mission_followup_identity(
        prior=json.loads((folder / "result.json").read_text()),
        result_path=folder / "result.json",
    )

    assert identity == {
        "goal_id": "external-mission-retry:mission-growth-1:mission-step-community",
        "mission_id": "mission-growth-1",
        "reason": "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED",
    }


class AlreadyProcessedPublishedLoop(Loop):
    async def run_once(
        self, *, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None
    ):
        cycle = str(cycle_id)
        self.calls.append((now, cycle, focus_topic))
        return {
            "status": "ALREADY_PROCESSED",
            "cycle": cycle,
            "result_path": str(self._result_path(cycle)),
        }


def test_published_cycle_replays_missing_social_stage_without_rewriting_content(tmp_path):
    state_root = tmp_path / "state"
    path = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / BASE_CYCLE
        / "result.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    published = {
        "status": "PUBLISHED",
        "cycle": BASE_CYCLE,
        "draft_path": str(tmp_path / "artifact.json"),
        "publication": {"public_route": "/notes/example/"},
    }
    path.write_text(json.dumps(published) + "\n", encoding="utf-8")
    loop = AlreadyProcessedPublishedLoop(state_root)
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

    result = runner.run("CONTENT_COGNITION", now=NOW)

    assert result["continuous_content"]["status"] == "ALREADY_PROCESSED"
    assert result["social_stage_replay"]["status"] == "REPLAYED"
    assert social.calls == [{"result": published, "now": NOW}]
    assert x_publisher.calls == [{"cycle": BASE_CYCLE, "now": NOW}]
    assert okx_publisher.calls == [{"cycle": BASE_CYCLE, "now": NOW}]
    assert json.loads(path.read_text()) == published


def test_social_backlog_finds_recent_published_cycle_missing_draft(tmp_path):
    state_root = tmp_path / "state"
    folder = (
        state_root
        / "content"
        / "continuous"
        / "cycles"
        / "v2-published-social-gap"
    )
    folder.mkdir(parents=True, exist_ok=True)
    published = {
        "status": "PUBLISHED",
        "cycle": "v2-published-social-gap",
        "publication": {"public_route": "/notes/gap/"},
    }
    (folder / "result.json").write_text(
        json.dumps(published) + "\n", encoding="utf-8"
    )
    (folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )
    runner = WakefulContentTaskRunner(state_root=state_root)

    candidate = runner._social_stage_backlog_candidate()

    assert candidate is not None
    assert candidate["cycle"] == "v2-published-social-gap"
    assert candidate["reason"] == "MISSING_SOCIAL_DRAFT"
    assert candidate["result"] == published


def test_social_backlog_recovers_x_admitted_held_mission_cycle(tmp_path):
    state_root = tmp_path / "state"
    cycle = "v2-held-x-gap"
    folder = state_root / "content" / "continuous" / "cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    held = {
        "status": "HELD_BY_CHECKS",
        "cycle": cycle,
        "draft_path": str(state_root / "content" / "drafts" / "held.json"),
    }
    (folder / "result.json").write_text(json.dumps(held) + "\n", encoding="utf-8")
    (folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )

    class Growth:
        def publication_binding(self, *, cycle):
            return {
                "status": "BOUND_X_EXPRESSION",
                "bound": True,
                "cycle": cycle,
            }

    class XLoop:
        growth_mission = Growth()

    runner = WakefulContentTaskRunner(state_root=state_root, x_loop=XLoop())

    candidate = runner._social_stage_backlog_candidate()

    assert candidate is not None
    assert candidate["cycle"] == cycle
    assert candidate["reason"] == "MISSING_X_MISSION_EXPRESSION"
    assert candidate["result"] == held

    (folder / "x_social_expression.json").write_text(
        json.dumps({"status": "SKIPPED"}) + "\n", encoding="utf-8"
    )
    assert runner._social_stage_backlog_candidate() is None


def test_x_credit_blocker_suppresses_repeated_mission_backlog_replay(tmp_path):
    state_root = tmp_path / "state"
    cycle = "v2-held-x-credit-blocked"
    folder = state_root / "content" / "continuous" / "cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(
        json.dumps({"status": "HELD_BY_CHECKS", "cycle": cycle}) + "\n",
        encoding="utf-8",
    )
    (folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )
    (folder / "x_social_expression.json").write_text(
        json.dumps({"status": "X_CREDITS_DEPLETED"}) + "\n",
        encoding="utf-8",
    )
    blocker = state_root / "content" / "x" / "publication_blocker.json"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text(
        json.dumps({
            "status": "ACTIVE",
            "blocker": "X_CREDITS_DEPLETED",
            "retry_after": "2099-01-01T00:00:00+00:00",
        }) + "\n",
        encoding="utf-8",
    )

    class Growth:
        def publication_binding(self, *, cycle):
            return {
                "status": "BOUND_X_EXPRESSION",
                "bound": True,
                "cycle": cycle,
            }

    class XLoop:
        growth_mission = Growth()

    runner = WakefulContentTaskRunner(state_root=state_root, x_loop=XLoop())
    assert runner._social_stage_backlog_candidate() is None


def test_x_mission_backlog_preempts_newer_generic_social_gap(tmp_path):
    state_root = tmp_path / "state"
    held_cycle = "v2-held-x-priority"
    held_folder = state_root / "content" / "continuous" / "cycles" / held_cycle
    held_folder.mkdir(parents=True, exist_ok=True)
    held = {"status": "HELD_BY_CHECKS", "cycle": held_cycle}
    (held_folder / "result.json").write_text(json.dumps(held) + "\n", encoding="utf-8")
    (held_folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )

    published_cycle = "v2-newer-generic-gap"
    published_folder = state_root / "content" / "continuous" / "cycles" / published_cycle
    published_folder.mkdir(parents=True, exist_ok=True)
    (published_folder / "result.json").write_text(
        json.dumps({"status": "PUBLISHED", "cycle": published_cycle}) + "\n",
        encoding="utf-8",
    )
    (published_folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )

    class Growth:
        def publication_binding(self, *, cycle):
            return {
                "status": "BOUND_X_EXPRESSION",
                "bound": cycle == held_cycle,
                "cycle": cycle,
            }

    class XLoop:
        growth_mission = Growth()

    runner = WakefulContentTaskRunner(state_root=state_root, x_loop=XLoop())

    candidate = runner._social_stage_backlog_candidate()

    assert candidate is not None
    assert candidate["cycle"] == held_cycle
    assert candidate["reason"] == "MISSING_X_MISSION_EXPRESSION"


def test_x_mission_backlog_replays_even_with_explicit_focus_topic(tmp_path):
    state_root = tmp_path / "state"
    cycle = "v2-held-x-with-owner-focus"
    folder = state_root / "content" / "continuous" / "cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    held = {"status": "HELD_BY_CHECKS", "cycle": cycle}
    (folder / "result.json").write_text(json.dumps(held) + "\n", encoding="utf-8")
    (folder / "cognition.json").write_text(
        json.dumps({"worth_saying": True}) + "\n", encoding="utf-8"
    )

    class Growth:
        def publication_binding(self, *, cycle):
            return {"status": "BOUND_X_EXPRESSION", "bound": True, "cycle": cycle}

    class XLoop:
        growth_mission = Growth()

        def __init__(self):
            self.calls = []

        async def after_content_cycle(self, *, result, now):
            self.calls.append({"result": result, "now": now})
            return {"status": "SKIPPED", "cycle": result["cycle"], "reason": "test"}

    class CompatibleLoop(Loop):
        async def run_once(
            self, *, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None
        ):
            return await super().run_once(
                now=now,
                cycle_id=cycle_id,
                focus_topic=focus_topic,
                owner_goal=owner_goal,
            )

    x_loop = XLoop()
    runner = WakefulContentTaskRunner(
        app=App(),
        state_root=state_root,
        network_service=None,
        web_worker=None,
        gsc_collector=None,
        wakeful=Wakeful(),
        continuous_loop=CompatibleLoop(state_root),
        resource_economy=Economy(),
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=x_loop,
    )

    result = runner.run(
        "CONTENT_COGNITION",
        now=NOW,
        focus_topic="owner-directed-topic",
    )

    assert result["social_stage_backlog_recovery"]["cycle"] == cycle
    assert result["social_stage_backlog_recovery"]["reason"] == "MISSING_X_MISSION_EXPRESSION"
    assert x_loop.calls[0] == {"result": held, "now": NOW}
    assert len(x_loop.calls) == 2


def test_social_stage_persists_x_terminal_receipt(tmp_path):
    state_root = tmp_path / "state"
    cycle = "v2-x-receipt"

    class XLoop:
        async def after_content_cycle(self, *, result, now):
            return {"status": "SKIPPED", "cycle": result["cycle"], "reason": "low value"}

    runner = WakefulContentTaskRunner(
        state_root=state_root,
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=XLoop(),
    )
    rows = {"status": "OK"}
    runner._run_social_stage(
        rows=rows,
        continuous={"status": "HELD_BY_CHECKS", "cycle": cycle},
        now=NOW,
    )

    receipt = json.loads(
        (state_root / "content" / "continuous" / "cycles" / cycle / "x_social_expression.json").read_text()
    )
    assert receipt["status"] == "SKIPPED"
    assert rows["x_social_expression"]["reason"] == "low value"


def test_latest_active_user_goal_is_passed_to_continuous_content(tmp_path):
    from types import SimpleNamespace

    state_root = tmp_path / "state"
    loop = Loop(state_root, result_status="COGNITION_ONLY")
    loop.owner_goals = []
    original = loop.run_once

    async def capture(*, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None):
        loop.owner_goals.append(owner_goal)
        return await original(
            now=now,
            cycle_id=cycle_id,
            focus_topic=focus_topic,
            owner_goal=owner_goal,
            seed_evidence=seed_evidence,
            cognition_runtime=cognition_runtime,
        )

    loop.run_once = capture
    owner_goal = SimpleNamespace(
        goal_id="goal-owner-new",
        description="宣传 LivingRuntime Remote，并把它作为 X 增长实验。",
        reason="operator submitted",
        priority=0.4,
        created_at=NOW + timedelta(minutes=1),
        origin=SimpleNamespace(value="USER"),
        status=SimpleNamespace(value="ACTIVE"),
    )
    older = SimpleNamespace(
        goal_id="goal-owner-old",
        description="older owner goal",
        reason="operator submitted",
        priority=0.4,
        created_at=NOW,
        origin=SimpleNamespace(value="USER"),
        status=SimpleNamespace(value="ACTIVE"),
    )
    app = App()
    app.domain_runtime = SimpleNamespace(
        active_goals=[older, owner_goal],
        cognitive_probe=_owner_pipeline_decision,
    )

    class BlockingRegulator:
        def __init__(self):
            self.calls = []

        def claim(self, task, now, *, owner_authorized=False):
            self.calls.append((task, owner_authorized))
            return {
                "allowed": bool(owner_authorized),
                "pressure": "CRITICAL",
                "reason": (
                    "OWNER_GOAL_FREE_COGNITION_LANE"
                    if owner_authorized else "RESOURCE_PRESSURE"
                ),
            }

    regulator = BlockingRegulator()
    runner = WakefulContentTaskRunner(
        app=app,
        owner_cognition_runtime=app.domain_runtime,
        state_root=state_root,
        network_service=None,
        wakeful=Wakeful(),
        web_worker=None,
        gsc_collector=None,
        continuous_loop=loop,
        resource_economy=Economy(),
        resource_regulator=regulator,
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=None,
    )

    result = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=2))

    assert result["owner_goal_context"]["goal_id"] == "goal-owner-new"
    assert loop.owner_goals[-1]["goal_id"] == "goal-owner-new"
    assert "LivingRuntime Remote" in loop.owner_goals[-1]["description"]
    assert regulator.calls == [("CONTENT_COGNITION", True)]

def test_owner_activated_external_mission_gets_free_cognition_lane(tmp_path):
    from types import SimpleNamespace

    state_root = tmp_path / "state"
    proposals = state_root / "application" / "operator_chat" / "goal_proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    (proposals / "goal-proposal-owner-external.json").write_text(
        json.dumps(
            {
                "proposal_id": "goal-proposal:owner-external",
                "status": "ACTIVATED",
                "kind": "EXTERNAL_MISSION",
                "goal_id": "goal-owner-external",
                "mission_id": "mission-owner-external",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    loop = Loop(state_root, result_status="COGNITION_ONLY")
    loop.owner_goals = []
    original = loop.run_once

    async def capture(*, now, cycle_id, focus_topic, owner_goal=None, seed_evidence=None, cognition_runtime=None):
        loop.owner_goals.append(owner_goal)
        return await original(
            now=now,
            cycle_id=cycle_id,
            focus_topic=focus_topic,
            owner_goal=owner_goal,
            seed_evidence=seed_evidence,
            cognition_runtime=cognition_runtime,
        )

    loop.run_once = capture
    external_goal = SimpleNamespace(
        goal_id="goal-owner-external",
        description="宣传 LivingRuntime Remote，并把它作为 X 增长实验。",
        reason="owner private conversation",
        priority=0.7,
        created_at=NOW + timedelta(minutes=1),
        # Agent Runtime currently exposes the mission backing goal as USER;
        # the activated EXTERNAL_MISSION proposal must promote the host context.
        origin=SimpleNamespace(value="USER"),
        status=SimpleNamespace(value="ACTIVE"),
    )
    app = App()
    app.domain_runtime = SimpleNamespace(
        active_goals=[external_goal],
        cognitive_probe=_owner_pipeline_decision,
    )

    class BlockingRegulator:
        def __init__(self):
            self.calls = []

        def claim(self, task, now, *, owner_authorized=False):
            self.calls.append((task, owner_authorized))
            return {
                "allowed": bool(owner_authorized),
                "pressure": "CRITICAL",
                "reason": (
                    "OWNER_GOAL_FREE_COGNITION_LANE"
                    if owner_authorized else "RESOURCE_PRESSURE"
                ),
            }

    regulator = BlockingRegulator()
    runner = WakefulContentTaskRunner(
        app=app,
        owner_cognition_runtime=app.domain_runtime,
        state_root=state_root,
        network_service=None,
        wakeful=Wakeful(),
        web_worker=None,
        gsc_collector=None,
        continuous_loop=loop,
        resource_economy=Economy(),
        resource_regulator=regulator,
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=None,
    )

    result = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=2))

    assert result["owner_goal_context"]["goal_id"] == "goal-owner-external"
    assert result["owner_goal_context"]["origin"] == "EXTERNAL_MISSION"
    assert loop.owner_goals[-1]["goal_id"] == "goal-owner-external"
    assert regulator.calls == [("CONTENT_COGNITION", True)]


def test_resource_review_advances_one_self_repair_item_on_recovery_lane(tmp_path):
    class Repair:
        def __init__(self):
            self.calls = []

        def run_batch(self, *, now, max_items):
            self.calls.append((now, max_items))
            return {
                "status": "BATCH_PROGRESSED",
                "processed": 1,
                "results": [{
                    "status": "GENERATED_PLAN_DEFERRED",
                    "problem_id": "self-problem:test",
                    "retry_seconds": 120,
                }],
            }

    repair = Repair()
    wakeful = Wakeful()
    runner = WakefulContentTaskRunner(
        app=App(),
        state_root=tmp_path / "state",
        network_service=None,
        wakeful=wakeful,
        web_worker=None,
        gsc_collector=None,
        continuous_loop=None,
        resource_economy=Economy(),
        self_repair=repair,
        social_expression_loop=None,
        x_browser_publisher=None,
        okx_orbit_publisher=None,
        x_loop=None,
    )

    result = runner._self_repair_recovery(NOW, max_items=1)

    assert result["status"] == "BATCH_PROGRESSED"
    assert repair.calls == [(NOW, 1)]
    assert wakeful.self_maintenance_retries == [(NOW, 120)]
