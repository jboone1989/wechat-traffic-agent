from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.screeps_training import (
    EPISODE_SCHEMA,
    MIN_SAMPLES_PER_ARM,
    ScreepsResearcher,
)
from wechat_traffic_agent.world_learning import WorldLearningLedger


NOW = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)


def _good_metrics(**overrides):
    row = {
        "survived": True,
        "owned_rooms_start": 1,
        "owned_rooms_end": 1,
        "energy_start": 1000,
        "energy_end": 1600,
        "controller_progress_start": 1000,
        "controller_progress_end": 1500,
        "hostile_creeps_destroyed": 3,
        "own_creeps_lost": 1,
        "structures_start": 5,
        "structures_end": 7,
    }
    row.update(overrides)
    return row


def _settle(engine: ScreepsResearcher, trial: dict, metrics: dict, index: int = 0):
    return engine.settle_episode(
        {
            "schema_version": EPISODE_SCHEMA,
            "episode_id": trial["episode_id"],
            "evidence_ref": f"match-replay:{trial['episode_id']}:{index}",
            "metrics": metrics,
        },
        now=NOW + timedelta(minutes=index + 1),
    )


def test_screeps_reward_requires_measured_world_metrics(tmp_path):
    reward, factors = ScreepsResearcher.score_metrics(_good_metrics())
    assert reward > 0
    assert factors["energy"] > 0
    assert factors["combat_exchange"] > 0

    win_reward, win_factors = ScreepsResearcher.score_metrics(
        _good_metrics(match_outcome=1)
    )
    loss_reward, loss_factors = ScreepsResearcher.score_metrics(
        _good_metrics(match_outcome=-1)
    )
    assert win_reward > 0 > loss_reward
    assert win_factors["match_outcome"] == 1.0
    assert loss_factors["match_outcome"] == -1.0

    bad_reward, bad_factors = ScreepsResearcher.score_metrics(
        _good_metrics(
            survived=False,
            owned_rooms_end=0,
            energy_end=0,
            hostile_creeps_destroyed=0,
            own_creeps_lost=8,
            structures_end=0,
        )
    )
    assert bad_reward < 0
    assert bad_factors["survival"] == -1.0


def test_episode_settles_generic_world_learning_with_external_evidence(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="simplebot-A",
        seed=7,
        now=NOW,
    )
    outcome = _settle(engine, trial, _good_metrics())
    assert outcome.reward > 0

    world = json.loads((tmp_path / "application/world_learning.screeps.json").read_text())
    action = world["actions"][trial["world_action_id"]]
    settlement = action["settlements"]["episode"]
    assert settlement["status"] == "SETTLED"
    assert settlement["actual_utility"] == outcome.reward
    assert settlement["evidence_refs"] == [f"match-replay:{trial['episode_id']}:0"]

    projection = engine.projection()
    assert projection["episode_count"] == 1
    assert projection["function_success_is_reward"] is False
    assert projection["prospective_validation_required"] is True


def test_duplicate_result_file_does_not_double_count(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="simplebot-A",
        seed=8,
        now=NOW,
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": EPISODE_SCHEMA,
                "episode_id": trial["episode_id"],
                "evidence_ref": "replay:8",
                "metrics": _good_metrics(),
            }
        )
    )
    first = engine.ingest_result_file(result_path, now=NOW + timedelta(minutes=1))
    second = engine.ingest_result_file(result_path, now=NOW + timedelta(minutes=2))
    assert first["status"] == "INGESTED"
    assert second["status"] == "ALREADY_INGESTED"
    assert engine.projection()["episode_count"] == 1


def test_screeps_uses_namespaced_world_ledger(tmp_path):
    generic = WorldLearningLedger(tmp_path)
    engine = ScreepsResearcher(tmp_path)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="simplebot-A",
        seed=9,
        now=NOW,
    )
    assert generic.path.name == "world_learning.json"
    assert (tmp_path / "application/world_learning.screeps.json").exists()
    assert not (tmp_path / "application/world_learning.json").exists()
    assert trial["world_action_id"]


