from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from wechat_traffic_agent.longitudinal_individuality import (
    LongitudinalIndividuality,
    MONTH_LOOKBACK_DAYS,
)


NOW = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
ORGANISM = "ferro-organism:test-continuity"


def cortex(model: str = "model-a"):
    return {
        "organism_id": ORGANISM,
        "current_cortex": {
            "class": "OPENAI_COMPATIBLE",
            "provider_name": "test-provider",
            "model": model,
            "base_url": "https://example.invalid/v1",
            "selected_resource_id": "resource:test",
        },
    }


def personality(**overrides):
    traits = {
        "novelty_seeking": 0.50,
        "persistence": 0.50,
        "sociality": 0.50,
        "caution": 0.50,
        "independence": 0.50,
        "playfulness": 0.50,
    }
    traits.update(overrides)
    return {"traits": traits}


def social(*, actor="actor:aaa", interactions=1, familiarity=0.1, valence=0.0):
    return {
        "relationships": {
            actor: {
                "actor": actor,
                "interactions": interactions,
                "familiarity": familiarity,
                "valence": valence,
                "approach_bias": 0.5 + 0.2 * valence,
                "first_seen_at": NOW.isoformat(),
                "last_seen_at": NOW.isoformat(),
            }
        }
    }


def motor(*, habit=False, meaning="OBSERVE_WORLD"):
    unit = {
        "motor_id": "motor_003",
        "state": "HABIT" if habit else "LEARNING",
        "meaning": meaning if habit else None,
        "associations": {},
    }
    if habit:
        unit["associations"] = {
            meaning: {
                "trials": 8,
                "mean_reward": 0.75,
            }
        }
    return {"units": {"motor_003": unit}}


def dreams(*, dream_count=0, stable=False, signature="curiosity|opportunity"):
    attractors = {}
    if stable:
        attractors["attractor:test"] = {
            "attractor_id": "attractor:test",
            "signature": signature,
            "status": "STABLE",
            "recurrence_count": 4,
            "stability_score": 0.72,
            "channels": signature.split("|"),
        }
    return {
        "dream_count": dream_count,
        "attractors": attractors,
    }


def neural_graph(
    *,
    synthetic=0,
    birth_seq=0,
    event_seq=0,
    mutation_count=0,
    organs=(),
):
    return {
        "substrate": {
            "source_fingerprint": "sha256:ancestral-test",
            "neuron_count": 166_700 + synthetic,
            "developmental_neurogenesis": {
                "synthetic_neuron_count": synthetic,
                "birth_seq": birth_seq,
                "event_seq": event_seq,
                "species_stage": (
                    "DROSOPHILA_FERRO_F0"
                    if synthetic == 0
                    else "FERRO_F0_DEVELOPING"
                ),
                "developmental_genome": {
                    "fingerprint": f"sha256:genome-{mutation_count}",
                    "mutation_count": mutation_count,
                },
                "circuits": [],
                "organs": [
                    {
                        "organ_id": organ_id,
                        "state": "EMERGENT_ORGAN",
                    }
                    for organ_id in organs
                ],
            },
        }
    }


def observe(
    ledger: LongitudinalIndividuality,
    *,
    now: datetime,
    model="model-a",
    person=None,
    social_state=None,
    motor_state=None,
    dream_state=None,
    graph=None,
):
    return ledger.observe(
        now=now,
        personality=person or personality(),
        social_state=social_state or social(),
        motor_state=motor_state or motor(),
        dream_state=dream_state or dreams(),
        cortex=cortex(model),
        neural_graph=graph or neural_graph(),
        force=True,
    )


def test_same_organism_can_change_visibly_over_one_month(tmp_path: Path):
    ledger = LongitudinalIndividuality(tmp_path)

    first = observe(ledger, now=NOW)
    later = observe(
        ledger,
        now=NOW + timedelta(days=MONTH_LOOKBACK_DAYS + 2),
        person=personality(
            novelty_seeking=0.82,
            persistence=0.74,
            sociality=0.68,
            caution=0.37,
            independence=0.79,
            playfulness=0.71,
        ),
        social_state={
            "relationships": {
                **social(
                    actor="actor:aaa",
                    interactions=18,
                    familiarity=0.72,
                    valence=0.45,
                )["relationships"],
                **social(
                    actor="actor:bbb",
                    interactions=7,
                    familiarity=0.41,
                    valence=0.20,
                )["relationships"],
            }
        },
        motor_state=motor(habit=True, meaning="OBSERVE_WORLD"),
        dream_state=dreams(
            dream_count=14,
            stable=True,
            signature="curiosity|opportunity",
        ),
        graph=neural_graph(
            synthetic=9,
            birth_seq=9,
            event_seq=400,
            mutation_count=5,
            organs=("organ_001",),
        ),
    )

    assert first["status"] == "SNAPSHOT_RECORDED"
    assert later["status"] == "SNAPSHOT_RECORDED"
    projection = ledger.projection()
    comparison = projection["month_comparison"]
    assert comparison["status"] == "LONGITUDINAL_COMPARISON_READY"
    assert comparison["ready"] is True
    assert comparison["same_organism"] is True
    assert comparison["individuality_changed"] is True
    assert comparison["change_score"] > 0.10
    assert comparison["dream_count_growth"] == 14
    assert projection["organism_id"] == ORGANISM


