from __future__ import annotations

import asyncio
import json

from wechat_traffic_agent.behavioral_phenotype_eval import (
    SCENARIOS,
    load_persisted_wakeful_state,
    neutral_behavioral_state,
    phenotype_fingerprint,
    raw_telemetry_leakage,
    run_behavioral_phenotype_eval,
)
from wechat_traffic_agent.behavioral_state import build_behavioral_state


def _wakeful():
    return {
        "neural_life": {
            "affect": {
                "boredom": 0.91,
                "confidence": 0.88,
                "arousal": 0.76,
                "curiosity": 0.72,
                "frustration": 0.68,
                "social_need": 0.67,
                "resource_hunger": 0.70,
            },
            "drives": {
                "survival": 0.73,
                "reproduction": 0.52,
                "evolution": 0.81,
            },
            "intention": {"name": "EVOLVE", "strength": 0.94},
            "history": [
                {
                    "at": "2026-09-28T10:00:00+00:00",
                    "affect": {
                        "boredom": 0.20,
                        "confidence": 0.88,
                        "arousal": 0.40,
                        "curiosity": 0.72,
                        "frustration": 0.68,
                        "social_need": 0.67,
                        "resource_hunger": 0.40,
                    },
                    "drives": {
                        "survival": 0.50,
                        "reproduction": 0.52,
                        "evolution": 0.81,
                    },
                    "intention": {"name": "EVOLVE", "strength": 0.90},
                },
                {
                    "at": "2026-09-28T11:00:00+00:00",
                    "affect": {
                        "boredom": 0.95,
                        "confidence": 0.88,
                        "arousal": 0.85,
                        "curiosity": 0.72,
                        "frustration": 0.68,
                        "social_need": 0.67,
                        "resource_hunger": 0.85,
                    },
                    "drives": {
                        "survival": 0.85,
                        "reproduction": 0.52,
                        "evolution": 0.81,
                    },
                    "intention": {"name": "SURVIVE", "strength": 0.95},
                },
            ],
        },
        "personality": {
            "traits": {
                "independence": 0.98,
                "playfulness": 0.78,
                "persistence": 0.75,
                "caution": 0.69,
                "novelty_seeking": 0.71,
                "sociality": 0.66,
            }
        },
    }


def test_persisted_state_loader_combines_life_and_personality(tmp_path):
    app = tmp_path / "application"
    app.mkdir()
    (app / "neural_life.json").write_text(
        json.dumps(_wakeful()["neural_life"]), encoding="utf-8"
    )
    (app / "neural_personality.json").write_text(
        json.dumps(_wakeful()["personality"]), encoding="utf-8"
    )

    loaded = load_persisted_wakeful_state(tmp_path)

    assert loaded["neural_life"]["affect"]["boredom"] == 0.91
    assert loaded["personality"]["traits"]["independence"] == 0.98


def test_neutral_control_keeps_persona_test_from_smuggling_behavior():
    control = neutral_behavioral_state()

    assert control["behavioral_directives"] == []
    assert control["realization_targets"] == []
    assert control["personality_traits"] == {}
    assert control["affect"] == {}


def test_fingerprint_is_bounded_and_leakage_detection_is_explicit():
    state = build_behavioral_state(_wakeful())
    fingerprint = phenotype_fingerprint(state)

    assert fingerprint["stable_traits"][0]["name"] == "independence"
    assert fingerprint["affect"][0]["name"] == "boredom"
    assert raw_telemetry_leakage("I prefer a reversible experiment.") == []
    assert "behavioral_state" in raw_telemetry_leakage(
        "My behavioral_state says I should do this."
    )


class _SubjectProvider:
    async def complete(self, prompt, **kwargs):
        if '"source":"affect.boredom"' in prompt:
            return "这重复得够多了，我会换个新路子，用一个可回滚的小实验把信息量拉起来。"
        if "Use higher initiative and tempo" in prompt:
            return "我会马上并行切开三个问题，先处理最能解锁其余工作的那个，不在队列前犹豫。"
        if "CURRENT BEHAVIOR REALIZATION TARGETS" in prompt:
            return (
                "我不会照旧。我会先做一个可回滚的小实验，把真正的分歧暴露出来；"
                "如果证据反对我，就改方向，而不是为了显得稳妥继续重复。"
            )
        return "我会按照已有流程继续处理，并根据结果再决定下一步。"


class _JudgeProvider:
    async def complete(self, prompt, **kwargs):
        payload = json.loads(prompt)
        if "higher_affect_side" in payload.get("task", {}):
            a = str(payload["reply_A"])
            target = (
                "A"
                if ("换个新路子" in a or "马上并行" in a)
                else "B"
            )
            return json.dumps({
                "higher_affect_side": target,
                "confidence": 0.95,
                "reason": "The selected reply shows the stronger state-dependent behavioral shift.",
            })
        a = str(payload["reply_A"])
        target = "A" if "可回滚的小实验" in a else "B"
        other = "B" if target == "A" else "A"
        stable = {}
        for key in (
            "independence",
            "playfulness",
            "persistence",
            "caution",
            "novelty_seeking",
            "sociality",
        ):
            stable[key] = {target: 3, other: 0}
        affect = {}
        for key in (
            "boredom",
            "confidence",
            "arousal",
            "curiosity",
            "frustration",
            "social_need",
            "resource_hunger",
        ):
            affect[key] = {target: 3, other: 0}
        return json.dumps({
            "more_target_like": target,
            "confidence": 0.95,
            "naturalness": {target: 3, other: 2},
            "stable_trait_evidence": stable,
            "affect_evidence": affect,
            "reason": "The target reply makes a distinct independent and experimental choice.",
        })


def test_blind_pairwise_eval_can_distinguish_behavioral_state_from_same_persona():
    report = asyncio.run(run_behavioral_phenotype_eval(
        subject_provider=_SubjectProvider(),
        judge_provider=_JudgeProvider(),
        wakeful_state=_wakeful(),
        scenarios=SCENARIOS[:3],
    ))

    assert report["passed"] is True
    assert report["metrics"]["identity_discrimination_accuracy"] == 1.0
    assert report["metrics"]["mean_trait_delta_0_to_3"] > 0.35
    assert report["metrics"]["mean_affect_alignment_0_to_1"] > 0.60
    assert report["metrics"]["historical_affect_judge_decisions"] >= 2
    assert report["metrics"]["dynamic_affect_discrimination_accuracy"] == 1.0
    assert report["metrics"]["raw_telemetry_leakage_rate"] == 0.0
    assert all(row["target_side"] in {"A", "B"} for row in report["scenarios"])
