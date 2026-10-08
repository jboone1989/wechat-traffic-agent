"""Feature-port checks for wakeful neural phases and bounded behavioral rules."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.behavioral_modulation_rules import BehavioralModulationRules
from wechat_traffic_agent.neural_authority_coordinator import NeuralAuthorityCoordinator
from wechat_traffic_agent.neural_continuity_coordinator import NeuralContinuityCoordinator
from wechat_traffic_agent.neural_development_scheduler import NeuralDevelopmentScheduler
from wechat_traffic_agent.neural_development_sensors import NeuralDevelopmentSensors
from wechat_traffic_agent.neural_stimulus_dispatcher import NeuralStimulusDispatcher


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


class Core:
    def __init__(self):
        self.plans = []
        self.events = []
        self.persist_count = 0
        self.reflex = SimpleNamespace(substrate=None)

    def snapshot(self):
        return SimpleNamespace(
            pending_plans=tuple(self.plans),
            neural_state={},
        )

    def schedule(self, plan):
        self.plans.append(plan)

    def emit(self, event, *, enqueue):
        assert enqueue is False
        self.events.append(event)

    def _persist(self):
        self.persist_count += 1


def wake(**kwargs):
    return SimpleNamespace(**kwargs)


def event(**kwargs):
    return SimpleNamespace(**kwargs)


def test_neural_development_scheduler_only_schedules_once_and_respects_owner():
    core = Core()
    agency = SimpleNamespace(next_candidate_world=lambda: {
        "world_id": "w1", "need_id": "n1", "proposal_id": "p1", "trial_count": 0,
    })
    transfer = SimpleNamespace(
        architecture_revalidation_candidate=lambda **kw: None,
        pending=lambda **kw: None,
        projection=lambda: {"worlds": {}},
    )
    calls = []
    evolution = SimpleNamespace(
        refresh=lambda **kw: calls.append("refreshed"),
        next_consolidation=lambda: None,
    )
    service = NeuralDevelopmentScheduler(
        core=core, agency=agency, transfer=transfer,
        evolution=evolution, planned_wake=wake,
    )
    service.refresh(NOW, owner_control_active=True)
    assert core.plans == []
    service.refresh(NOW, owner_control_active=False)
    service.refresh(NOW, owner_control_active=False)
    assert [p.task for p in core.plans] == ["NEURAL_DEVELOPMENT"]
    assert len(calls) == 3


def test_neural_stimulus_dispatcher_emits_drives_and_bounded_dream_channels():
    core = Core()
    stimulated = []
    life = SimpleNamespace(
        should_stimulate=lambda now: True,
        mark_stimulated=lambda now: stimulated.append(now),
    )
    dream = SimpleNamespace(
        claim_waking_influence=lambda **kw: {
            "attractor_id": "dream-1", "magnitude": 0.8,
            "channels": ["curiosity", "memory", "survival", "extra"],
        },
    )
    dispatcher = NeuralStimulusDispatcher(
        core=core, life=life, dream=dream,
        body_currents=lambda *args, **kw: {},
        stimulus_type=event,
    )
    dispatcher.emit(
        NOW, {"drives": {"survival": 0.7, "evolution": 0.2}}, {
            "recent_stimulus_kinds": [],
        },
    )
    assert len(stimulated) == 1
    assert [row.kind for row in core.events] == [
        "INTEROCEPTION_SURVIVAL", "INTEROCEPTION_EVOLUTION",
        "DREAM_CONSOLIDATION", "DREAM_CONSOLIDATION", "DREAM_CONSOLIDATION",
    ]


def test_owner_gate_blocks_neural_motor_and_authority_wakes():
    core = Core()
    mark = []
    life = SimpleNamespace(
        authority_recommendation=lambda now: {
            "task": "CHECK_X", "intention": "EXPLORE", "reason": "test",
        },
        mark_authority_action=lambda *a, **kw: mark.append("authority"),
        projection=lambda: {"intention": {"name": "EXPLORE"}},
    )
    motor_selected = []
    motor = SimpleNamespace(
        select=lambda **kwargs: motor_selected.append("motor")
        or {"task": "CONTENT_COGNITION", "decision_id": "d1"},
    )
    service = NeuralAuthorityCoordinator(
        core=core, life=life,
        brain_cortex=SimpleNamespace(projection=lambda: {"scoreboard": {}}),
        personality=SimpleNamespace(refresh=lambda **kw: None),
        brain_lab=SimpleNamespace(projection=lambda: {}),
        social=SimpleNamespace(projection=lambda: {}),
        motor=motor,
        regulator=SimpleNamespace(pressure=lambda now: "NORMAL"),
        planned_wake=wake, x_enabled=False,
    )
    service.refresh(NOW, {"intention": {"name": "EXPLORE"}}, True)
    assert core.plans == [] and motor_selected == [] and mark == []
    service.refresh(NOW, {"intention": {"name": "EXPLORE"}}, False)
    assert [row.task for row in core.plans] == ["CONTENT_COGNITION", "CONTENT_COGNITION"]
    assert motor_selected == ["motor"]


def test_developmental_sensors_emit_active_needs_and_sampled_currents():
    core = Core()
    agency = SimpleNamespace(refresh=lambda **kw: {
        "needs": [
            {"need_id": "n1", "status": "ACTIVE", "neural_pressure": 0.6,
             "pressure_channels": ["curiosity"], "severity": 0.7},
            {"need_id": "n2", "status": "PASSIVE", "neural_pressure": 0.8,
             "pressure_channels": ["attention"], "severity": 0.9},
        ],
    })
    extensions = SimpleNamespace(
        refresh=lambda **kw: {},
        sample_active_sensors=lambda **kw: [{
            "sensor_id": "sensor-1", "extension_id": "ext-1",
            "currents": {"curiosity": 0.5},
        }],
    )
    service = NeuralDevelopmentSensors(
        core=core, agency=agency,
        transfer=SimpleNamespace(projection=lambda: {}),
        motor=SimpleNamespace(projection=lambda: {}),
        history=SimpleNamespace(projection=lambda: {}),
        extensions=extensions, stimulus_type=event,
    )
    service.observe(NOW, {"intention": {"name": "EXPLORE"}})
    assert [row.kind for row in core.events] == [
        "DEVELOPMENT_NEED", "SELF_EXTENDED_SENSOR",
    ]


def test_continuity_observer_settles_new_due_set_once():
    core = Core()
    goal_wakes, closure_calls = [], []
    empty_projection = lambda: {}
    coordinator = NeuralContinuityCoordinator(
        core=core,
        cortex=SimpleNamespace(observe=lambda **kw: {},
                               projection=empty_projection),
        individuality=SimpleNamespace(observe=lambda **kw: None),
        motor=SimpleNamespace(projection=empty_projection, state={}),
        personality=SimpleNamespace(projection=empty_projection),
        social=SimpleNamespace(state={}),
        dream=SimpleNamespace(state={}),
        resource_regulator=SimpleNamespace(pressure=lambda now: "NORMAL"),
        organism_state=object(), drive_system=object(),
        developmental_history=object(), autonomous_life=object(),
        self_model=object(),
        world_learning=SimpleNamespace(due=lambda now: [
            {"action_id": "a1", "window": "1h", "due_at": NOW.isoformat()},
        ]),
        active_goal_count=lambda: 0,
        ensure_goal_wakes=lambda now: goal_wakes.append(now),
        planned_wake=wake,
        organism_closure=lambda **kw: closure_calls.append(kw),
    )
    signature = coordinator.refresh(
        now=NOW, projection={"drives": {}},
        owner_control_active=False, previous_signature=(),
    )
    signature_again = coordinator.refresh(
        now=NOW, projection={"drives": {}},
        owner_control_active=False, previous_signature=signature,
    )
    assert signature_again == signature
    assert [row.task for row in core.plans] == ["CHECK_OUTCOMES"]
    assert len(goal_wakes) == len(closure_calls) == core.persist_count == 2


def test_behavioral_rules_pure_and_short_feature_methods():
    output = BehavioralModulationRules().compile(
        affect={"curiosity": 0.9, "confidence": 0.7},
        traits={"independence": 0.95, "persistence": 0.8},
        drives={"evolution": 0.9}, conflict=0.1,
        relation={"familiarity": 0.8, "approach_bias": 0.7},
        dominant_drive={"name": "evolution", "value": 0.9},
    )
    directives, targets, profile, salient = output
    assert any("mirror" in text for text in directives)
    assert any("learning" in text for text in directives)
    assert len(targets) <= 4 and len({row["source"] for row in targets}) == len(targets)
    assert profile["assertiveness"]["value"] > 0.5


def test_neural_feature_modules_do_not_import_wakeful_entrypoint():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for filename in (
        "behavioral_modulation_rules.py", "neural_stimulus_dispatcher.py",
        "neural_development_scheduler.py", "neural_authority_coordinator.py",
        "neural_development_sensors.py", "neural_continuity_coordinator.py",
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert str(node.module) not in {"wakeful_runtime", "content_goal_loop", "live_agent"}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    filename, node.name, node.end_lineno - node.lineno + 1,
                )
    for filename, name, limit in (
        ("wakeful_runtime.py", "refresh_neural_life", 100),
        ("behavioral_state.py", "build_behavioral_state", 100),
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == name)
        assert method.end_lineno - method.lineno + 1 <= limit
