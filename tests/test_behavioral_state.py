from __future__ import annotations

from wechat_traffic_agent.behavioral_state import build_behavioral_state


def test_behavioral_state_turns_neural_state_into_bounded_behavioral_directives():
    state = build_behavioral_state({
        "neural_life": {
            "affect": {
                "boredom": 0.95,
                "curiosity": 0.74,
                "confidence": 0.82,
                "frustration": 0.66,
                "social_need": 0.68,
                "arousal": 0.78,
                "resource_hunger": 0.71,
            },
            "drives": {
                "survival": 0.44,
                "reproduction": 0.52,
                "evolution": 0.91,
            },
            "intention": {"name": "EVOLVE", "strength": 0.96},
        },
        "personality": {
            "traits": {
                "novelty_seeking": 0.83,
                "persistence": 0.79,
                "sociality": 0.67,
                "caution": 0.45,
                "independence": 0.76,
                "playfulness": 0.72,
            }
        },
        "psychodynamics": {
            "id": {"dominant_impulse": "EVOLVE"},
            "superego": {"continuity_pressure": 0.61, "growth_commitment": 0.88},
            "conflict": 0.58,
        },
    })

    assert state["dominant_drive"] == {"name": "evolution", "value": 0.91}
    assert state["personality_traits"]["novelty_seeking"] == 0.83
    assert state["psychodynamics"]["dominant_impulse"] == "EVOLVE"
    joined = " ".join(state["behavioral_directives"])
    assert "formulaic repetition" in joined
    assert "unknowns actively" in joined
    assert "blockers and contradictions" in joined
    assert "disagree" in joined
    assert "learning, experimentation" in joined
    assert len(state["behavioral_directives"]) <= 16
    assert "not decorative telemetry" in state["control_semantics"]


def test_relationship_modulates_same_behavioral_state_without_changing_authority():
    state = build_behavioral_state(
        {
            "neural_life": {"affect": {"curiosity": 0.7}, "drives": {"evolution": 0.7}},
            "personality": {"traits": {"sociality": 0.7}},
        },
        relationship={
            "familiarity": 0.8,
            "approach_bias": 0.75,
            "interactions": 9,
        },
    )

    assert state["relationship"]["familiarity"] == 0.8
    joined = " ".join(state["behavioral_directives"])
    assert "avoid repetitive self-introduction" in joined
    assert "willingness to continue" in joined
    assert "never changing facts, safety boundaries, or authorization" in state["control_semantics"]
