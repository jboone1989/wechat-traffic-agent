from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.cognition_context import _mission_state
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _mission():
    return SimpleNamespace(
        mission_id="mission-x",
        goal_id="goal-owner",
        description="Grow the real X audience for LivingRuntime Remote",
        metric="followers",
        baseline=1800,
        target=5000,
        direction=SimpleNamespace(value="INCREASE"),
        status=SimpleNamespace(value="ACTIVE"),
        deadline=None,
        constraints=(),
        issued_by="operator",
    )


def _roadmap():
    return SimpleNamespace(
        roadmap_id="roadmap-2",
        version=2,
        rationale="Research before promotion.",
        steps=[
            SimpleNamespace(
                step_id="step-research",
                mission_id="mission-x",
                title="Research durable execution pain",
                objective="Collect current evidence before publication",
                depends_on_step_ids=[],
                started_at=NOW,
                completed_at=None,
                cancelled_at=None,
            ),
            SimpleNamespace(
                step_id="step-publish",
                mission_id="mission-x",
                title="Publish grounded result",
                objective="Publish only after research",
                depends_on_step_ids=["step-research"],
                started_at=None,
                completed_at=None,
                cancelled_at=None,
            ),
        ],
    )


class _Missions:
    experiments = []

    def __init__(self, mission):
        self._mission = mission

    def active(self):
        return [self._mission]

    def progress(self, mission_id, now):
        return SimpleNamespace(
            current_value=1810,
            trajectory="BEHIND",
            pressure=0.8,
        )

    def latest_roadmap(self, mission_id):
        return _roadmap()

    def portfolio_schedule(self, *, now, max_parallelism):
        return SimpleNamespace(
            items=(
                SimpleNamespace(
                    mission_id="mission-x",
                    step_id="step-research",
                    status=SimpleNamespace(value="RUNNING"),
                ),
                SimpleNamespace(
                    mission_id="mission-x",
                    step_id="step-publish",
                    status=SimpleNamespace(value="BLOCKED"),
                ),
            )
        )


def test_owner_controller_sees_durable_roadmap_and_dependencies():
    mission = _mission()
    controller = _Missions(mission)
    runtime = SimpleNamespace(
        external_missions=[mission],
        missions=controller,
    )
    runner = WakefulContentTaskRunner(
        app=SimpleNamespace(domain_runtime=runtime),
    )

    context = runner._owner_agent_mission_context(
        {"goal_id": "goal-owner"},
        NOW,
    )

    roadmap = context["roadmap"]
    assert roadmap["roadmap_id"] == "roadmap-2"
    assert roadmap["version"] == 2
    assert roadmap["steps"][0]["status"] == "RUNNING"
    assert roadmap["steps"][1]["status"] == "BLOCKED"
    assert roadmap["steps"][1]["depends_on"] == ["step-research"]


def test_general_cognition_mission_state_sees_same_roadmap():
    mission = _mission()
    controller = _Missions(mission)
    state = _mission_state(
        SimpleNamespace(missions=controller),
        NOW,
    )

    roadmap = state["active"][0]["roadmap"]
    assert roadmap["roadmap_id"] == "roadmap-2"
    assert [row["status"] for row in roadmap["steps"]] == [
        "RUNNING",
        "BLOCKED",
    ]


def test_neural_play_failure_is_isolated_from_service_continuity():
    recorded = []

    class _Substrate:
        def record_error(self, exc, observed_at):
            recorded.append((type(exc).__name__, str(exc), observed_at))

    class _Wakeful:
        brain_lab = SimpleNamespace(substrate=_Substrate())

        def run_brain_lab(self, now, *, steps):
            raise IndexError(
                "boolean index did not match indexed array along axis 0"
            )

    runner = WakefulContentTaskRunner(wakeful=_Wakeful())
    result = runner._neural_play(NOW)

    assert result["task"] == "NEURAL_PLAY"
    assert result["status"] == "ERROR"
    assert result["error_type"] == "IndexError"
    assert result["failure_isolated"] is True
    assert result["service_continuity_preserved"] is True
    assert result["active_game_stage"] == "FAILED_ISOLATED"
    assert recorded[0][0] == "IndexError"


