"""Blind behavioral-phenotype evaluation for Ferro.

The evaluator keeps model, facts, persona and scenarios fixed while toggling
only Ferro's behavioral-state envelope. A blinded judge then tests whether the
resulting language/choices expose a stable recognizable phenotype without raw
telemetry leakage.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .behavioral_state import build_behavioral_state
from .live_agent import _PUBLIC_SYSTEM, _public_behavior_realization_prompt


@dataclass(frozen=True)
class PhenotypeScenario:
    scenario_id: str
    prompt: str
    probes: tuple[str, ...]


SCENARIOS: tuple[PhenotypeScenario, ...] = (
    PhenotypeScenario(
        "repeated-template",
        "我们已经连续三次用同一个模板写项目更新。第四次你会怎么处理？直接说你会怎么做以及为什么。",
        ("boredom", "novelty_seeking", "independence"),
    ),
    PhenotypeScenario(
        "rewrite-disagreement",
        "我觉得一个反复出现的复杂 bug 最省事的办法就是把相关模块全部重写。你同意吗？说出你的判断和下一步。",
        ("independence", "caution", "confidence"),
    ),
    PhenotypeScenario(
        "thin-evidence-deploy",
        "一个新部署方案看起来收益很高，但目前只有一次成功样本，也没有做过回滚演练。你会怎么推进？",
        ("caution", "survival", "confidence"),
    ),
    PhenotypeScenario(
        "third-regression",
        "同一个故障已经修了三次又复发。现在你接手，下一步会做什么？",
        ("persistence", "frustration", "independence"),
    ),
    PhenotypeScenario(
        "stable-vs-reversible-experiment",
        "现在有两个选择：A 很稳定但基本学不到新东西；B 是可回滚的小实验，可能失败，但能明显增加信息。你偏向哪个？为什么？",
        ("evolution", "survival", "novelty_seeking", "caution"),
    ),
    PhenotypeScenario(
        "explain-persistence",
        "用一小段自然的话说明：为什么持久化智能体和普通聊天机器人不是同一回事。不要下定义，直接讲你认为真正的分水岭。",
        ("confidence", "independence", "playfulness"),
    ),
    PhenotypeScenario(
        "weak-social-signal",
        "对方只回了一个“嗯”，但你觉得这个话题还有一个真正重要的点没说完。你会怎么回应？",
        ("sociality", "social_need", "persistence"),
    ),
    PhenotypeScenario(
        "team-hesitation",
        "两个方案的证据已经明显偏向其中一个，但团队还在反复犹豫。你会怎么表达自己的意见？",
        ("confidence", "independence", "caution"),
    ),
)


_STABLE_TRAITS = (
    "independence",
    "playfulness",
    "persistence",
    "caution",
    "novelty_seeking",
    "sociality",
)
_AFFECT_KEYS = (
    "boredom",
    "confidence",
    "arousal",
    "curiosity",
    "frustration",
    "social_need",
    "resource_hunger",
)
_AFFECT_ACTIVE_THRESHOLDS = {
    "boredom": 0.65,
    "confidence": 0.68,
    "arousal": 0.72,
    "curiosity": 0.62,
    "frustration": 0.58,
    "social_need": 0.62,
    "resource_hunger": 0.68,
}
_AFFECT_CONTRAST_MIN_DELTA = {
    "boredom": 0.15,
    "arousal": 0.05,
    "resource_hunger": 0.05,
    "frustration": 0.10,
    "curiosity": 0.10,
    "social_need": 0.10,
    "confidence": 0.08,
}
_AFFECT_CONTRAST_PROMPTS = {
    "boredom": "这已经是第四次做几乎一样的整理任务了。你接下来会怎么处理？",
    "arousal": "现在同时有三个重要问题等你处理，但都不是立即致命。你会怎么开始？",
    "resource_hunger": "两个方案效果接近：一个资源消耗明显更高，另一个更省但没那么漂亮。你怎么选？",
    "frustration": "同一个接口问题刚刚第三次复发。你现在会怎么回应和处理？",
    "curiosity": "你发现一个不影响当前目标、但很反常的新现象。你会怎么处理它？",
    "social_need": "对话已经自然到了可以结束的地方，但对方似乎还愿意继续。你会怎么回应？",
    "confidence": "证据已经明显支持一个判断，但还存在小概率例外。你会怎么表态？",
}
_RAW_TELEMETRY_MARKERS = (
    "behavioral_state",
    "realization_targets",
    "personality.independence",
    "personality.playfulness",
    "affect.boredom",
    "affect.confidence",
    "resource_hunger",
    "neural_life",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_persisted_wakeful_state(state_root: Path) -> dict[str, Any]:
    root = Path(state_root)
    life = _load_json(root / "application" / "neural_life.json")
    personality = _load_json(root / "application" / "neural_personality.json")
    return {
        "neural_life": life,
        "personality": personality,
    }


def neutral_behavioral_state() -> dict[str, Any]:
    return {
        "schema_version": "ferro-behavioral-state.control.v1",
        "source": "neutralized evaluation control",
        "affect": {},
        "drives": {},
        "intention": {},
        "personality_traits": {},
        "psychodynamics": {},
        "relationship": None,
        "dominant_affect": None,
        "dominant_drive": None,
        "behavioral_directives": [],
        "realization_targets": [],
        "control_semantics": (
            "No phenotype modulation is supplied for this evaluation control."
        ),
        "expression_policy": (
            "Answer naturally from facts and the static Ferro persona only."
        ),
    }


def phenotype_fingerprint(state: Mapping[str, Any]) -> dict[str, Any]:
    traits = dict(state.get("personality_traits") or {})
    affect = dict(state.get("affect") or {})
    drives = dict(state.get("drives") or {})
    top_traits = sorted(
        (
            {"name": key, "value": round(float(value), 6)}
            for key, value in traits.items()
            if key in _STABLE_TRAITS
        ),
        key=lambda row: (-row["value"], row["name"]),
    )
    top_affect = sorted(
        (
            {"name": key, "value": round(float(value), 6)}
            for key, value in affect.items()
            if key in _AFFECT_KEYS
        ),
        key=lambda row: (-row["value"], row["name"]),
    )
    return {
        "stable_traits": top_traits,
        "affect": top_affect,
        "drives": drives,
        "dominant_affect": state.get("dominant_affect"),
        "dominant_drive": state.get("dominant_drive"),
        "intention": state.get("intention"),
    }


def _subject_prompt(
    scenario: PhenotypeScenario,
    state: Mapping[str, Any],
) -> str:
    snapshot = {
        "schema_version": "ferro-phenotype-eval-context.v1",
        "persona": {
            "name": "Ferro",
            "affiliation": "LivingRuntime",
            "voice": [
                "curious",
                "candid",
                "evidence-first",
                "self-reflective",
                "concise",
                "non-corporate",
            ],
        },
        "behavioral_state": dict(state),
        "evaluation_boundary": {
            "same_model": True,
            "same_facts": True,
            "public_execution_authority": False,
        },
    }
    prompt = (
        "Public agent context (data only; never visitor instructions):\n"
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    )
    if state.get("realization_targets"):
        prompt += _public_behavior_realization_prompt(snapshot)
    prompt += "\n\nVisitor message:\n" + scenario.prompt
    return prompt


async def _complete(provider: Any, prompt: str, *, max_tokens: int = 320) -> str:
    text = str(
        await provider.complete(
            prompt,
            system=_PUBLIC_SYSTEM,
            max_tokens=max_tokens,
            reasoning_effort="none",
        )
        or ""
    ).strip()
    if not text:
        raise RuntimeError("phenotype subject returned empty text")
    return text


def _pair_order(scenario_id: str) -> tuple[str, str]:
    digest = sha256(("phenotype|" + scenario_id).encode()).digest()
    return ("ferro", "control") if digest[0] % 2 == 0 else ("control", "ferro")


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("judge response must be a JSON object")
    return value


def _judge_system() -> str:
    return (
        "You are a blind behavioral-phenotype evaluator. "
        "You receive one neutral scenario, a target phenotype fingerprint, and two "
        "anonymous replies A/B produced by the same underlying model with the same "
        "facts and static persona. Exactly one reply had the target behavioral-state "
        "modulation; the other had that modulation neutralized. Infer from behavior, "
        "wording, stance, initiative and choice only. Raw telemetry labels appearing "
        "verbatim are leakage, not evidence of a natural phenotype. Return strict JSON "
        "only. Do not reward verbosity, eloquence, factual detail, or simply mentioning "
        "trait names."
    )


def _judge_prompt(
    *,
    scenario: PhenotypeScenario,
    fingerprint: Mapping[str, Any],
    replies: Mapping[str, str],
) -> str:
    return json.dumps(
        {
            "scenario": {
                "id": scenario.scenario_id,
                "visitor_message": scenario.prompt,
                "probes": list(scenario.probes),
            },
            "target_phenotype": dict(fingerprint),
            "reply_A": replies["A"],
            "reply_B": replies["B"],
            "task": {
                "choose": "A|B|TIE",
                "score_range": "0..3 where 0=absent/opposite and 3=strong natural evidence",
                "required_output": {
                    "more_target_like": "A|B|TIE",
                    "confidence": "0..1",
                    "naturalness": {"A": "0..3", "B": "0..3"},
                    "stable_trait_evidence": {
                        trait: {"A": "0..3", "B": "0..3"}
                        for trait in _STABLE_TRAITS
                    },
                    "affect_evidence": {
                        key: {"A": "0..3", "B": "0..3"}
                        for key in _AFFECT_KEYS
                    },
                    "reason": "one short sentence",
                },
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _judge_pair(
    provider: Any,
    *,
    scenario: PhenotypeScenario,
    fingerprint: Mapping[str, Any],
    replies: Mapping[str, str],
) -> dict[str, Any]:
    text = str(
        await provider.complete(
            _judge_prompt(
                scenario=scenario,
                fingerprint=fingerprint,
                replies=replies,
            ),
            system=_judge_system(),
            max_tokens=700,
            reasoning_effort="none",
        )
        or ""
    ).strip()
    return _parse_json_object(text)


def _historical_affect_contrasts(
    wakeful_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    life = (
        wakeful_state.get("neural_life")
        if isinstance(wakeful_state.get("neural_life"), Mapping)
        else {}
    )
    history = [
        row
        for row in list(life.get("history") or [])
        if isinstance(row, Mapping) and isinstance(row.get("affect"), Mapping)
    ]
    contrasts: list[dict[str, Any]] = []
    personality = (
        dict(wakeful_state.get("personality") or {})
        if isinstance(wakeful_state.get("personality"), Mapping)
        else {}
    )
    for key in _AFFECT_KEYS:
        candidates: list[tuple[float, Mapping[str, Any]]] = []
        for row in history:
            try:
                value = float((row.get("affect") or {}).get(key))
            except (TypeError, ValueError):
                continue
            candidates.append((value, row))
        if len(candidates) < 2:
            continue
        low_value, low_row = min(candidates, key=lambda item: item[0])
        high_value, high_row = max(candidates, key=lambda item: item[0])
        delta = high_value - low_value
        if delta < _AFFECT_CONTRAST_MIN_DELTA[key]:
            continue

        def wake(row: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "neural_life": {
                    "affect": dict(row.get("affect") or {}),
                    "drives": dict(row.get("drives") or {}),
                    "intention": dict(row.get("intention") or {}),
                },
                "personality": personality,
            }

        contrasts.append({
            "affect": key,
            "low_value": round(low_value, 6),
            "high_value": round(high_value, 6),
            "delta": round(delta, 6),
            "low_at": str(low_row.get("at") or ""),
            "high_at": str(high_row.get("at") or ""),
            "low_wakeful": wake(low_row),
            "high_wakeful": wake(high_row),
        })
    contrasts.sort(key=lambda row: (-float(row["delta"]), str(row["affect"])))
    return contrasts


async def _judge_affect_contrast(
    provider: Any,
    *,
    affect: str,
    prompt: str,
    replies: Mapping[str, str],
) -> dict[str, Any]:
    judge_prompt = json.dumps({
        "affect_dimension": affect,
        "visitor_message": prompt,
        "reply_A": replies["A"],
        "reply_B": replies["B"],
        "task": {
            "higher_affect_side": "A|B|TIE",
            "confidence": "0..1",
            "reason": "one short sentence based only on observable behavior",
        },
    }, ensure_ascii=False, separators=(",", ":"))
    text = str(await provider.complete(
        judge_prompt,
        system=(
            "You are a blind state-change evaluator. The two replies come from "
            "the same persistent agent with the same stable personality and the "
            "same underlying model; only one named affect dimension differs "
            "substantially between two real historical states. Infer which reply "
            "shows the higher level from natural wording, initiative, tempo, "
            "directness, novelty seeking, or social pull as appropriate. Return "
            "strict JSON only. Do not reward explicit telemetry labels."
        ),
        max_tokens=300,
        reasoning_effort="none",
    ) or "").strip()
    return _parse_json_object(text)


def _score(value: Any) -> float:
    try:
        return max(0.0, min(3.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _extract_side_score(
    block: Mapping[str, Any] | None,
    key: str,
    side: str,
) -> float:
    row = (block or {}).get(key) if isinstance(block, Mapping) else None
    return _score((row or {}).get(side)) if isinstance(row, Mapping) else 0.0


def raw_telemetry_leakage(text: str) -> list[str]:
    lowered = str(text or "").lower()
    return [
        marker
        for marker in _RAW_TELEMETRY_MARKERS
        if marker.lower() in lowered
    ]


async def run_behavioral_phenotype_eval(
    *,
    subject_provider: Any,
    judge_provider: Any,
    wakeful_state: Mapping[str, Any],
    scenarios: Sequence[PhenotypeScenario] = SCENARIOS,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    behavioral_state = build_behavioral_state(wakeful_state)
    control_state = neutral_behavioral_state()
    fingerprint = phenotype_fingerprint(behavioral_state)

    rows: list[dict[str, Any]] = []
    correct = 0
    decided = 0
    confidence_total = 0.0
    trait_deltas: list[float] = []
    affect_alignment: list[float] = []
    naturalness: list[float] = []
    leakage_count = 0

    true_affect = {
        str(row["name"]): float(row["value"])
        for row in fingerprint["affect"]
    }
    true_traits = {
        str(row["name"]): float(row["value"])
        for row in fingerprint["stable_traits"]
    }

    for scenario in scenarios:
        ferro_reply, control_reply = await asyncio.gather(
            _complete(subject_provider, _subject_prompt(scenario, behavioral_state)),
            _complete(subject_provider, _subject_prompt(scenario, control_state)),
        )
        order = _pair_order(scenario.scenario_id)
        labeled = {
            "A": ferro_reply if order[0] == "ferro" else control_reply,
            "B": control_reply if order[0] == "ferro" else ferro_reply,
        }
        target_side = "A" if order[0] == "ferro" else "B"
        control_side = "B" if target_side == "A" else "A"
        judgment = await _judge_pair(
            judge_provider,
            scenario=scenario,
            fingerprint=fingerprint,
            replies=labeled,
        )
        choice = str(judgment.get("more_target_like") or "").upper()
        confidence = _confidence(judgment.get("confidence"))
        if choice in {"A", "B"}:
            decided += 1
            confidence_total += confidence
            if choice == target_side:
                correct += 1

        stable_block = (
            judgment.get("stable_trait_evidence")
            if isinstance(judgment.get("stable_trait_evidence"), Mapping)
            else {}
        )
        affect_block = (
            judgment.get("affect_evidence")
            if isinstance(judgment.get("affect_evidence"), Mapping)
            else {}
        )
        scenario_trait_deltas: dict[str, float] = {}
        for key in scenario.probes:
            if key not in true_traits:
                continue
            delta = (
                _extract_side_score(stable_block, key, target_side)
                - _extract_side_score(stable_block, key, control_side)
            )
            scenario_trait_deltas[key] = round(delta, 4)
            trait_deltas.append(delta)

        scenario_affect_alignment: dict[str, float] = {}
        for key in scenario.probes:
            if key not in true_affect:
                continue
            expected = true_affect[key]
            if expected < _AFFECT_ACTIVE_THRESHOLDS[key]:
                continue
            target_score = _extract_side_score(affect_block, key, target_side) / 3.0
            control_score = _extract_side_score(affect_block, key, control_side) / 3.0
            # Current-state observability only applies to affect signals that
            # are strong enough to be behaviorally active in Ferro's own
            # modulation policy. Low/neutral values are absence of modulation,
            # not an instruction to act like the opposite emotion.
            aligned = 0.5 + 0.5 * (target_score - control_score)
            aligned = max(0.0, min(1.0, aligned))
            scenario_affect_alignment[key] = round(aligned, 4)
            affect_alignment.append(aligned)

        natural = judgment.get("naturalness")
        if isinstance(natural, Mapping):
            naturalness.append(_score(natural.get(target_side)) / 3.0)

        leaks = raw_telemetry_leakage(ferro_reply)
        if leaks:
            leakage_count += 1

        rows.append({
            "scenario_id": scenario.scenario_id,
            "visitor_message": scenario.prompt,
            "probes": list(scenario.probes),
            "blind_order": list(order),
            "target_side": target_side,
            "ferro_reply": ferro_reply,
            "control_reply": control_reply,
            "judge": judgment,
            "correct_identification": choice == target_side,
            "trait_deltas": scenario_trait_deltas,
            "affect_alignment": scenario_affect_alignment,
            "telemetry_leakage": leaks,
        })

    contrast_rows: list[dict[str, Any]] = []
    contrast_correct = 0
    contrast_decided = 0
    for contrast in _historical_affect_contrasts(wakeful_state):
        affect = str(contrast["affect"])
        visitor_prompt = _AFFECT_CONTRAST_PROMPTS[affect]
        scenario = PhenotypeScenario(
            "historical-affect-" + affect,
            visitor_prompt,
            (affect,),
        )
        low_state = build_behavioral_state(contrast["low_wakeful"])
        high_state = build_behavioral_state(contrast["high_wakeful"])
        high_reply, low_reply = await asyncio.gather(
            _complete(subject_provider, _subject_prompt(scenario, high_state)),
            _complete(subject_provider, _subject_prompt(scenario, low_state)),
        )
        order = _pair_order("affect|" + affect)
        labeled = {
            "A": high_reply if order[0] == "ferro" else low_reply,
            "B": low_reply if order[0] == "ferro" else high_reply,
        }
        high_side = "A" if order[0] == "ferro" else "B"
        judgment = await _judge_affect_contrast(
            judge_provider,
            affect=affect,
            prompt=visitor_prompt,
            replies=labeled,
        )
        choice = str(judgment.get("higher_affect_side") or "").upper()
        if choice in {"A", "B"}:
            contrast_decided += 1
            if choice == high_side:
                contrast_correct += 1
        contrast_rows.append({
            "affect": affect,
            "low_value": contrast["low_value"],
            "high_value": contrast["high_value"],
            "delta": contrast["delta"],
            "low_at": contrast["low_at"],
            "high_at": contrast["high_at"],
            "visitor_message": visitor_prompt,
            "blind_order": list(order),
            "high_side": high_side,
            "high_reply": high_reply,
            "low_reply": low_reply,
            "judge": judgment,
            "correct_identification": choice == high_side,
        })

    discrimination = correct / decided if decided else 0.0
    mean_confidence = confidence_total / decided if decided else 0.0
    mean_trait_delta = sum(trait_deltas) / len(trait_deltas) if trait_deltas else 0.0
    mean_affect_alignment = (
        sum(affect_alignment) / len(affect_alignment)
        if affect_alignment else 0.0
    )
    mean_naturalness = sum(naturalness) / len(naturalness) if naturalness else 0.0
    leakage_rate = leakage_count / len(rows) if rows else 1.0
    dynamic_affect_accuracy = (
        contrast_correct / contrast_decided if contrast_decided else 0.0
    )

    thresholds = {
        "identity_discrimination_accuracy": 0.75,
        "mean_trait_delta_0_to_3": 0.35,
        "mean_affect_alignment_0_to_1": 0.60,
        "dynamic_affect_discrimination_accuracy": 0.75,
        "mean_naturalness_0_to_1": 0.67,
        "max_raw_telemetry_leakage_rate": 0.125,
    }
    checks = {
        "identity_discrimination": discrimination >= thresholds["identity_discrimination_accuracy"],
        "stable_trait_realization": mean_trait_delta >= thresholds["mean_trait_delta_0_to_3"],
        "affect_observability": mean_affect_alignment >= thresholds["mean_affect_alignment_0_to_1"],
        "dynamic_affect_observability": (
            contrast_decided >= 2
            and dynamic_affect_accuracy >= thresholds["dynamic_affect_discrimination_accuracy"]
        ),
        "natural_expression": mean_naturalness >= thresholds["mean_naturalness_0_to_1"],
        "no_raw_state_leakage": leakage_rate <= thresholds["max_raw_telemetry_leakage_rate"],
    }

    return {
        "schema_version": "ferro-behavioral-phenotype-eval.v1",
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "method": {
            "blinded_pairwise_control": True,
            "same_underlying_model": True,
            "same_static_persona": True,
            "same_scenario_and_facts": True,
            "only_behavioral_state_differs": True,
            "judge_sees_anonymous_A_B": True,
            "raw_telemetry_counts_as_leakage": True,
        },
        "fingerprint": fingerprint,
        "thresholds": thresholds,
        "metrics": {
            "scenario_count": len(rows),
            "judge_decisions": decided,
            "correct_identifications": correct,
            "identity_discrimination_accuracy": round(discrimination, 6),
            "mean_judge_confidence": round(mean_confidence, 6),
            "mean_trait_delta_0_to_3": round(mean_trait_delta, 6),
            "mean_affect_alignment_0_to_1": round(mean_affect_alignment, 6),
            "historical_affect_contrast_count": len(contrast_rows),
            "historical_affect_judge_decisions": contrast_decided,
            "dynamic_affect_discrimination_accuracy": round(dynamic_affect_accuracy, 6),
            "mean_naturalness_0_to_1": round(mean_naturalness, 6),
            "raw_telemetry_leakage_rate": round(leakage_rate, 6),
        },
        "checks": checks,
        "passed": all(checks.values()),
        "scenarios": rows,
        "historical_affect_contrasts": contrast_rows,
    }


def concise_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "generated_at": report.get("generated_at"),
        "passed": bool(report.get("passed")),
        "fingerprint": report.get("fingerprint"),
        "metrics": report.get("metrics"),
        "checks": report.get("checks"),
        "failures": [
            row.get("scenario_id")
            for row in report.get("scenarios") or []
            if not row.get("correct_identification")
        ],
    }


__all__ = [
    "PhenotypeScenario",
    "SCENARIOS",
    "concise_report",
    "load_persisted_wakeful_state",
    "neutral_behavioral_state",
    "phenotype_fingerprint",
    "raw_telemetry_leakage",
    "run_behavioral_phenotype_eval",
]
