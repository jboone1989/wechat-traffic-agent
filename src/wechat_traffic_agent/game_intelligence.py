"""Cross-game learning substrate for Ferro.

This module deliberately sits above individual game implementations.  It turns
measured play into durable episodes, skills, a tiny empirical world model,
adaptive curriculum, self-play population evidence, dream challengers and
cross-game transfer measurements.  Offline replay may propose hypotheses, but
only observed world outcomes update empirical rewards.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "ferro-game-intelligence.v1"
MAX_EPISODES = 1024
MAX_TRANSITIONS = 8192
MAX_DREAMS = 128
MAX_POPULATION_MATCHES = 1024
MIN_CURRICULUM_EPISODES = 6


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


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


def _mean(total: float, count: int) -> float | None:
    return None if count <= 0 else round(float(total) / int(count), 6)


def _safe_key(value: object, limit: int = 256) -> str:
    return str(value or "")[:limit]


def _mutating(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._mutation():
            return method(self, *args, **kwargs)
    return wrapped


class GameIntelligence:
    """Persistent cross-game learning engine.

    The engine never turns function success or dream replay into reward.  Reward
    fields accepted by this class are expected to already represent measured
    game/world outcomes from the owning environment.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "game_intelligence.json"
        self.lock_path = self.state_root / "application" / "game_intelligence.lock"
        self._mutation_depth = 0
        self._lock_handle = None
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        else:
            self.state = {
                "schema_version": SCHEMA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "episodes": [],
                "transitions": [],
                "skills": {},
                "world_model": {},
                "curricula": {},
                "population": {},
                "dreams": [],
                "transfer_evaluations": [],
                "accelerated_batches": [],
                "temperament_history": [],
                "macro_executions": [],
            }
        self._ensure_state_defaults()

    def _ensure_state_defaults(self) -> None:
        for key, default in (
            ("episodes", []),
            ("transitions", []),
            ("skills", {}),
            ("world_model", {}),
            ("curricula", {}),
            ("population", {}),
            ("dreams", []),
            ("transfer_evaluations", []),
            ("accelerated_batches", []),
            ("temperament_history", []),
            ("macro_executions", []),
        ):
            self.state.setdefault(
                key,
                default.copy() if hasattr(default, "copy") else default,
            )

    @contextmanager
    def _mutation(self):
        outermost = self._mutation_depth == 0
        if outermost:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._lock_handle = handle
            latest = _read(self.path)
            if latest.get("schema_version") == SCHEMA:
                self.state = latest
            self._ensure_state_defaults()
        self._mutation_depth += 1
        try:
            yield
        finally:
            self._mutation_depth -= 1
            if outermost:
                handle = self._lock_handle
                self._lock_handle = None
                if handle is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()

    @staticmethod
    def _skill_family(skill_id: str, actions: Sequence[str] = ()) -> str:
        tokens = [str(x).upper() for x in actions if x]
        if any(x in {"UP", "DOWN", "LEFT", "RIGHT"} for x in tokens):
            return "NAVIGATE"
        if any(x in {"FORWARD", "ADVANCE"} for x in tokens):
            return "ADVANCE"
        upper = str(skill_id).upper()
        if any(token in upper for token in ("HARVEST", "ECONOMY", "WORKER")):
            return "RESOURCE_ACQUISITION"
        if any(token in upper for token in ("DEFEND", "FORTRESS", "RETREAT", "KITE")):
            return "DEFENSE"
        if any(token in upper for token in ("RAID", "RUSH", "ATTACK", "SOLDIER")):
            return "OFFENSE"
        if ":" in str(skill_id):
            return str(skill_id).split(":")[-1].upper()[:64]
        return upper[:64] or "UNKNOWN"

    def _skill_row(
        self,
        skill_id: str,
        *,
        kind: str = "PRIMITIVE",
        actions: Sequence[str] = (),
    ) -> dict[str, Any]:
        skills = self.state.setdefault("skills", {})
        row = dict(skills.get(skill_id) or {})
        if not row:
            row = {
                "skill_id": skill_id,
                "kind": kind,
                "actions": list(actions),
                "family": self._skill_family(skill_id, actions),
                "count": 0,
                "reward_sum": 0.0,
                "successes": 0,
                "environments": {},
                "last_observed_at": None,
            }
        skills[skill_id] = row
        return row

    def _observe_skill(
        self,
        *,
        skill_id: str,
        environment: str,
        reward: float,
        success: bool | None,
        observed_at: str,
        kind: str = "PRIMITIVE",
        actions: Sequence[str] = (),
    ) -> None:
        row = self._skill_row(skill_id, kind=kind, actions=actions)
        row["count"] = int(row.get("count") or 0) + 1
        row["reward_sum"] = round(float(row.get("reward_sum") or 0.0) + float(reward), 6)
        if success is True:
            row["successes"] = int(row.get("successes") or 0) + 1
        row["last_observed_at"] = observed_at
        envs = row.setdefault("environments", {})
        env = dict(envs.get(environment) or {"count": 0, "reward_sum": 0.0, "successes": 0})
        env["count"] = int(env.get("count") or 0) + 1
        env["reward_sum"] = round(float(env.get("reward_sum") or 0.0) + float(reward), 6)
        if success is True:
            env["successes"] = int(env.get("successes") or 0) + 1
        env["mean_reward"] = _mean(float(env["reward_sum"]), int(env["count"]))
        env["success_rate"] = round(int(env["successes"]) / int(env["count"]), 6)
        envs[environment] = env
        row["mean_reward"] = _mean(float(row["reward_sum"]), int(row["count"]))
        row["success_rate"] = round(int(row["successes"]) / int(row["count"]), 6)
        self.state["skills"][skill_id] = row

    @_mutating
    def record_transition(
        self,
        *,
        environment: str,
        episode_id: str,
        state_key: str,
        action: str,
        reward: float,
        next_state_key: str,
        skill_id: str | None = None,
        success: bool | None = None,
        observed_at: datetime | None = None,
        source: str = "GAME_WORLD",
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        env = str(environment).upper()
        action = _safe_key(action, 128)
        skill_id = _safe_key(skill_id or f"{env}:{action}", 192)
        transition = {
            "environment": env,
            "episode_id": _safe_key(episode_id, 192),
            "state_key": _safe_key(state_key),
            "action": action,
            "skill_id": skill_id,
            "reward": round(float(reward), 6),
            "next_state_key": _safe_key(next_state_key),
            "success": None if success is None else bool(success),
            "observed_at": now.isoformat(),
            "source": _safe_key(source, 64),
        }
        rows = list(self.state.get("transitions") or ())
        rows.append(transition)
        self.state["transitions"] = rows[-MAX_TRANSITIONS:]

        model_key = sha256(
            f"{env}|{transition['state_key']}|{skill_id}".encode("utf-8")
        ).hexdigest()[:24]
        model = dict((self.state.get("world_model") or {}).get(model_key) or {})
        if not model:
            model = {
                "environment": env,
                "state_key": transition["state_key"],
                "skill_id": skill_id,
                "count": 0,
                "reward_sum": 0.0,
                "successes": 0,
                "next_states": {},
            }
        model["count"] = int(model.get("count") or 0) + 1
        model["reward_sum"] = round(float(model.get("reward_sum") or 0.0) + float(reward), 6)
        if success is True:
            model["successes"] = int(model.get("successes") or 0) + 1
        next_states = dict(model.get("next_states") or {})
        next_states[transition["next_state_key"]] = int(next_states.get(transition["next_state_key"]) or 0) + 1
        model["next_states"] = next_states
        model["mean_reward"] = _mean(float(model["reward_sum"]), int(model["count"]))
        model["success_rate"] = round(int(model["successes"]) / int(model["count"]), 6)
        self.state.setdefault("world_model", {})[model_key] = model

        self._observe_skill(
            skill_id=skill_id,
            environment=env,
            reward=float(reward),
            success=success,
            observed_at=now.isoformat(),
            actions=(action,),
        )
        self._persist(now)
        return dict(transition)

    def predict(self, *, environment: str, state_key: str, skill_id: str) -> dict[str, Any]:
        env = str(environment).upper()
        state_key = _safe_key(state_key)
        skill_id = _safe_key(skill_id, 192)
        key = sha256(f"{env}|{state_key}|{skill_id}".encode("utf-8")).hexdigest()[:24]
        row = dict((self.state.get("world_model") or {}).get(key) or {})
        if row:
            next_states = row.get("next_states") or {}
            likely_next = (
                sorted(next_states.items(), key=lambda item: (-int(item[1]), str(item[0])))[0][0]
                if next_states
                else None
            )
            count = int(row.get("count") or 0)
            return {
                "status": "EMPIRICAL_STATE_SKILL_MODEL",
                "environment": env,
                "state_key": state_key,
                "skill_id": skill_id,
                "samples": count,
                "expected_reward": row.get("mean_reward"),
                "success_rate": row.get("success_rate"),
                "likely_next_state": likely_next,
                "confidence": round(min(0.95, count / 20.0), 6),
            }
        skill = dict((self.state.get("skills") or {}).get(skill_id) or {})
        env_row = dict((skill.get("environments") or {}).get(env) or {})
        if env_row:
            count = int(env_row.get("count") or 0)
            return {
                "status": "EMPIRICAL_SKILL_BACKOFF",
                "environment": env,
                "state_key": state_key,
                "skill_id": skill_id,
                "samples": count,
                "expected_reward": env_row.get("mean_reward"),
                "success_rate": env_row.get("success_rate"),
                "likely_next_state": None,
                "confidence": round(min(0.70, count / 30.0), 6),
            }
        return {
            "status": "UNKNOWN",
            "environment": env,
            "state_key": state_key,
            "skill_id": skill_id,
            "samples": 0,
            "expected_reward": None,
            "success_rate": None,
            "likely_next_state": None,
            "confidence": 0.0,
        }

    def _curriculum_row(self, environment: str) -> dict[str, Any]:
        env = str(environment).upper()
        curricula = self.state.setdefault("curricula", {})
        row = dict(curricula.get(env) or {})
        if not row:
            row = {
                "environment": env,
                "level": 1,
                "max_level": 10,
                "levels": {},
                "advance_count": 0,
                "regression_count": 0,
            }
        curricula[env] = row
        return row

    def curriculum(self, environment: str) -> dict[str, Any]:
        return dict(self._curriculum_row(environment))

    def _update_curriculum(self, *, environment: str, success: bool, reward: float) -> dict[str, Any]:
        row = self._curriculum_row(environment)
        level = int(row.get("level") or 1)
        levels = row.setdefault("levels", {})
        stats = dict(levels.get(str(level)) or {"episodes": 0, "successes": 0, "reward_sum": 0.0})
        stats["episodes"] = int(stats.get("episodes") or 0) + 1
        if success:
            stats["successes"] = int(stats.get("successes") or 0) + 1
        stats["reward_sum"] = round(float(stats.get("reward_sum") or 0.0) + float(reward), 6)
        stats["success_rate"] = round(int(stats["successes"]) / int(stats["episodes"]), 6)
        stats["mean_reward"] = _mean(float(stats["reward_sum"]), int(stats["episodes"]))
        levels[str(level)] = stats
        if int(stats["episodes"]) >= MIN_CURRICULUM_EPISODES:
            rate = float(stats["success_rate"])
            if rate >= 0.70 and level < int(row.get("max_level") or 10):
                row["level"] = level + 1
                row["advance_count"] = int(row.get("advance_count") or 0) + 1
                row["last_decision"] = "ADVANCE"
            elif rate <= 0.25 and level > 1:
                row["level"] = level - 1
                row["regression_count"] = int(row.get("regression_count") or 0) + 1
                row["last_decision"] = "REGRESS"
            else:
                row["last_decision"] = "HOLD"
        self.state.setdefault("curricula", {})[str(environment).upper()] = row
        return dict(row)

    def training_plan(self, environment: str, *, base_steps: int = 32) -> dict[str, Any]:
        curriculum = self._curriculum_row(environment)
        level = int(curriculum.get("level") or 1)
        return {
            "environment": str(environment).upper(),
            "curriculum_level": level,
            "steps": max(8, min(512, int(base_steps) + (level - 1) * 8)),
            "exploration_target": round(max(0.04, 0.16 - level * 0.01), 4),
            "difficulty_scale": round(0.75 + 0.125 * level, 4),
        }

    @_mutating
    def record_episode(
        self,
        *,
        environment: str,
        episode_id: str,
        reward: float,
        strategy_id: str | None = None,
        opponent_id: str | None = None,
        success: bool | None = None,
        metrics: Mapping[str, Any] | None = None,
        causal_factors: Mapping[str, Any] | None = None,
        evidence_ref: str | None = None,
        observed_at: datetime | None = None,
        source: str = "GAME_WORLD",
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        env = str(environment).upper()
        if success is None:
            success = float(reward) > 0.0
        skill_id = _safe_key(strategy_id or f"{env}:EPISODE_POLICY", 192)
        row = {
            "environment": env,
            "episode_id": _safe_key(episode_id, 192),
            "strategy_id": strategy_id,
            "opponent_id": opponent_id,
            "reward": round(float(reward), 6),
            "success": bool(success),
            "metrics": dict(metrics or {}),
            "causal_factors": dict(causal_factors or {}),
            "evidence_ref": evidence_ref,
            "observed_at": now.isoformat(),
            "source": _safe_key(source, 64),
        }
        episodes = [
            dict(item) for item in (self.state.get("episodes") or ())
            if isinstance(item, Mapping) and item.get("episode_id") != row["episode_id"]
        ]
        episodes.append(row)
        self.state["episodes"] = episodes[-MAX_EPISODES:]
        self._observe_skill(
            skill_id=skill_id,
            environment=env,
            reward=float(reward),
            success=bool(success),
            observed_at=now.isoformat(),
            kind="STRATEGY" if strategy_id else "EPISODE_POLICY",
            actions=(),
        )
        curriculum = self._update_curriculum(
            environment=env,
            success=bool(success),
            reward=float(reward),
        )
        dream = self.dream_replay(environment=env, persist=False, now=now)
        self._persist(now)
        return {"episode": dict(row), "curriculum": curriculum, "dream": dream}

    @staticmethod
    def _dominant_failure(episode: Mapping[str, Any]) -> str:
        factors = episode.get("causal_factors") if isinstance(episode.get("causal_factors"), Mapping) else {}
        numeric = [
            (str(key), float(value))
            for key, value in factors.items()
            if isinstance(value, (int, float))
        ]
        if numeric:
            return sorted(numeric, key=lambda item: (item[1], item[0]))[0][0]
        metrics = episode.get("metrics") if isinstance(episode.get("metrics"), Mapping) else {}
        if metrics.get("survived") is False:
            return "survival"
        return "reward"

    @staticmethod
    def _counterfactual_skill(failure: str) -> str:
        key = str(failure).lower()
        if "survival" in key or "structure" in key:
            return "DEFEND_BASE"
        if "combat" in key:
            return "KITE_AND_REINFORCE"
        if "energy" in key or "resource" in key:
            return "HARVEST_AND_RETURN"
        if "territory" in key or "room" in key:
            return "RETAKE_TERRITORY"
        if "controller" in key:
            return "ECONOMY_EXPAND"
        return "TRY_ALTERNATE_SKILL"

    @_mutating
    def dream_replay(
        self,
        *,
        environment: str | None = None,
        replay_count: int = 6,
        persist: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = [
            dict(row) for row in (self.state.get("episodes") or ())
            if isinstance(row, Mapping)
            and (environment is None or str(row.get("environment")) == str(environment).upper())
        ]
        if not rows:
            return {"status": "NO_EPISODES", "challengers": []}
        rows.sort(key=lambda row: (float(row.get("reward") or 0.0), str(row.get("observed_at") or "")))
        selected = rows[: max(1, min(int(replay_count), 16))]
        challengers = []
        for row in selected:
            failure = self._dominant_failure(row)
            challengers.append({
                "source_episode_id": row.get("episode_id"),
                "environment": row.get("environment"),
                "observed_reward": row.get("reward"),
                "failure_factor": failure,
                "proposed_skill": self._counterfactual_skill(failure),
                "authority": "OFFLINE_HYPOTHESIS_ONLY",
                "requires_prospective_world_validation": True,
            })
        dream_id = "game-dream:" + sha256(
            ("|".join(str(row.get("episode_id")) for row in selected) + now.isoformat()).encode("utf-8")
        ).hexdigest()[:20]
        dream = {
            "dream_id": dream_id,
            "created_at": now.isoformat(),
            "environment": None if environment is None else str(environment).upper(),
            "replay_count": len(selected),
            "challengers": challengers,
            "reward_authority": "NONE_OFFLINE_REPLAY_DOES_NOT_REWARD",
        }
        dreams = list(self.state.get("dreams") or ())
        dreams.append(dream)
        self.state["dreams"] = dreams[-MAX_DREAMS:]
        if persist:
            self._persist(now)
        return dict(dream)

    @_mutating
    def discover_macro_skills(
        self,
        environment: str,
        *,
        min_support: int = 3,
        max_length: int = 3,
    ) -> list[dict[str, Any]]:
        env = str(environment).upper()
        by_episode: dict[str, list[dict[str, Any]]] = {}
        for row in self.state.get("transitions") or ():
            if not isinstance(row, Mapping) or row.get("environment") != env:
                continue
            by_episode.setdefault(str(row.get("episode_id") or ""), []).append(dict(row))
        counts: dict[tuple[str, ...], list[float]] = {}
        for rows in by_episode.values():
            actions = [str(row.get("action") or "") for row in rows]
            rewards = [float(row.get("reward") or 0.0) for row in rows]
            for length in range(2, max(2, int(max_length)) + 1):
                for index in range(0, max(0, len(actions) - length + 1)):
                    seq = tuple(actions[index:index + length])
                    value = sum(rewards[index:index + length])
                    counts.setdefault(seq, []).append(value)
        discovered = []
        for actions, rewards in sorted(counts.items()):
            if len(rewards) < max(2, int(min_support)):
                continue
            mean_reward = sum(rewards) / len(rewards)
            if mean_reward <= 0.0:
                continue
            skill_id = f"macro:{env.lower()}:{sha256('|'.join(actions).encode()).hexdigest()[:12]}"
            row = self._skill_row(skill_id, kind="MACRO", actions=actions)
            row.update({
                "kind": "MACRO",
                "actions": list(actions),
                "family": self._skill_family(skill_id, actions),
                "support": len(rewards),
                "offline_sequence_mean_reward": round(mean_reward, 6),
                "requires_prospective_world_validation": True,
            })
            self.state["skills"][skill_id] = row
            discovered.append(dict(row))
        self._persist()
        return discovered

    def choose_macro_skill(
        self,
        environment: str,
        *,
        valid_actions: Sequence[str],
        seed: int = 0,
        min_support: int = 3,
    ) -> dict[str, Any] | None:
        env = str(environment).upper()
        valid = {str(value) for value in valid_actions}
        candidates: list[tuple[float, int, int, str, dict[str, Any]]] = []
        for skill_id, raw in (self.state.get("skills") or {}).items():
            if not isinstance(raw, Mapping) or raw.get("kind") != "MACRO":
                continue
            row = dict(raw)
            actions = [str(value) for value in row.get("actions") or ()]
            if not actions or not all(action in valid for action in actions):
                continue
            if not str(skill_id).startswith(f"macro:{env.lower()}:"):
                continue
            support = int(row.get("support") or 0)
            if support < max(2, int(min_support)):
                continue
            offline = float(row.get("offline_sequence_mean_reward") or 0.0)
            if offline <= 0.0:
                continue
            empirical = (
                ((row.get("environments") or {}).get(env) or {}).get("mean_reward")
            )
            empirical_score = (
                offline if empirical is None else float(empirical)
            )
            digest = int.from_bytes(
                sha256(f"{seed}:{skill_id}".encode()).digest()[:4], "little"
            )
            candidates.append(
                (empirical_score, support, -digest, str(skill_id), row)
            )
        if not candidates:
            return None
        _, _, _, skill_id, row = sorted(
            candidates,
            key=lambda value: (-value[0], -value[1], value[2], value[3]),
        )[0]
        return {"skill_id": skill_id, **row}

    @_mutating
    def record_macro_execution(
        self,
        *,
        environment: str,
        skill_id: str,
        execution_id: str,
        actions: Sequence[str],
        reward: float,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        env = str(environment).upper()
        action_list = [str(value) for value in actions]
        bounded_reward = _clip(float(reward))
        self._observe_skill(
            skill_id=skill_id,
            environment=env,
            reward=bounded_reward,
            success=bounded_reward > 0.0,
            observed_at=now.isoformat(),
            kind="MACRO",
            actions=tuple(action_list),
        )
        executions = list(self.state.get("macro_executions") or ())
        executions.append({
            "execution_id": _safe_key(execution_id, 192),
            "environment": env,
            "skill_id": _safe_key(skill_id, 192),
            "actions": action_list,
            "reward": round(float(reward), 6),
            "bounded_reward": bounded_reward,
            "success": bounded_reward > 0.0,
            "observed_at": now.isoformat(),
            "source": "MACRO_SKILL_PROSPECTIVE_EXECUTION",
        })
        self.state["macro_executions"] = executions[-MAX_EPISODES:]

        skill = dict((self.state.get("skills") or {}).get(skill_id) or {})
        evidence = dict((skill.get("environments") or {}).get(env) or {})
        runs = int(evidence.get("count") or 0)
        mean_reward = evidence.get("mean_reward")
        validated = bool(
            runs >= 3
            and mean_reward is not None
            and float(mean_reward) > 0.0
        )
        skill["prospective_runs"] = runs
        skill["prospectively_validated"] = validated
        skill["requires_prospective_world_validation"] = not validated
        self.state.setdefault("skills", {})[skill_id] = skill
        self._persist(now)
        return {
            "execution_id": execution_id,
            "environment": env,
            "skill_id": skill_id,
            "reward": round(float(reward), 6),
            "prospective_runs": runs,
            "prospectively_validated": validated,
        }

    @staticmethod
    def temperament_from_neural_state(neural: Mapping[str, Any] | None) -> dict[str, float]:
        neural = neural if isinstance(neural, Mapping) else {}
        drives = neural.get("drives") if isinstance(neural.get("drives"), Mapping) else {}
        affect = neural.get("affect") if isinstance(neural.get("affect"), Mapping) else {}
        body = neural.get("body") if isinstance(neural.get("body"), Mapping) else {}
        exploration = _clip(
            0.35 * float(drives.get("evolution") or drives.get("curiosity") or 0.0)
            + 0.25 * float(affect.get("frustration") or 0.0)
            + 0.20 * (1.0 - float(affect.get("confidence") or 0.5)),
            0.0,
            1.0,
        )
        aggression = _clip(
            0.45 * float(drives.get("competition") or drives.get("agency") or 0.0)
            + 0.30 * float(affect.get("confidence") or 0.0)
            - 0.25 * float(body.get("resource_pressure") or body.get("cpu_pressure") or 0.0),
            0.0,
            1.0,
        )
        resource_pressure = _clip(
            max(
                float(body.get("resource_pressure") or 0.0),
                float(body.get("cpu_pressure") or 0.0),
                float(body.get("memory_pressure") or 0.0),
                float(body.get("disk_pressure") or 0.0),
            ),
            0.0,
            1.0,
        )
        caution = _clip(
            0.45 * resource_pressure
            + 0.35 * float(affect.get("frustration") or 0.0)
            + 0.20 * (1.0 - float(affect.get("confidence") or 0.5)),
            0.0,
            1.0,
        )
        return {
            "exploration": round(exploration, 6),
            "aggression": round(aggression, 6),
            "resource_pressure": round(resource_pressure, 6),
            "caution": round(caution, 6),
        }

    @_mutating
    def note_temperament(
        self,
        temperament: Mapping[str, Any],
        *,
        context: str,
        now: datetime | None = None,
    ) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = list(self.state.get("temperament_history") or ())
        rows.append({
            "observed_at": now.isoformat(),
            "context": _safe_key(context, 128),
            "temperament": {
                key: round(float(value), 6)
                for key, value in temperament.items()
                if isinstance(value, (int, float))
            },
        })
        self.state["temperament_history"] = rows[-128:]
        self._persist(now)

    def choose_strategy_for_temperament(
        self,
        strategies: Mapping[str, Mapping[str, Any]],
        *,
        environment: str,
        temperament: Mapping[str, float],
        seed: int = 0,
    ) -> str | None:
        if not strategies:
            return None
        env = str(environment).upper()
        scores: list[tuple[float, str]] = []
        for strategy_id, params in strategies.items():
            skill = (self.state.get("skills") or {}).get(str(strategy_id)) or {}
            empirical = ((skill.get("environments") or {}).get(env) or {}).get("mean_reward")
            empirical_score = 0.5 if empirical is None else (float(empirical) + 1.0) / 2.0
            aggression = _clip(float(params.get("aggression") or 0.0), 0.0, 1.0)
            reserve = _clip(float(params.get("reserve_energy") or 0.0), 0.0, 1.0)
            soldiers = _clip(float(params.get("soldier_target") or 0.0) / 6.0, 0.0, 1.0)
            workers = _clip(float(params.get("worker_target") or 0.0) / 8.0, 0.0, 1.0)
            fit = (
                0.45 * empirical_score
                + 0.20 * (1.0 - abs(aggression - float(temperament.get("aggression") or 0.0)))
                + 0.15 * (1.0 - abs(reserve - float(temperament.get("caution") or 0.0)))
                + 0.10 * (1.0 - abs(soldiers - float(temperament.get("aggression") or 0.0)))
                + 0.10 * (1.0 - abs(workers - float(temperament.get("resource_pressure") or 0.0)))
            )
            jitter = int.from_bytes(
                sha256(f"{seed}:{strategy_id}".encode()).digest()[:2], "little"
            ) / 65535.0 * 1e-6
            scores.append((fit + jitter, str(strategy_id)))
        return sorted(scores, key=lambda item: (-item[0], item[1]))[0][1]

    @_mutating
    def register_population(
        self,
        environment: str,
        strategies: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        env = str(environment).upper()
        population = self.state.setdefault("population", {}).setdefault(
            env, {"members": {}, "matches": []}
        )
        members = population.setdefault("members", {})
        changed = False
        for strategy_id, params in strategies.items():
            key = str(strategy_id)
            previous = dict(members.get(key) or {})
            row = dict(previous)
            row.setdefault("strategy_id", key)
            row["params"] = dict(params)
            row.setdefault("games", 0)
            row.setdefault("reward_sum", 0.0)
            row.setdefault("wins", 0)
            row.setdefault("losses", 0)
            if row != previous:
                changed = True
            members[key] = row
        if changed:
            self._persist()
        return self.population_summary(env)

    def select_self_play_pair(self, environment: str, *, seed: int = 0) -> tuple[str, str] | None:
        env = str(environment).upper()
        members = sorted(
            ((self.state.get("population") or {}).get(env) or {}).get("members", {}).keys()
        )
        if len(members) < 2:
            return None
        match_counts: dict[tuple[str, str], int] = {}
        for row in ((self.state.get("population") or {}).get(env) or {}).get("matches", []):
            if not isinstance(row, Mapping):
                continue
            pair = tuple(sorted((str(row.get("left") or ""), str(row.get("right") or ""))))
            match_counts[pair] = match_counts.get(pair, 0) + 1
        candidates = []
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                pair = (left, right)
                digest = int.from_bytes(
                    sha256(f"{seed}:{left}:{right}".encode()).digest()[:4], "little"
                )
                candidates.append((match_counts.get(pair, 0), digest, left, right))
        _, _, left, right = sorted(candidates)[0]
        return left, right

    def select_population_opponent(
        self,
        environment: str,
        *,
        strategy_id: str,
        candidates: Sequence[str] | None = None,
        seed: int = 0,
    ) -> str | None:
        env = str(environment).upper()
        population = ((self.state.get("population") or {}).get(env) or {})
        members = {
            str(value)
            for value in (population.get("members") or {}).keys()
            if str(value) != str(strategy_id)
        }
        if candidates is not None:
            allowed = {str(value) for value in candidates}
            members &= allowed
        if not members:
            return None
        pair_counts: dict[str, int] = {member: 0 for member in members}
        for row in population.get("matches") or ():
            if not isinstance(row, Mapping):
                continue
            left = str(row.get("left") or "")
            right = str(row.get("right") or "")
            if left == str(strategy_id) and right in pair_counts:
                pair_counts[right] += 1
            elif right == str(strategy_id) and left in pair_counts:
                pair_counts[left] += 1
        ranked = []
        for member in members:
            digest = int.from_bytes(
                sha256(f"opponent:{seed}:{strategy_id}:{member}".encode()).digest()[:4],
                "little",
            )
            ranked.append((pair_counts.get(member, 0), digest, member))
        return sorted(ranked)[0][2]

    @_mutating
    def record_self_play_match(
        self,
        *,
        environment: str,
        left: str,
        right: str,
        left_reward: float,
        episode_id: str,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        env = str(environment).upper()
        population = self.state.setdefault("population", {}).setdefault(
            env, {"members": {}, "matches": []}
        )
        members = population.setdefault("members", {})
        if left not in members or right not in members:
            raise KeyError("self-play members must be registered before a match")
        reward = _clip(float(left_reward))
        for member_id, signed in ((left, reward), (right, -reward)):
            row = dict(members[member_id])
            row["games"] = int(row.get("games") or 0) + 1
            row["reward_sum"] = round(float(row.get("reward_sum") or 0.0) + signed, 6)
            row["mean_reward"] = _mean(float(row["reward_sum"]), int(row["games"]))
            if signed > 0:
                row["wins"] = int(row.get("wins") or 0) + 1
            elif signed < 0:
                row["losses"] = int(row.get("losses") or 0) + 1
            members[member_id] = row
        matches = list(population.get("matches") or ())
        matches.append({
            "episode_id": _safe_key(episode_id, 192),
            "left": left,
            "right": right,
            "left_reward": reward,
            "observed_at": now.isoformat(),
        })
        population["matches"] = matches[-MAX_POPULATION_MATCHES:]
        self._persist(now)
        return self.population_summary(env)

    def population_summary(self, environment: str) -> dict[str, Any]:
        env = str(environment).upper()
        population = ((self.state.get("population") or {}).get(env) or {})
        members = {
            str(key): {
                "games": int(value.get("games") or 0),
                "mean_reward": value.get("mean_reward"),
                "wins": int(value.get("wins") or 0),
                "losses": int(value.get("losses") or 0),
                "params": dict(value.get("params") or {}),
            }
            for key, value in (population.get("members") or {}).items()
            if isinstance(value, Mapping)
        }
        return {
            "environment": env,
            "member_count": len(members),
            "match_count": len(population.get("matches") or ()),
            "members": members,
        }

    @_mutating
    def evaluate_transfer(
        self,
        *,
        source_environment: str,
        target_environment: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        source = str(source_environment).upper()
        target = str(target_environment).upper()
        paired: list[tuple[float, float, str]] = []
        target_all: list[float] = []
        by_family: dict[str, dict[str, list[float]]] = {}
        for skill in (self.state.get("skills") or {}).values():
            if not isinstance(skill, Mapping):
                continue
            family = str(skill.get("family") or "UNKNOWN")
            for env, stats in (skill.get("environments") or {}).items():
                if not isinstance(stats, Mapping) or stats.get("mean_reward") is None:
                    continue
                by_family.setdefault(family, {}).setdefault(str(env), []).append(
                    float(stats["mean_reward"])
                )
                if str(env) == target:
                    target_all.append(float(stats["mean_reward"]))
        for family, envs in by_family.items():
            if source not in envs or target not in envs:
                continue
            paired.append((
                sum(envs[source]) / len(envs[source]),
                sum(envs[target]) / len(envs[target]),
                family,
            ))
        target_baseline = sum(target_all) / len(target_all) if target_all else 0.0
        target_paired = sum(row[1] for row in paired) / len(paired) if paired else 0.0
        source_paired = sum(row[0] for row in paired) / len(paired) if paired else 0.0
        result = {
            "evaluated_at": now.isoformat(),
            "source_environment": source,
            "target_environment": target,
            "shared_skill_families": [row[2] for row in paired],
            "shared_family_count": len(paired),
            "source_shared_mean_reward": round(source_paired, 6) if paired else None,
            "target_shared_mean_reward": round(target_paired, 6) if paired else None,
            "target_baseline_mean_reward": round(target_baseline, 6) if target_all else None,
            "transfer_advantage": (
                round(target_paired - target_baseline, 6) if paired and target_all else None
            ),
            "interpretation": (
                "MEASURED_CROSS_GAME_REUSE"
                if paired
                else "INSUFFICIENT_SHARED_SKILL_EVIDENCE"
            ),
        }
        rows = list(self.state.get("transfer_evaluations") or ())
        rows.append(result)
        self.state["transfer_evaluations"] = rows[-128:]
        self._persist(now)
        return dict(result)

    @_mutating
    def record_accelerated_batch(
        self,
        payload: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = list(self.state.get("accelerated_batches") or ())
        rows.append({"observed_at": now.isoformat(), **dict(payload)})
        self.state["accelerated_batches"] = rows[-128:]
        self._persist(now)

    def graduation_status(self) -> dict[str, Any]:
        """Measure whether the current game curriculum has enough real evidence."""
        base_environments = ("PONG", "MAZE", "FORAGING", "ROVER")
        episode_counts = {name: 0 for name in base_environments}
        transition_counts = {name: 0 for name in base_environments}
        for row in self.state.get("episodes") or ():
            if isinstance(row, Mapping):
                env = str(row.get("environment") or "").upper()
                if env in episode_counts:
                    episode_counts[env] += 1
        for row in self.state.get("transitions") or ():
            if isinstance(row, Mapping):
                env = str(row.get("environment") or "").upper()
                if env in transition_counts:
                    transition_counts[env] += 1

        population = self.population_summary("SCREEPS")
        members = population.get("members") or {}
        population_min_games = (
            min(int(row.get("games") or 0) for row in members.values())
            if members else 0
        )
        validated_macro_skills = sum(
            1
            for row in (self.state.get("skills") or {}).values()
            if isinstance(row, Mapping)
            and row.get("kind") == "MACRO"
            and bool(row.get("prospectively_validated"))
        )
        measured_transfers = sum(
            1
            for row in self.state.get("transfer_evaluations") or ()
            if isinstance(row, Mapping)
            and row.get("interpretation") == "MEASURED_CROSS_GAME_REUSE"
        )
        checks = {
            **{
                f"{name.lower()}_episodes": episode_counts[name] >= 40
                for name in base_environments
            },
            **{
                f"{name.lower()}_transitions": transition_counts[name] >= 750
                for name in base_environments
            },
            "screeps_matches": int(population.get("match_count") or 0) >= 50,
            "population_coverage": len(members) >= 4 and population_min_games >= 5,
            "validated_macro_skills": validated_macro_skills >= 2,
            "measured_cross_game_transfers": measured_transfers >= 3,
        }
        completed = sum(1 for value in checks.values() if value)
        return {
            "stage": "FOUNDATION_GAMES_V1",
            "graduated": all(checks.values()),
            "completed_requirements": completed,
            "total_requirements": len(checks),
            "progress": round(completed / len(checks), 6),
            "checks": checks,
            "evidence": {
                "episodes": episode_counts,
                "transitions": transition_counts,
                "screeps_matches": int(population.get("match_count") or 0),
                "population_member_count": len(members),
                "population_min_games": population_min_games,
                "validated_macro_skills": validated_macro_skills,
                "measured_cross_game_transfers": measured_transfers,
            },
            "next_stage": "HARDER_OPEN_WORLD_CURRICULUM",
        }

    def projection(self) -> dict[str, Any]:
        macro = [
            dict(row) for row in (self.state.get("skills") or {}).values()
            if isinstance(row, Mapping) and row.get("kind") == "MACRO"
        ]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "episode_count": len(self.state.get("episodes") or ()),
            "transition_count": len(self.state.get("transitions") or ()),
            "skill_count": len(self.state.get("skills") or {}),
            "macro_skill_count": len(macro),
            "macro_execution_count": len(self.state.get("macro_executions") or ()),
            "world_model_state_skill_count": len(self.state.get("world_model") or {}),
            "curricula": {
                key: dict(value)
                for key, value in (self.state.get("curricula") or {}).items()
                if isinstance(value, Mapping)
            },
            "population": {
                env: self.population_summary(env)
                for env in (self.state.get("population") or {}).keys()
            },
            "latest_dream": (
                dict((self.state.get("dreams") or [])[-1])
                if self.state.get("dreams")
                else None
            ),
            "recent_transfer_evaluations": [
                dict(row) for row in (self.state.get("transfer_evaluations") or [])[-8:]
                if isinstance(row, Mapping)
            ],
            "accelerated_batch_count": len(self.state.get("accelerated_batches") or ()),
            "graduation": self.graduation_status(),
            "reward_authority": "MEASURED_GAME_WORLD_ONLY",
            "offline_replay_can_promote": False,
        }

    def _persist(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)


__all__ = [
    "GameIntelligence",
    "MIN_CURRICULUM_EPISODES",
    "SCHEMA",
]
