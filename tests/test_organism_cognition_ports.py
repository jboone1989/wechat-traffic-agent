"""Bounded cognition owner scope and organism observation/calculation ports."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.cognition_scope_planner import CognitionScopePlanner
from wechat_traffic_agent.organism_observations import OrganismObservationReader
from wechat_traffic_agent.organism_signal_calculator import (
    OrganismInputs, OrganismSignalCalculator,
)

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_owner_scope_preserves_only_matching_missions_and_intents():
    goal = {"goal_id": "g1", "mission_id": "m1", "description": "Test memory causality"}
    other = {"goal_id": "g2", "mission_id": "m2", "description": "Unrelated rates"}
    data = {
        "active": [goal, other],
        "recent_experiments": [
            {"mission_id": "m1", "strategy": "ablation"},
            {"mission_id": "m2", "strategy": "macro"},
        ],
    }
    selected = CognitionScopePlanner().prepare(
        owner_scope={"goal_id": "g1", "mission_id": "m1",
                     "description": "Test memory causality", "topic": "agent memory"},
        owner_mode=True, prompt="OWNER_AGENT_EPISODE current goal action evidence rates",
        mission_state=data,
        current_intent=[
            {"goal_id": "g1", "kind": "GOAL"},
            {"goal_id": "g2", "kind": "GOAL"},
            {"goal_id": "g1", "kind": "MISSION"},
        ],
        projection={"latest_plan": {"owner_goal_id": "g2", "goal": {"goal_id": "g2"}}},
        compact_latest_plan=lambda row: row,
    )
    assert [row["mission_id"] for row in selected.missions["active"]] == ["m1"]
    assert [row["mission_id"] for row in selected.missions["recent_experiments"]] == ["m1"]
    assert [row["goal_id"] for row in selected.intents] == ["g1"]
    assert selected.intents[0]["authority"] == "OWNER_OBJECTIVE"
    assert "rates" not in selected.query
    assert "agent memory" in selected.query
    assert selected.latest_plan is None
    assert len(data["active"]) == 2  # No mutation of the source state.


def test_organism_evidence_reader_uses_injected_io_and_returns_no_mutation():
    observed = NOW.isoformat()
    data = {
        "application/survival_resources/policy.json": {
            "observed_at": observed, "pressure": "NORMAL",
        },
        "application/llm_resource_pool/policy.json": {
            "observed_at": observed, "pressure": "CONSERVE",
            "paid_available_funds": 0, "paid_reserve_floor": 1,
        },
        "application/llm_resource_pool/resources.json": {
            "observed_at": observed,
            "resources": [{"quality_score": 0.9, "success_rate": 0.9,
                           "pricing_class": "FREE", "quota_limit": 10,
                           "quota_remaining": 10}],
        },
        "application/survival_resources/resources.json": {
            "observed_at": observed,
            "resources": [{"domain": "SERVICE", "kind": "PRIMARY_STATEFUL",
                           "status": "ACTIVE"}],
        },
        "application/world_learning.json": {
            "actions": {"a": {"settlements": {
                "s": {"observed_at": observed, "learning_signal": 0.8},
            }}},
        },
    }
    reads = []
    def read(path):
        rel = str(path.relative_to(Path("/test")))
        reads.append(rel)
        return data.get(rel, {})
    reader = OrganismObservationReader(
        Path("/test"), read=read,
        parse_time=lambda text: datetime.fromisoformat(text) if text else None,
        clamp=lambda value: min(1, max(0, value)),
        pressure=lambda text: {"NORMAL": 0.1, "CONSERVE": 0.68}.get(text),
    )
    resources, rewards = reader.read_observations(NOW)
    assert resources.host_pressure == 0.1
    assert resources.free_model_availability == 0.81
    assert resources.service_health == 1.0
    assert rewards.recent_reward == 0.9
    assert rewards.reward_confidence == 0.125
    assert len(reads) == 5
    assert "application/world_learning.json" in reads


def test_organism_signal_calculator_does_not_read_or_write_persistent_state():
    calls = []
    def signal(**kwargs):
        calls.append(kwargs["name"])
        return {
            "value": kwargs["value"], "freshness": (
                "FRESH" if kwargs["observed_at"] is not None
                and kwargs["value"] is not None else "UNKNOWN"
            ),
        }
    from wechat_traffic_agent.organism_observations import (
        ResourceObservations, RewardObservations,
    )
    resources = ResourceObservations(
        NOW, 0.1, NOW, NOW, 0.85, 0.85, 1.0, 0.1, 0.15,
        NOW, 1.0, None,
    )
    rewards = RewardObservations([], NOW, 0.5, 0.5, 0.25)
    calculator = OrganismSignalCalculator(
        signal=signal, clamp=lambda value: max(0, min(1, value)),
        pressure=lambda value: {"WATCH": 0.38}.get(value),
        parse_time=lambda value: datetime.fromisoformat(value) if value else None,
    )
    observation = OrganismInputs(
        NOW, {"updated_at": NOW.isoformat(),
              "affect": {"curiosity": 0.5, "arousal": 0.1},
              "body": {"cpu_pressure": 0.1, "memory_pressure": 0.1}},
        "WATCH", 1, NOW - timedelta(hours=1), NOW - timedelta(hours=1),
        resources, rewards,
    )
    signals, factors = calculator.calculate(observation)
    summary = calculator.summarize(signals, observation, factors)
    assert "compute_pressure" in signals
    assert "network_health" in signals
    assert "reward" not in calls and "recent_reward" in calls
    assert signals["network_health"]["freshness"] == "UNKNOWN"
    assert summary["status"] == "UNKNOWN"
    assert "network_health" in summary["unknown_signals"]


def test_organism_and_scope_modules_do_not_import_the_cycle_runner():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for name in (
        "organism_observations.py", "organism_signal_calculator.py",
        "cognition_scope_planner.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "content_goal_loop" not in str(node.module), name
                assert "live_agent" not in str(node.module), name
                assert "organism_state" not in str(node.module), name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    name, node.name, node.end_lineno - node.lineno + 1,
                )
    for file, klass, fn, limit in (
        ("organism_state.py", "OrganismStateService", "refresh", 50),
    ):
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == klass)
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == fn)
        assert method.end_lineno - method.lineno + 1 <= limit
    source = ast.parse((root / "cognition_context.py").read_text(encoding="utf-8"))
    fn = next(n for n in source.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "build_ferro_cognition_context")
    assert fn.end_lineno - fn.lineno + 1 <= 400