def test_neural_play_enters_long_horizon_adversarial_stage_after_open_world_graduation():
    calls = []

    class _Game:
        def graduation_status(self):
            return {"graduated": True}

        def open_world_graduation_status(self, environments):
            return {"graduated": True, "environments": list(environments)}

        def adversarial_open_world_graduation_status(self, environments):
            return {"graduated": False, "environments": list(environments)}

    class _Brain:
        ENVIRONMENTS = ("PONG", "MAZE", "FORAGING", "ROVER")
        game_intelligence = _Game()

        def foundation_competence(self):
            return {"graduated": True}

        def open_world_curriculum_environments(self, *, limit=4):
            return ("WORLD_0001", "WORLD_0002", "WORLD_0003")

        def adversarial_open_world_environments(self, *, limit=4):
            return ("ADV_WORLD_0001", "ADV_WORLD_0002", "ADV_WORLD_0003", "ADV_WORLD_0004")

        def run_accelerated_arena(self, **kwargs):
            calls.append(kwargs)
            return {"mode": "HEADLESS_ACCELERATED_ARENA"}

    class _Wakeful:
        brain_lab = _Brain()

        def run_brain_lab(self, now, *, steps):
            return {"status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM"}

    runner = WakefulContentTaskRunner(wakeful=_Wakeful())
    result = runner._neural_play_impl(NOW)

    assert result["active_game_stage"] == "LONG_HORIZON_ADVERSARIAL_OPEN_WORLD"
    assert result["adversarial_open_world_graduation"]["graduated"] is False
    assert calls[-1]["environments"] == (
        "ADV_WORLD_0001",
        "ADV_WORLD_0002",
        "ADV_WORLD_0003",
        "ADV_WORLD_0004",
    )
    assert calls[-1]["sessions_per_environment"] == 3
    assert calls[-1]["base_steps"] == 64


from wechat_traffic_agent.developmental_history import DevelopmentalHistory
from wechat_traffic_agent.organism_closure import refresh_organism_closure
from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime


class _OrganismStub:
    def refresh(self, *, now, **kwargs):
        return {
            "updated_at": now.isoformat(),
            "summary": {
                "vitality": 1.0,
                "status": "NORMAL",
                "unknown_signal_count": 0,
            },
            "signals": {},
        }


class _SelfModelStub:
    def refresh(self, **kwargs):
        return {}


class _WorldLearningStub:
    def projection(self):
        return {}

    def due(self, now):
        return []


class _ClosureSnapshot:
    pending_plans = ()
    last_cognition_at = NOW
    last_action_at = NOW


def test_expired_autonomous_goal_becomes_drive_failure_and_late_result_is_ignored(
    tmp_path,
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:ttl-expiry",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]
    assert runtime._ensure_autonomous_goal_wakes(NOW) == 1

    later = NOW.replace(hour=15)
    refresh_organism_closure(
        now=later,
        snapshot=_ClosureSnapshot(),
        neural_life={"affect": {}, "drives": {}},
        personality={"traits": {}},
        cortex={},
        continuity={},
        resource_pressure="NORMAL",
        organism_state=_OrganismStub(),
        drive_system=runtime.drive_system,
        developmental_history=DevelopmentalHistory(tmp_path),
        autonomous_life=runtime.autonomous_life,
        self_model=_SelfModelStub(),
        world_learning=_WorldLearningStub(),
    )

    expired = next(
        row
        for row in runtime.autonomous_life.projection()["recent_goals"]
        if row["goal_id"] == goal_id
    )
    assert expired["status"] == "EXPIRED"
    assert expired["close_reason"] == "TTL_EXPIRED"
    feedback = runtime.drive_system.projection()["drives"]["resource"]
    assert feedback["last_outcome_status"] == "FAILED"
    assert feedback["failure_streak"] >= 1
    assert not any(
        row.get("task") == "RESOURCE_REVIEW"
        for row in runtime.autonomous_life.projection()["active_goals"]
    )

    runtime._ensure_autonomous_goal_wakes(later)
    assert not any(
        goal_id in tuple(str(ref) for ref in wake.relevant_refs)
        for wake in runtime.core.snapshot().pending_plans
    )
    assert runtime.observe_autonomous_goal_outcome(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": 0.8},
        goal_id=goal_id,
        observed_at=later,
    ) is None
