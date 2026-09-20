from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.neural_identity import (
    BrainCortexLedger,
    NeuralPersonality,
    NeuralSocialSelf,
    NeuralTasteEngine,
    PsychodynamicState,
)

NOW = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


class FakeSubstrate:
    def __init__(self) -> None:
        self.calls = []

    def stimulate(self, currents, observed_at, *, source_event_id=None, feedback_eligible=None):
        self.calls.append((dict(currents), observed_at, source_event_id, feedback_eligible))
        # Deterministic nonlinear-ish projection so labels create different taste.
        total = sum((i + 1) * float(currents.get(k, 0.0)) for i, k in enumerate(sorted(currents)))
        delta = {
            "curiosity": float(currents.get("curiosity", 0.0)) * 8.0 + total * 0.03,
            "opportunity": float(currents.get("opportunity", 0.0)) * 7.0 + total * 0.02,
            "social_attention": float(currents.get("social_attention", 0.0)) * 6.0,
            "prediction_due": float(currents.get("prediction_due", 0.0)) * 5.0,
            "task_urgency": float(currents.get("task_urgency", 0.0)) * 4.0,
            "resource_pressure": float(currents.get("resource_pressure", 0.0)) * 4.0,
            "threat_risk": float(currents.get("threat_risk", 0.0)) * 3.0,
            "error_recovery": float(currents.get("error_recovery", 0.0)) * 2.0,
        }
        return {
            "delta_readout_hz": delta,
            "spikes": 1200 + len(self.calls),
            "active_neurons": 700 + len(self.calls),
            "response_spike_pattern_digest": f"sha256:{len(self.calls):064x}",
        }


def test_neural_taste_is_persistent_and_llm_free(tmp_path: Path):
    substrate = FakeSubstrate()
    engine = NeuralTasteEngine(tmp_path, substrate=substrate)
    result = engine.rank_topics(
        ["agent-memory", "resource-economics", "neural-life"],
        now=NOW,
    )
    assert result["status"] == "RANKED"
    assert result["llm_used"] is False
    assert result["selected_topic"] in {"agent-memory", "resource-economics", "neural-life"}
    assert len(result["ranking"]) == 3
    assert len(substrate.calls) == 3
    restored = NeuralTasteEngine(tmp_path, substrate=substrate)
    assert restored.projection()["ranking_count"] == 1
    assert restored.projection()["last_ranking"]["selected_topic"] == result["selected_topic"]


def test_social_relationship_accumulates_without_storing_raw_actor_id(tmp_path: Path):
    substrate = FakeSubstrate()
    social = NeuralSocialSelf(tmp_path, substrate=substrate)
    first = social.observe(
        actor="browser-session-secret-id",
        category="SOCIAL",
        signal="QUESTION",
        now=NOW,
    )
    second = social.observe(
        actor="browser-session-secret-id",
        category="SOCIAL",
        signal="POSITIVE_FEEDBACK",
        now=NOW + timedelta(minutes=5),
    )
    assert first["actor"].startswith("actor:")
    assert first["actor"] != "browser-session-secret-id"
    assert second["interactions"] == 2
    assert second["familiarity"] > first["familiarity"]
    assert 0.0 <= second["approach_bias"] <= 1.0
    assert social.relationship("browser-session-secret-id")["interactions"] == 2


def test_personality_moves_slowly_from_lived_state(tmp_path: Path):
    personality = NeuralPersonality(tmp_path)
    before = personality.projection()["traits"]
    after = personality.refresh(
        now=NOW,
        neural_life={
            "affect": {
                "curiosity": 0.95,
                "boredom": 0.5,
                "confidence": 0.8,
                "frustration": 0.1,
                "social_need": 0.75,
                "resource_hunger": 0.2,
            },
            "drives": {"evolution": 0.9, "reproduction": 0.7, "survival": 0.25},
        },
        brain_lab={
            "total_sessions": 12,
            "environments": {
                "PONG": {"hits": 8, "misses": 2},
                "MAZE": {"reaches": 3, "collisions": 4},
            },
        },
        social={"recent_relationships": [{"approach_bias": 0.8}]},
        neural_authority_fraction=0.7,
    )
    assert after["prompt_defined"] is False
    assert after["traits"]["novelty_seeking"] > before["novelty_seeking"]
    assert after["traits"]["independence"] > before["independence"]
    # 48h trait time constant prevents one observation from becoming a persona rewrite.
    assert after["traits"]["novelty_seeking"] < 0.55


def test_brain_cortex_contest_is_prospective_and_world_settled(tmp_path: Path):
    ledger = BrainCortexLedger(tmp_path)
    contest = ledger.choose(
        cycle="v2-123",
        neural_choice="neural-life",
        ego_choice="agent-memory",
        now=NOW,
    )
    assert contest["authority"] in {"NEURAL", "EGO"}
    assert contest["executed_choice"] in {"neural-life", "agent-memory"}
    repeated = ledger.choose(
        cycle="v2-123",
        neural_choice="other",
        ego_choice="different",
        now=NOW + timedelta(minutes=1),
    )
    assert repeated["contest_id"] == contest["contest_id"]
    settled = ledger.settle(
        cycle="v2-123",
        utility=0.6,
        evidence_refs=["evidence:real-world"],
        observed_at=NOW + timedelta(hours=24),
    )
    assert settled["status"] == "SETTLED"
    projection = ledger.projection()
    bucket = projection["scoreboard"][contest["authority"]]
    assert bucket["settled"] == 1
    assert bucket["mean_utility"] == 0.6


def test_psychodynamics_exposes_id_ego_and_internalized_pressure(tmp_path: Path):
    psyche = PsychodynamicState(tmp_path)
    payload = psyche.projection(
        neural_life={
            "intention": {"name": "EVOLVE", "strength": 0.82},
            "drives": {"survival": 0.35, "reproduction": 0.62, "evolution": 0.84},
        },
        personality={
            "traits": {"caution": 0.42, "persistence": 0.73},
        },
        active_goal_count=2,
    )
    assert payload["id"]["dominant_impulse"] == "EVOLVE"
    assert payload["ego"]["role"] == "REALITY_CHECK_AND_PLAN"
    assert payload["ego"]["desire_source"] is False
    assert payload["superego"]["source"].startswith("durable identity")
    assert 0.0 <= payload["conflict"] <= 1.0