def test_harness_failure_cancels_without_reward(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="simplebot-A",
        seed=10,
        now=NOW,
    )
    cancelled = engine.abandon_episode(
        trial["episode_id"],
        reason="HARNESS_EXIT_1",
        evidence_ref="harness-failure:test",
        now=NOW + timedelta(minutes=1),
    )
    assert cancelled["status"] == "CANCELLED_WITHOUT_REWARD"
    projection = engine.projection()
    assert projection["episode_count"] == 0
    assert projection["harness_failure_count"] == 1
    ledger = json.loads((tmp_path / "application/world_learning.screeps.json").read_text())
    action = ledger["actions"][trial["world_action_id"]]
    assert action["status"] == "CANCELLED"
    assert action["settlements"]["episode"]["learning_signal"] is None


def test_researcher_creates_bounded_challenger_only_after_real_history(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    for index in range(4):
        trial = engine.begin_episode(
            strategy_id="balanced-v1",
            opponent_id=f"simplebot-{index}",
            seed=index,
            now=NOW + timedelta(minutes=index * 2),
        )
        _settle(
            engine,
            trial,
            _good_metrics(
                hostile_creeps_destroyed=0,
                own_creeps_lost=5,
            ),
            index=index,
        )

    projection = engine.projection()
    research = projection["prospective_trial"]
    assert research["status"] == "ACTIVE"
    assert research["baseline_strategy_id"] == "balanced-v1"
    assert research["challenger_strategy_id"].startswith("screeps-mutant-g")
    challenger = engine.strategy(research["challenger_strategy_id"])
    assert challenger["soldier_target"] == 2
    assert challenger["aggression"] < engine.strategy("balanced-v1")["aggression"]


def test_challenger_needs_prospective_matches_before_promotion(tmp_path):
    engine = ScreepsResearcher(tmp_path)

    # Seed enough incumbent history to create a challenger.
    for index in range(4):
        trial = engine.begin_episode(
            strategy_id="balanced-v1",
            opponent_id="seed-opponent",
            seed=index,
            now=NOW + timedelta(minutes=index * 2),
        )
        _settle(engine, trial, _good_metrics(energy_end=900), index=index)

    research = engine.projection()["prospective_trial"]
    assert research["status"] == "ACTIVE"
    challenger_id = research["challenger_strategy_id"]

    # The active prospective trial alternates baseline/challenger assignments.
    # Challenger gets clearly better real-world outcomes.
    for index in range(MIN_SAMPLES_PER_ARM * 2):
        choice = engine.choose_strategy(seed=index)
        trial = engine.begin_episode(
            strategy_id=choice["strategy_id"],
            opponent_id="trial-opponent",
            seed=100 + index,
            now=NOW + timedelta(hours=1, minutes=index * 2),
        )
        metrics = (
            _good_metrics(energy_end=2200, hostile_creeps_destroyed=5, own_creeps_lost=0)
            if choice["strategy_id"] == challenger_id
            else _good_metrics(energy_end=600, hostile_creeps_destroyed=0, own_creeps_lost=3)
        )
        _settle(engine, trial, metrics, index=100 + index)

    final = engine.projection()
    assert final["prospective_trial"]["status"] == "PROMOTED"
    assert final["incumbent_strategy_id"] == challenger_id
    assert final["promotion_count"] == 1
    assert final["generation"] == 1


def test_screeps_real_match_updates_self_play_population(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id="screeps-opponent:raider-v1",
        seed=77,
        now=NOW,
    )
    _settle(
        engine,
        trial,
        _good_metrics(match_outcome=1, hostile_creeps_destroyed=5, own_creeps_lost=0),
        index=77,
    )

    population = engine.projection()["self_play_population"]
    assert population["match_count"] == 1
    assert population["members"]["balanced-v1"]["wins"] == 1
    assert population["members"]["raider-v1"]["losses"] == 1


def test_screeps_population_opponent_rotates_toward_untested_member(tmp_path):
    engine = ScreepsResearcher(tmp_path)
    first = engine.choose_population_opponent("balanced-v1", seed=3)
    trial = engine.begin_episode(
        strategy_id="balanced-v1",
        opponent_id=f"screeps-opponent:{first}",
        seed=3,
        now=NOW,
    )
    _settle(engine, trial, _good_metrics(match_outcome=1), index=3)

    second = engine.choose_population_opponent("balanced-v1", seed=3)
    assert second != first
