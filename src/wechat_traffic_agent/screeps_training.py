"""Screeps training ledger and bounded strategy researcher.

The game is an external world: only measured match outcomes can create reward.
Internal command/function success is never treated as learning evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from .world_learning import WorldLearningLedger
from .game_intelligence import GameIntelligence

SCHEMA = "ferro-screeps-training.v2"
EPISODE_SCHEMA = "ferro-screeps-episode.v2"
MAX_EPISODES = 512
MIN_SAMPLES_PER_ARM = 3

_DEFAULT_STRATEGIES: dict[str, dict[str, float | int]] = {
    "balanced-v1": {
        "worker_target": 4,
        "builder_target": 2,
        "upgrader_target": 2,
        "soldier_target": 1,
        "aggression": 0.35,
        "reserve_energy": 0.25,
    },
    "economy-v1": {
        "worker_target": 6,
        "builder_target": 2,
        "upgrader_target": 2,
        "soldier_target": 0,
        "aggression": 0.10,
        "reserve_energy": 0.35,
    },
    "fortress-v1": {
        "worker_target": 4,
        "builder_target": 3,
        "upgrader_target": 1,
        "soldier_target": 2,
        "aggression": 0.20,
        "reserve_energy": 0.45,
    },
    "raider-v1": {
        "worker_target": 4,
        "builder_target": 1,
        "upgrader_target": 1,
        "soldier_target": 4,
        "aggression": 0.80,
        "reserve_energy": 0.15,
    },
}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _aware(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class ScreepsEpisodeOutcome:
    episode_id: str
    strategy_id: str
    opponent_id: str
    reward: float
    causal_factors: dict[str, float]
    evidence_ref: str
    observed_at: str


class ScreepsResearcher:
    """Prospective strategy selection over real Screeps episodes.

    A challenger can be proposed from history, but it is promoted only after
    both baseline and challenger have enough *future* match outcomes.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "screeps_training.json"
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        else:
            self.state = {
                "schema_version": SCHEMA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "strategies": dict(_DEFAULT_STRATEGIES),
                "incumbent_strategy_id": "balanced-v1",
                "episodes": [],
                "open_trials": {},
                "prospective_trial": None,
                "generation": 0,
                "promotion_count": 0,
                "rejection_count": 0,
                "harness_failures": [],
            }
        self.game_intelligence = GameIntelligence(self.state_root)
        self.game_intelligence.register_population(
            "SCREEPS",
            {
                str(strategy_id): dict(params)
                for strategy_id, params in (self.state.get("strategies") or {}).items()
                if isinstance(params, Mapping)
            },
        )

    def strategy(self, strategy_id: str | None = None) -> dict[str, Any]:
        strategy_id = strategy_id or str(self.state.get("incumbent_strategy_id") or "balanced-v1")
        strategies = self.state.get("strategies") or {}
        row = strategies.get(strategy_id)
        if not isinstance(row, Mapping):
            raise KeyError(f"unknown Screeps strategy: {strategy_id}")
        return {"strategy_id": strategy_id, **dict(row)}

    def choose_strategy(self, *, seed: int = 0) -> dict[str, Any]:
        trial = self.state.get("prospective_trial")
        if isinstance(trial, Mapping) and trial.get("status") == "ACTIVE":
            dispatch_count = int(trial.get("dispatch_count") or 0)
            arm = "BASELINE" if dispatch_count % 2 == 0 else "CHALLENGER"
            strategy_id = str(
                trial.get("baseline_strategy_id")
                if arm == "BASELINE"
                else trial.get("challenger_strategy_id")
            )
            updated = dict(trial)
            updated["dispatch_count"] = dispatch_count + 1
            self.state["prospective_trial"] = updated
            self._persist()
            return {**self.strategy(strategy_id), "research_arm": arm, "trial_id": trial.get("trial_id")}

        summaries = self._summaries()
        strategies = sorted((self.state.get("strategies") or {}).keys())
        unseen = [sid for sid in strategies if int(summaries.get(sid, {}).get("count") or 0) < 2]
        if unseen:
            strategy_id = unseen[abs(int(seed)) % len(unseen)]
            return {**self.strategy(strategy_id), "research_arm": "EXPLORATION", "trial_id": None}

        incumbent = str(self.state.get("incumbent_strategy_id") or "balanced-v1")
        neural = _read(self.state_root / "application" / "neural_life.json")
        temperament = self.game_intelligence.temperament_from_neural_state(neural)
        strategies = {
            str(strategy_id): dict(params)
            for strategy_id, params in (self.state.get("strategies") or {}).items()
            if isinstance(params, Mapping)
        }
        selected = self.game_intelligence.choose_strategy_for_temperament(
            strategies,
            environment="SCREEPS",
            temperament=temperament,
            seed=seed,
        ) or incumbent
        self.game_intelligence.note_temperament(
            temperament,
            context=f"SCREEPS:{selected}",
        )
        return {
            **self.strategy(selected),
            "research_arm": "INCUMBENT" if selected == incumbent else "TEMPERAMENT",
            "trial_id": None,
            "temperament": temperament,
        }

    def begin_episode(
        self,
        *,
        strategy_id: str,
        opponent_id: str,
        seed: int,
        prediction: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.strategy(strategy_id)
        nonce = sha256(f"{strategy_id}|{opponent_id}|{seed}|{now.isoformat()}".encode()).hexdigest()[:20]
        episode_id = f"screeps-episode:{nonce}"
        evidence_ref = f"{episode_id}:start"
        prediction = prediction or (
            f"strategy {strategy_id} will survive and improve external Screeps world utility "
            f"against {opponent_id}"
        )
        world = WorldLearningLedger(self.state_root, namespace="screeps")
        action = world.commit(
            action_kind="SCREEPS_STRATEGY_EPISODE",
            prediction=prediction,
            expected_utility=0.0,
            confidence=0.45,
            evidence_refs=[evidence_ref],
            now=now,
            windows=(("episode", timedelta(seconds=0)),),
            metadata={
                "screeps_episode_id": episode_id,
                "strategy_id": strategy_id,
                "opponent_id": opponent_id,
                "seed": int(seed),
            },
        )
        open_trials = dict(self.state.get("open_trials") or {})
        open_trials[episode_id] = {
            "episode_id": episode_id,
            "started_at": now.isoformat(),
            "strategy_id": strategy_id,
            "opponent_id": opponent_id,
            "seed": int(seed),
            "world_action_id": action["action_id"],
            "start_evidence_ref": evidence_ref,
        }
        self.state["open_trials"] = open_trials
        self._persist(now)
        return dict(open_trials[episode_id])

    @staticmethod
    def score_metrics(metrics: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
        """Convert measured world deltas to bounded utility.

        Every factor must be derived from externally observed game state.
        """
        survived = 1.0 if bool(metrics.get("survived", True)) else -1.0
        match_outcome = _clip(float(metrics.get("match_outcome") or 0.0))
        rooms_start = max(0.0, float(metrics.get("owned_rooms_start") or 0.0))
        rooms_end = max(0.0, float(metrics.get("owned_rooms_end") or 0.0))
        room_progress = _clip((rooms_end - rooms_start) / max(1.0, rooms_start))

        energy_start = max(0.0, float(metrics.get("energy_start") or 0.0))
        energy_end = max(0.0, float(metrics.get("energy_end") or 0.0))
        energy_progress = _clip((energy_end - energy_start) / max(500.0, energy_start))

        controller_start = max(0.0, float(metrics.get("controller_progress_start") or 0.0))
        controller_end = max(0.0, float(metrics.get("controller_progress_end") or 0.0))
        controller_progress = _clip(
            (controller_end - controller_start) / max(1000.0, controller_start + 1.0)
        )

        hostile_losses = max(0.0, float(metrics.get("hostile_creeps_destroyed") or 0.0))
        own_losses = max(0.0, float(metrics.get("own_creeps_lost") or 0.0))
        exchange = _clip((hostile_losses - own_losses) / max(1.0, hostile_losses + own_losses))

        structures_start = max(0.0, float(metrics.get("structures_start") or 0.0))
        structures_end = max(0.0, float(metrics.get("structures_end") or 0.0))
        infrastructure = _clip(
            (structures_end - structures_start) / max(5.0, structures_start)
        )

        factors = {
            "match_outcome": match_outcome,
            "survival": survived,
            "territory": room_progress,
            "energy": energy_progress,
            "controller": controller_progress,
            "combat_exchange": exchange,
            "infrastructure": infrastructure,
        }
        reward = (
            0.45 * match_outcome
            + 0.20 * survived
            + 0.10 * room_progress
            + 0.08 * energy_progress
            + 0.05 * controller_progress
            + 0.08 * exchange
            + 0.04 * infrastructure
        )
        return round(_clip(reward), 6), {key: round(value, 6) for key, value in factors.items()}

    def settle_episode(
        self,
        result: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> ScreepsEpisodeOutcome:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if str(result.get("schema_version") or "") not in {"", EPISODE_SCHEMA}:
            raise ValueError("unsupported Screeps episode schema")
        episode_id = str(result.get("episode_id") or "")
        open_trials = dict(self.state.get("open_trials") or {})
        trial = dict(open_trials.get(episode_id) or {})
        if not trial:
            raise KeyError("unknown or already settled Screeps episode")

        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError("Screeps result requires measured metrics")
        reward, factors = self.score_metrics(metrics)
        evidence_ref = str(result.get("evidence_ref") or f"{episode_id}:outcome")
        world = WorldLearningLedger(self.state_root, namespace="screeps")
        settlement = world.settle(
            action_id=str(trial["world_action_id"]),
            window="episode",
            actual_utility=reward,
            evidence_refs=[evidence_ref],
            now=now,
            counterfactual=str(result.get("counterfactual") or "")[:1000] or None,
            causal_factors=factors,
        )

        outcome = ScreepsEpisodeOutcome(
            episode_id=episode_id,
            strategy_id=str(trial["strategy_id"]),
            opponent_id=str(trial["opponent_id"]),
            reward=reward,
            causal_factors=factors,
            evidence_ref=evidence_ref,
            observed_at=now.isoformat(),
        )
        episodes = [
            dict(row)
            for row in (self.state.get("episodes") or ())
            if isinstance(row, Mapping) and row.get("episode_id") != episode_id
        ]
        episodes.append(
            {
                "episode_id": episode_id,
                "strategy_id": outcome.strategy_id,
                "opponent_id": outcome.opponent_id,
                "seed": int(trial.get("seed") or 0),
                "observed_at": outcome.observed_at,
                "reward": reward,
                "causal_factors": factors,
                "evidence_ref": evidence_ref,
                "metrics": dict(metrics),
                "world_action_id": trial["world_action_id"],
                "learning_signal": settlement.get("learning_signal"),
            }
        )
        self.state["episodes"] = episodes[-MAX_EPISODES:]
        self.game_intelligence.record_episode(
            environment="SCREEPS",
            episode_id=episode_id,
            reward=reward,
            strategy_id=outcome.strategy_id,
            opponent_id=outcome.opponent_id,
            success=float(factors.get("match_outcome") or 0.0) > 0.0,
            metrics=dict(metrics),
            causal_factors=factors,
            evidence_ref=evidence_ref,
            observed_at=now,
            source="SCREEPS_MATCH",
        )
        opponent_strategy_id = outcome.opponent_id.removeprefix("screeps-opponent:")
        population_members = self.game_intelligence.population_summary("SCREEPS").get("members") or {}
        if outcome.strategy_id in population_members and opponent_strategy_id in population_members:
            self.game_intelligence.record_self_play_match(
                environment="SCREEPS",
                left=outcome.strategy_id,
                right=opponent_strategy_id,
                left_reward=reward,
                episode_id=episode_id,
                observed_at=now,
            )
        open_trials.pop(episode_id, None)
        self.state["open_trials"] = open_trials
        self._record_trial_result(outcome)
        self._maybe_start_challenger(now)
        self.game_intelligence.register_population(
            "SCREEPS",
            {
                str(strategy_id): dict(params)
                for strategy_id, params in (self.state.get("strategies") or {}).items()
                if isinstance(params, Mapping)
            },
        )
        self._persist(now)
        return outcome

    def abandon_episode(
        self,
        episode_id: str,
        *,
        reason: str,
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Close a harness-failed episode without rewarding or punishing policy."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        open_trials = dict(self.state.get("open_trials") or {})
        trial = dict(open_trials.get(str(episode_id)) or {})
        if not trial:
            return {"status": "NOT_OPEN", "episode_id": str(episode_id)}
        world = WorldLearningLedger(self.state_root, namespace="screeps")
        world.cancel(
            action_id=str(trial["world_action_id"]),
            reason=str(reason or "SCREEPS_HARNESS_FAILURE")[:500],
            evidence_refs=[str(evidence_ref)],
            now=now,
        )
        open_trials.pop(str(episode_id), None)
        self.state["open_trials"] = open_trials
        failures = [
            dict(row)
            for row in (self.state.get("harness_failures") or ())
            if isinstance(row, Mapping)
        ]
        failures.append({
            "episode_id": str(episode_id),
            "strategy_id": trial.get("strategy_id"),
            "opponent_id": trial.get("opponent_id"),
            "observed_at": now.isoformat(),
            "reason": str(reason or "SCREEPS_HARNESS_FAILURE")[:500],
            "evidence_ref": str(evidence_ref),
        })
        self.state["harness_failures"] = failures[-64:]
        self._persist(now)
        return {
            "status": "CANCELLED_WITHOUT_REWARD",
            "episode_id": str(episode_id),
            "evidence_ref": str(evidence_ref),
        }

    def _summaries(self) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.state.get("episodes") or ():
            if not isinstance(row, Mapping):
                continue
            grouped.setdefault(str(row.get("strategy_id") or ""), []).append(dict(row))
        output: dict[str, dict[str, Any]] = {}
        for strategy_id, rows in grouped.items():
            rewards = [float(row.get("reward") or 0.0) for row in rows]
            factors: dict[str, list[float]] = {}
            for row in rows:
                for key, value in (row.get("causal_factors") or {}).items():
                    if isinstance(value, (int, float)):
                        factors.setdefault(str(key), []).append(float(value))
            output[strategy_id] = {
                "count": len(rows),
                "mean_reward": round(sum(rewards) / len(rewards), 6) if rewards else None,
                "negative_count": sum(1 for value in rewards if value < 0.0),
                "mean_factors": {
                    key: round(sum(values) / len(values), 6)
                    for key, values in factors.items()
                    if values
                },
            }
        return output

    def _record_trial_result(self, outcome: ScreepsEpisodeOutcome) -> None:
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping) or trial.get("status") != "ACTIVE":
            return
        baseline_id = str(trial.get("baseline_strategy_id") or "")
        challenger_id = str(trial.get("challenger_strategy_id") or "")
        arm = (
            "BASELINE"
            if outcome.strategy_id == baseline_id
            else "CHALLENGER"
            if outcome.strategy_id == challenger_id
            else None
        )
        if arm is None:
            return
        updated = dict(trial)
        results = list(updated.get("results") or ())
        results.append(
            {
                "episode_id": outcome.episode_id,
                "arm": arm,
                "strategy_id": outcome.strategy_id,
                "reward": outcome.reward,
                "observed_at": outcome.observed_at,
                "evidence_ref": outcome.evidence_ref,
            }
        )
        updated["results"] = results[-64:]
        self.state["prospective_trial"] = updated
        self._resolve_trial()

    def _resolve_trial(self) -> None:
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping) or trial.get("status") != "ACTIVE":
            return
        rows = [dict(row) for row in trial.get("results") or () if isinstance(row, Mapping)]
        arms = {
            "BASELINE": [row for row in rows if row.get("arm") == "BASELINE"],
            "CHALLENGER": [row for row in rows if row.get("arm") == "CHALLENGER"],
        }
        if any(len(values) < MIN_SAMPLES_PER_ARM for values in arms.values()):
            return
        baseline_rewards = [float(row.get("reward") or 0.0) for row in arms["BASELINE"]]
        challenger_rewards = [float(row.get("reward") or 0.0) for row in arms["CHALLENGER"]]
        baseline_mean = sum(baseline_rewards) / len(baseline_rewards)
        challenger_mean = sum(challenger_rewards) / len(challenger_rewards)
        baseline_neg = sum(1 for value in baseline_rewards if value < 0.0)
        challenger_neg = sum(1 for value in challenger_rewards if value < 0.0)

        updated = dict(trial)
        updated["baseline_mean_reward"] = round(baseline_mean, 6)
        updated["challenger_mean_reward"] = round(challenger_mean, 6)
        updated["baseline_negative_count"] = baseline_neg
        updated["challenger_negative_count"] = challenger_neg
        updated["decided_at"] = datetime.now(timezone.utc).isoformat()
        if challenger_mean > baseline_mean + 0.02 and challenger_neg <= baseline_neg:
            challenger_id = str(updated["challenger_strategy_id"])
            self.state["incumbent_strategy_id"] = challenger_id
            self.state["generation"] = int(self.state.get("generation") or 0) + 1
            self.state["promotion_count"] = int(self.state.get("promotion_count") or 0) + 1
            updated["status"] = "PROMOTED"
            updated["decision"] = "PROMOTE_AFTER_PROSPECTIVE_WORLD_EVIDENCE"
        else:
            self.state["rejection_count"] = int(self.state.get("rejection_count") or 0) + 1
            updated["status"] = "REJECTED"
            updated["decision"] = "KEEP_INCUMBENT"
        self.state["prospective_trial"] = updated

    def _maybe_start_challenger(self, now: datetime) -> None:
        current_trial = self.state.get("prospective_trial")
        if isinstance(current_trial, Mapping) and current_trial.get("status") == "ACTIVE":
            return
        summaries = self._summaries()
        incumbent_id = str(self.state.get("incumbent_strategy_id") or "balanced-v1")
        incumbent_summary = summaries.get(incumbent_id)
        if not incumbent_summary or int(incumbent_summary.get("count") or 0) < 4:
            return
        generation = int(self.state.get("generation") or 0) + 1
        challenger_id = f"screeps-mutant-g{generation}"
        if challenger_id in (self.state.get("strategies") or {}):
            return

        base = dict(self.strategy(incumbent_id))
        factors = incumbent_summary.get("mean_factors") or {}
        # Bounded mutation from external failure/success factors. The mutation is
        # only a hypothesis; it earns no authority until prospective matches.
        if float(factors.get("combat_exchange") or 0.0) < -0.1:
            base["soldier_target"] = min(6, int(base["soldier_target"]) + 1)
            base["aggression"] = max(0.05, float(base["aggression"]) - 0.10)
        elif float(factors.get("energy") or 0.0) < 0.0:
            base["worker_target"] = min(8, int(base["worker_target"]) + 1)
            base["reserve_energy"] = min(0.60, float(base["reserve_energy"]) + 0.10)
        elif float(factors.get("controller") or 0.0) < 0.05:
            base["upgrader_target"] = min(5, int(base["upgrader_target"]) + 1)
        else:
            base["aggression"] = min(0.90, float(base["aggression"]) + 0.10)

        base.pop("strategy_id", None)
        strategies = dict(self.state.get("strategies") or {})
        strategies[challenger_id] = base
        self.state["strategies"] = strategies
        trial_id = "screeps-trial:" + sha256(
            f"{incumbent_id}|{challenger_id}|{now.isoformat()}".encode()
        ).hexdigest()[:20]
        self.state["prospective_trial"] = {
            "trial_id": trial_id,
            "status": "ACTIVE",
            "started_at": now.isoformat(),
            "baseline_strategy_id": incumbent_id,
            "challenger_strategy_id": challenger_id,
            "minimum_samples_per_arm": MIN_SAMPLES_PER_ARM,
            "dispatch_count": 0,
            "results": [],
            "origin": "WORLD_GROUNDED_BOUNDED_MUTATION",
        }

    def ingest_result_file(self, path: Path, *, now: datetime | None = None) -> dict[str, Any]:
        row = _read(Path(path))
        if not row:
            return {"status": "NO_RESULT", "path": str(path)}
        episode_id = str(row.get("episode_id") or "")
        if any(
            isinstance(item, Mapping) and item.get("episode_id") == episode_id
            for item in self.state.get("episodes") or ()
        ):
            return {"status": "ALREADY_INGESTED", "episode_id": episode_id}
        outcome = self.settle_episode(row, now=now)
        return {
            "status": "INGESTED",
            "episode_id": outcome.episode_id,
            "strategy_id": outcome.strategy_id,
            "reward": outcome.reward,
            "evidence_ref": outcome.evidence_ref,
        }

    def planned_episode_count(self, requested: int) -> dict[str, Any]:
        requested_count = max(1, min(64, int(requested)))
        graduation = self.game_intelligence.graduation_status()
        if graduation.get("graduated"):
            planned = requested_count
            mode = "MAINTENANCE"
        else:
            planned = max(2, requested_count)
            mode = "GRADUATION_SPRINT"
        return {
            "mode": mode,
            "requested_episodes": requested_count,
            "planned_episodes": planned,
            "graduation": graduation,
        }

    def choose_population_opponent(
        self,
        strategy_id: str,
        *,
        seed: int = 0,
        candidates: tuple[str, ...] | None = None,
    ) -> str:
        selected = self.game_intelligence.select_population_opponent(
            "SCREEPS",
            strategy_id=strategy_id,
            candidates=candidates,
            seed=seed,
        )
        if selected is None:
            fallback = [
                str(value)
                for value in (candidates or tuple((self.state.get("strategies") or {}).keys()))
                if str(value) != str(strategy_id)
            ]
            if not fallback:
                raise ValueError("Screeps self-play requires at least two strategies")
            selected = sorted(fallback)[abs(int(seed)) % len(fallback)]
        self.strategy(selected)
        return selected

    def plan_self_play(self, *, seed: int = 0) -> dict[str, Any]:
        pair = self.game_intelligence.select_self_play_pair("SCREEPS", seed=seed)
        if pair is None:
            return {"status": "INSUFFICIENT_POPULATION"}
        left, right = pair
        return {
            "status": "READY",
            "left": self.strategy(left),
            "right": self.strategy(right),
            "evidence_required": "MEASURED_MATCH_OUTCOME",
        }

    def evaluate_transfer_from(self, source_environment: str) -> dict[str, Any]:
        return self.game_intelligence.evaluate_transfer(
            source_environment=source_environment,
            target_environment="SCREEPS",
        )

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "incumbent_strategy_id": self.state.get("incumbent_strategy_id"),
            "generation": int(self.state.get("generation") or 0),
            "promotion_count": int(self.state.get("promotion_count") or 0),
            "rejection_count": int(self.state.get("rejection_count") or 0),
            "open_episode_count": len(self.state.get("open_trials") or {}),
            "episode_count": len(self.state.get("episodes") or ()),
            "harness_failure_count": len(self.state.get("harness_failures") or ()),
            "strategy_summaries": self._summaries(),
            "prospective_trial": self.state.get("prospective_trial"),
            "function_success_is_reward": False,
            "prospective_validation_required": True,
            "game_intelligence": self.game_intelligence.projection(),
            "self_play_population": self.game_intelligence.population_summary("SCREEPS"),
        }

    def _persist(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)


__all__ = [
    "EPISODE_SCHEMA",
    "MIN_SAMPLES_PER_ARM",
    "SCHEMA",
    "ScreepsEpisodeOutcome",
    "ScreepsResearcher",
]