def test_cortex_swap_is_context_not_individuality_identity(tmp_path: Path):
    ledger = LongitudinalIndividuality(tmp_path)

    first = observe(
        ledger,
        now=NOW,
        model="model-a",
        person=personality(persistence=0.63, sociality=0.57),
        social_state=social(interactions=5, familiarity=0.35),
        motor_state=motor(habit=True),
        dream_state=dreams(dream_count=3, stable=True),
        graph=neural_graph(
            synthetic=2,
            birth_seq=2,
            event_seq=40,
            mutation_count=1,
        ),
    )
    second = observe(
        ledger,
        now=NOW + timedelta(days=1),
        model="model-b",
        person=personality(persistence=0.63, sociality=0.57),
        social_state=social(interactions=5, familiarity=0.35),
        motor_state=motor(habit=True),
        dream_state=dreams(dream_count=3, stable=True),
        graph=neural_graph(
            synthetic=2,
            birth_seq=2,
            event_seq=40,
            mutation_count=1,
        ),
    )

    assert first["snapshot"]["individuality_fingerprint"] == second["snapshot"]["individuality_fingerprint"]
    assert first["snapshot"]["cortex_key"] != second["snapshot"]["cortex_key"]
    projection = ledger.projection()
    assert projection["cortex_transition_count"] == 1
    transition = projection["recent_cortex_transitions"][0]
    assert transition["status"] == "PRESERVED"
    assert all(transition["checks"].values())
    assert projection["cortex_in_individuality_fingerprint"] is False


def test_different_histories_from_same_identity_can_clearly_diverge(tmp_path: Path):
    left = LongitudinalIndividuality(tmp_path / "left")
    right = LongitudinalIndividuality(tmp_path / "right")

    observe(left, now=NOW)
    observe(right, now=NOW)

    observe(
        left,
        now=NOW + timedelta(days=10),
        person=personality(
            novelty_seeking=0.90,
            persistence=0.84,
            sociality=0.75,
            caution=0.28,
            independence=0.88,
            playfulness=0.82,
        ),
        social_state={
            "relationships": {
                **social(
                    actor="actor:left-a",
                    interactions=30,
                    familiarity=0.9,
                    valence=0.7,
                )["relationships"],
                **social(
                    actor="actor:left-b",
                    interactions=20,
                    familiarity=0.8,
                    valence=0.5,
                )["relationships"],
            }
        },
        motor_state=motor(habit=True, meaning="NEURAL_PLAY"),
        dream_state=dreams(
            dream_count=20,
            stable=True,
            signature="curiosity|social_attention",
        ),
        graph=neural_graph(
            synthetic=16,
            birth_seq=16,
            event_seq=800,
            mutation_count=8,
            organs=("organ_001", "organ_002"),
        ),
    )
    observe(
        right,
        now=NOW + timedelta(days=10),
        person=personality(
            novelty_seeking=0.25,
            persistence=0.31,
            sociality=0.18,
            caution=0.91,
            independence=0.32,
            playfulness=0.20,
        ),
        social_state=social(
            actor="actor:right-a",
            interactions=2,
            familiarity=0.12,
            valence=-0.4,
        ),
        motor_state=motor(habit=True, meaning="RESOURCE_REVIEW"),
        dream_state=dreams(
            dream_count=2,
            stable=True,
            signature="resource_pressure|threat_risk",
        ),
        graph=neural_graph(
            synthetic=1,
            birth_seq=1,
            event_seq=90,
            mutation_count=0,
        ),
    )

    comparison = LongitudinalIndividuality.compare_histories(
        left.state["snapshots"],
        right.state["snapshots"],
    )
    assert comparison["status"] == "COMPARED"
    assert comparison["same_organism"] is True
    assert comparison["clearly_diverged"] is True
    assert comparison["divergence_score"] >= 0.20


def test_daily_cadence_does_not_manufacture_extra_history(tmp_path: Path):
    ledger = LongitudinalIndividuality(tmp_path)

    first = ledger.observe(
        now=NOW,
        personality=personality(),
        social_state=social(),
        motor_state=motor(),
        dream_state=dreams(),
        cortex=cortex(),
        neural_graph=neural_graph(),
    )
    second = ledger.observe(
        now=NOW + timedelta(hours=2),
        personality=personality(novelty_seeking=0.9),
        social_state=social(interactions=10),
        motor_state=motor(habit=True),
        dream_state=dreams(dream_count=10, stable=True),
        cortex=cortex(),
        neural_graph=neural_graph(synthetic=5, birth_seq=5),
    )

    assert first["status"] == "SNAPSHOT_RECORDED"
    assert second["status"] == "SNAPSHOT_NOT_DUE"
    assert ledger.projection()["snapshot_count"] == 1


def test_public_projection_does_not_expose_relationship_actor_ids(tmp_path: Path):
    ledger = LongitudinalIndividuality(tmp_path)
    observe(
        ledger,
        now=NOW,
        social_state=social(
            actor="actor:private-pseudonym",
            interactions=9,
            familiarity=0.6,
        ),
    )

    projection = ledger.projection()
    raw = json.dumps(projection, sort_keys=True)
    assert "actor:private-pseudonym" not in raw
    assert projection["latest"]["relationship_count"] == 1
    assert projection["latest"]["relationship_signature"].startswith("sha256:")
    assert projection["raw_relationship_ids_public"] is False


def test_restart_preserves_longitudinal_history(tmp_path: Path):
    ledger = LongitudinalIndividuality(tmp_path)
    observe(ledger, now=NOW)
    observe(
        ledger,
        now=NOW + timedelta(days=1),
        person=personality(persistence=0.66),
    )

    restored = LongitudinalIndividuality(tmp_path)
    projection = restored.projection()
    assert projection["snapshot_count"] == 2
    assert projection["organism_id"] == ORGANISM
    assert projection["latest"]["personality_traits"]["persistence"] == 0.66
