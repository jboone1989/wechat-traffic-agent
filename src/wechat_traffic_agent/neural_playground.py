"""Developmental neural environments for Ferro's persistent production connectome.

These are not separate trained models. Each step stimulates the same recurrent LIF
substrate used by the living runtime and applies bounded environment reward through
its provenance-linked eligibility traces.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from .game_intelligence import GameIntelligence


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _signed_unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


class FerroBrainLab:
    SCHEMA_VERSION = "ferro-brain-lab.v1"
    ENVIRONMENTS = ("PONG", "MAZE", "FORAGING", "ROVER")
    MAX_RECENT = 64

    def __init__(self, state_root: Path, *, substrate: Any | None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.path = self.state_root / "application" / "brain_lab.json"
        self.state = self._load()
        self.game_intelligence = GameIntelligence(self.state_root)

    def _default_env(self, name: str) -> dict[str, Any]:
        if name == "PONG":
            return {
                "episode": 0, "step": 0, "reward_sum": 0.0,
                "ball_x": 0.22, "ball_y": 0.52, "ball_vx": 0.055, "ball_vy": 0.027,
                "paddle_y": 0.5, "hits": 0, "misses": 0, "streak": 0, "best_streak": 0,
                "last_action": "STAY", "last_reward": 0.0,
            }
        if name == "MAZE":
            return {
                "episode": 0, "step": 0, "reward_sum": 0.0, "x": 0, "y": 0,
                "goal_x": 6, "goal_y": 6, "reaches": 0, "collisions": 0,
                "last_action": "RIGHT", "last_reward": 0.0,
            }
        if name == "FORAGING":
            return {
                "episode": 0, "step": 0, "reward_sum": 0.0,
                "x": 0.2, "y": 0.2, "food_x": 0.8, "food_y": 0.75,
                "energy": 1.0, "foods": 0, "last_action": "RIGHT", "last_reward": 0.0,
            }
        return {
            "episode": 0, "step": 0, "reward_sum": 0.0,
            "position": 0.0, "heading_error": 0.65, "progress": 0.0,
            "gates": 0, "off_course": 0, "last_action": "LEFT", "last_reward": 0.0,
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raw = {}
        if raw.get("schema_version") != self.SCHEMA_VERSION:
            raw = {
                "schema_version": self.SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "total_steps": 0,
                "total_sessions": 0,
                "active_environment": "PONG",
                "environments": {name: self._default_env(name) for name in self.ENVIRONMENTS},
                "policies": {},
                "recent": [],
            }
        for name in self.ENVIRONMENTS:
            raw.setdefault("environments", {}).setdefault(name, self._default_env(name))
        raw.setdefault("policies", {})
        raw.setdefault("skill_execution", {})
        return raw

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _phase(seed: str) -> float:
        value = int.from_bytes(sha256(seed.encode()).digest()[:8], "little")
        return (value % 10_000) / 10_000.0

    def _stimulate(self, currents: Mapping[str, float], now: datetime, event_id: str) -> dict[str, Any]:
        if self.substrate is None:
            return {"status": "CONNECTOME_UNAVAILABLE", "delta_readout_hz": {}}
        burst = self.substrate.stimulate(
            dict(currents), now, source_event_id=event_id, feedback_eligible=True
        )
        return dict(burst or {})

    def _reward(self, *, event_id: str, action_channel: str, reward: float, now: datetime) -> dict[str, Any]:
        if self.substrate is None or not hasattr(self.substrate, "apply_feedback"):
            return {"status": "CONNECTOME_UNAVAILABLE", "changed_synapse_count": 0}
        feedback = SimpleNamespace(
            outcome_id=f"{event_id}:reward",
            source_event_id=event_id,
            observed_at=now,
            pathway=(str(action_channel),),
        )
        return dict(self.substrate.apply_feedback(feedback, utility=_signed_unit(reward)))

    def _policy_bucket(self, name: str, env: Mapping[str, Any], currents: Mapping[str, float]) -> str:
        if name == "PONG":
            error = float(env["ball_y"]) - float(env["paddle_y"])
            return f"e{max(-3,min(3,int(round(error*6))))}:vx{1 if float(env['ball_vx']) >= 0 else -1}:x{min(3,int(float(env['ball_x'])*4))}"
        if name == "MAZE":
            return f"{int(env['x'])},{int(env['y'])}"
        if name == "FORAGING":
            dx = float(env["food_x"]) - float(env["x"])
            dy = float(env["food_y"]) - float(env["y"])
            energy = min(3, int(float(env["energy"]) * 4))
            return f"dx{max(-2,min(2,int(round(dx*3))))}:dy{max(-2,min(2,int(round(dy*3))))}:en{energy}"
        if name == "ROVER":
            error = max(-4, min(4, int(round(float(env["heading_error"]) * 4))))
            progress = min(3, int(float(env["progress"]) * 4))
            return f"e{error}:p{progress}"
        return "|".join(
            f"{channel}:{min(3,max(0,int(float(value)*4)))}"
            for channel, value in sorted(currents.items())
        )[:512]

    def _temperament(self) -> dict[str, float]:
        try:
            raw = json.loads(
                (self.state_root / "application" / "neural_life.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError, json.JSONDecodeError):
            raw = {}
        return self.game_intelligence.temperament_from_neural_state(raw)

    @staticmethod
    def _experience_state_key(name: str, env: Mapping[str, Any]) -> str:
        if name == "PONG":
            return (
                f"ball:{round(float(env.get('ball_x') or 0.0),1)},"
                f"{round(float(env.get('ball_y') or 0.0),1)}:"
                f"paddle:{round(float(env.get('paddle_y') or 0.0),1)}"
            )
        if name == "MAZE":
            return f"{int(env.get('x') or 0)},{int(env.get('y') or 0)}"
        if name == "FORAGING":
            return (
                f"p:{round(float(env.get('x') or 0.0),1)},"
                f"{round(float(env.get('y') or 0.0),1)}:"
                f"f:{round(float(env.get('food_x') or 0.0),1)},"
                f"{round(float(env.get('food_y') or 0.0),1)}:"
                f"e:{round(float(env.get('energy') or 0.0),1)}"
            )
        if name == "ROVER":
            return (
                f"h:{round(float(env.get('heading_error') or 0.0),1)}:"
                f"p:{round(float(env.get('progress') or 0.0),1)}"
            )
        compact = {
            key: value
            for key, value in env.items()
            if key in {"episode_step", "last_target_action", "last_action"}
        }
        return json.dumps(compact, sort_keys=True, separators=(",", ":"))[:256]

    def _policy_row(self, name: str) -> dict[str, Any]:
        return self.state.setdefault("policies", {}).setdefault(
            name, {"states": {}, "updates": 0, "explorations": 0}
        )

    def _macro_choice(
        self,
        *,
        name: str,
        options: tuple[tuple[str, str], ...],
        event_id: str,
    ) -> tuple[str, str] | None:
        option_map = {str(action): str(channel) for action, channel in options}
        executions = self.state.setdefault("skill_execution", {})
        active = dict(executions.get(name) or {})
        queue = [str(value) for value in active.get("queue") or ()]
        while queue:
            action = queue.pop(0)
            if action not in option_map:
                continue
            active["queue"] = queue
            active["last_action"] = action
            executions[name] = active
            return action, option_map[action]
        if active:
            executions.pop(name, None)

        # Macro use is deliberately bounded. Offline discovery can only propose
        # a sequence; repeated real executions must validate it.
        if self._phase(f"macro-execute:{event_id}") >= 0.14:
            return None
        macro = self.game_intelligence.choose_macro_skill(
            name,
            valid_actions=tuple(option_map),
            seed=int(self._phase(f"macro-seed:{event_id}") * 1_000_000),
        )
        if macro is None:
            return None
        actions = [str(value) for value in macro.get("actions") or ()]
        if not actions or actions[0] not in option_map:
            return None
        execution_id = (
            f"macro-execution:{name.lower()}:"
            + sha256(f"{event_id}:{macro['skill_id']}".encode()).hexdigest()[:16]
        )
        executions[name] = {
            "execution_id": execution_id,
            "skill_id": str(macro["skill_id"]),
            "actions": actions,
            "queue": actions[1:],
            "reward_sum": 0.0,
            "steps": 0,
            "started_event_id": event_id,
            "last_action": actions[0],
        }
        return actions[0], option_map[actions[0]]

    def _settle_macro_step(
        self,
        *,
        name: str,
        reward: float,
        now: datetime,
    ) -> dict[str, Any] | None:
        executions = self.state.setdefault("skill_execution", {})
        active = dict(executions.get(name) or {})
        if not active:
            return None
        active["reward_sum"] = round(
            float(active.get("reward_sum") or 0.0) + float(reward), 6
        )
        active["steps"] = int(active.get("steps") or 0) + 1
        executions[name] = active
        if active.get("queue"):
            return {
                "status": "MACRO_RUNNING",
                "skill_id": active.get("skill_id"),
                "execution_id": active.get("execution_id"),
                "steps": active["steps"],
            }
        executions.pop(name, None)
        result = self.game_intelligence.record_macro_execution(
            environment=name,
            skill_id=str(active.get("skill_id") or ""),
            execution_id=str(active.get("execution_id") or ""),
            actions=tuple(str(value) for value in active.get("actions") or ()),
            reward=float(active.get("reward_sum") or 0.0),
            observed_at=now,
        )
        return {
            "status": "MACRO_SETTLED",
            "skill_id": active.get("skill_id"),
            "execution_id": active.get("execution_id"),
            "steps": active["steps"],
            "reward": active["reward_sum"],
            "prospectively_validated": result.get("prospectively_validated"),
        }

    def _choose(
        self,
        *,
        name: str,
        state_key: str,
        delta: Mapping[str, Any],
        options: tuple[tuple[str, str], ...],
        event_id: str,
    ) -> tuple[str, str]:
        if not options:
            raise ValueError("brain-lab action set cannot be empty")
        macro_choice = self._macro_choice(
            name=name,
            options=options,
            event_id=event_id,
        )
        if macro_choice is not None:
            return macro_choice
        policy = self._policy_row(name)
        values = policy.setdefault("states", {}).setdefault(state_key, {})
        raw = [float(delta.get(channel) or 0.0) for _, channel in options]
        lo, hi = min(raw), max(raw)
        neural = [0.5 if hi <= lo else (value - lo) / (hi - lo) for value in raw]
        scored = []
        for index, (action, channel) in enumerate(options):
            q = max(-1.0, min(1.0, float(values.get(action) or 0.0)))
            prediction = self.game_intelligence.predict(
                environment=name,
                state_key=state_key,
                skill_id=f"{name}:{action}",
            )
            expected = prediction.get("expected_reward")
            world_score = 0.5 if expected is None else (max(-1.0, min(1.0, float(expected))) + 1.0) / 2.0
            score = 0.25 * neural[index] + 0.50 * ((q + 1.0) / 2.0) + 0.25 * world_score
            scored.append((score, action, channel))
        temperament = self._temperament()
        explore_rate = max(0.04, min(0.24, 0.06 + 0.18 * float(temperament.get("exploration") or 0.0)))
        if self._phase(f"policy-explore:{event_id}") < explore_rate and len(options) > 1:
            pick = int(self._phase(f"policy-pick:{event_id}") * len(options)) % len(options)
            policy["explorations"] = int(policy.get("explorations") or 0) + 1
            return options[pick]
        _, action, channel = sorted(scored, key=lambda row: (-row[0], row[1]))[0]
        return action, channel

    def _learn_policy(self, *, name: str, state_key: str, action: str, reward: float) -> None:
        policy = self._policy_row(name)
        states = policy.setdefault("states", {})
        values = states.setdefault(state_key, {})
        before = float(values.get(action) or 0.0)
        after = before + 0.28 * (_signed_unit(reward) - before)
        values[action] = round(_signed_unit(after), 6)
        policy["updates"] = int(policy.get("updates") or 0) + 1
        if len(states) > 512:
            for key in sorted(states):
                if len(states) <= 512:
                    break
                if key != state_key:
                    states.pop(key, None)

    def _pong_step(self, env: dict[str, Any], now: datetime, event_id: str) -> tuple[dict[str, Any], float, dict[str, Any], dict[str, float]]:
        error = float(env["ball_y"]) - float(env["paddle_y"])
        approach = _clip((float(env["ball_x"]) - 0.45) / 0.55) if float(env["ball_vx"]) > 0 else 0.0
        currents = {
            "prediction_due": _clip(max(0.0, error) * 1.5),
            "error_recovery": _clip(max(0.0, -error) * 1.5),
            "task_urgency": _clip(abs(error) * 1.4 + approach * 0.6),
            "opportunity": _clip(0.25 + approach * 0.75),
            "curiosity": 0.18,
        }
        burst = self._stimulate(currents, now, event_id)
        options = (("UP", "prediction_due"), ("DOWN", "error_recovery"), ("STAY", "opportunity"))
        state_key = self._policy_bucket("PONG", env, currents)
        action, action_channel = self._choose(
            name="PONG", state_key=state_key,
            delta=burst.get("delta_readout_hz") or {}, options=options, event_id=event_id,
        )
        env["paddle_y"] = _clip(float(env["paddle_y"]) + {"UP": 0.085, "DOWN": -0.085, "STAY": 0.0}[action])
        env["ball_x"] = float(env["ball_x"]) + float(env["ball_vx"])
        env["ball_y"] = float(env["ball_y"]) + float(env["ball_vy"])
        if env["ball_y"] <= 0.02 or env["ball_y"] >= 0.98:
            env["ball_vy"] = -float(env["ball_vy"])
            env["ball_y"] = _clip(float(env["ball_y"]), 0.02, 0.98)
        if env["ball_x"] <= 0.02:
            env["ball_vx"] = abs(float(env["ball_vx"]))
            env["ball_x"] = 0.02
        reward = -0.003 * abs(float(env["ball_y"]) - float(env["paddle_y"]))
        if env["ball_x"] >= 0.96:
            if abs(float(env["ball_y"]) - float(env["paddle_y"])) <= 0.18:
                reward = 1.0
                env["hits"] = int(env["hits"]) + 1
                env["streak"] = int(env["streak"]) + 1
                env["best_streak"] = max(int(env["best_streak"]), int(env["streak"]))
                env["ball_vx"] = -abs(float(env["ball_vx"]))
                env["ball_x"] = 0.95
            else:
                reward = -1.0
                env["misses"] = int(env["misses"]) + 1
                env["streak"] = 0
                env["episode"] = int(env["episode"]) + 1
                phase = self._phase(f"pong:{env['episode']}")
                env["ball_x"], env["ball_y"] = 0.18, 0.15 + 0.7 * phase
                env["ball_vx"] = 0.05 + 0.01 * self._phase(f"pong-vx:{env['episode']}")
                env["ball_vy"] = (self._phase(f"pong-vy:{env['episode']}") - 0.5) * 0.07
        env["last_action"] = action
        self._learn_policy(name="PONG", state_key=state_key, action=action, reward=reward)
        return env, reward, burst, currents, action_channel

    @staticmethod
    def _maze_blocked(x: int, y: int) -> bool:
        return (x, y) in {(1, 1), (1, 2), (3, 1), (3, 2), (3, 3), (5, 3), (5, 4), (2, 5)}

    def _maze_step(self, env: dict[str, Any], now: datetime, event_id: str) -> tuple[dict[str, Any], float, dict[str, Any], dict[str, float]]:
        dx, dy = int(env["goal_x"]) - int(env["x"]), int(env["goal_y"]) - int(env["y"])
        currents = {
            "opportunity": _clip(max(0, dx) / 6),
            "social_attention": _clip(max(0, -dx) / 6),
            "prediction_due": _clip(max(0, dy) / 6),
            "error_recovery": _clip(max(0, -dy) / 6),
            "curiosity": 0.30,
            "task_urgency": _clip((abs(dx) + abs(dy)) / 12),
        }
        burst = self._stimulate(currents, now, event_id)
        options = (("RIGHT", "opportunity"), ("LEFT", "social_attention"), ("UP", "prediction_due"), ("DOWN", "error_recovery"))
        state_key = self._policy_bucket("MAZE", env, currents)
        action, action_channel = self._choose(
            name="MAZE", state_key=state_key,
            delta=burst.get("delta_readout_hz") or {}, options=options, event_id=event_id,
        )
        sx, sy = {"RIGHT": (1, 0), "LEFT": (-1, 0), "UP": (0, 1), "DOWN": (0, -1)}[action]
        nx, ny = int(env["x"]) + sx, int(env["y"]) + sy
        old_distance = abs(dx) + abs(dy)
        reward = -0.02
        if nx < 0 or nx > 6 or ny < 0 or ny > 6 or self._maze_blocked(nx, ny):
            env["collisions"] = int(env["collisions"]) + 1
            reward = -0.20
        else:
            env["x"], env["y"] = nx, ny
            new_distance = abs(int(env["goal_x"]) - nx) + abs(int(env["goal_y"]) - ny)
            reward += 0.06 if new_distance < old_distance else -0.04
            if (nx, ny) == (int(env["goal_x"]), int(env["goal_y"])):
                reward = 1.0
                env["reaches"] = int(env["reaches"]) + 1
                env["episode"] = int(env["episode"]) + 1
                env["x"], env["y"] = 0, 0
        env["last_action"] = action
        self._learn_policy(name="MAZE", state_key=state_key, action=action, reward=reward)
        return env, reward, burst, currents, action_channel

    def _foraging_step(self, env: dict[str, Any], now: datetime, event_id: str) -> tuple[dict[str, Any], float, dict[str, Any], dict[str, float]]:
        dx, dy = float(env["food_x"]) - float(env["x"]), float(env["food_y"]) - float(env["y"])
        distance = math.hypot(dx, dy)
        currents = {
            "opportunity": _clip(max(0.0, dx)),
            "social_attention": _clip(max(0.0, -dx)),
            "prediction_due": _clip(max(0.0, dy)),
            "error_recovery": _clip(max(0.0, -dy)),
            "resource_pressure": _clip(1.0 - float(env["energy"])),
            "curiosity": _clip(distance),
        }
        burst = self._stimulate(currents, now, event_id)
        options = (("RIGHT", "opportunity"), ("LEFT", "social_attention"), ("UP", "prediction_due"), ("DOWN", "error_recovery"))
        state_key = self._policy_bucket("FORAGING", env, currents)
        action, action_channel = self._choose(
            name="FORAGING", state_key=state_key,
            delta=burst.get("delta_readout_hz") or {}, options=options, event_id=event_id,
        )
        sx, sy = {"RIGHT": (0.07, 0.0), "LEFT": (-0.07, 0.0), "UP": (0.0, 0.07), "DOWN": (0.0, -0.07)}[action]
        env["x"], env["y"] = _clip(float(env["x"]) + sx), _clip(float(env["y"]) + sy)
        env["energy"] = _clip(float(env["energy"]) - 0.018)
        new_distance = math.hypot(float(env["food_x"]) - float(env["x"]), float(env["food_y"]) - float(env["y"]))
        reward = 0.03 if new_distance < distance else -0.03
        if new_distance < 0.11:
            reward = 1.0
            env["foods"] = int(env["foods"]) + 1
            env["energy"] = _clip(float(env["energy"]) + 0.55)
            env["episode"] = int(env["episode"]) + 1
            env["food_x"] = 0.08 + 0.84 * self._phase(f"food-x:{env['episode']}")
            env["food_y"] = 0.08 + 0.84 * self._phase(f"food-y:{env['episode']}")
        elif float(env["energy"]) <= 0.01:
            reward = -1.0
            env["episode"] = int(env["episode"]) + 1
            env["x"], env["y"], env["energy"] = 0.2, 0.2, 1.0
        env["last_action"] = action
        self._learn_policy(name="FORAGING", state_key=state_key, action=action, reward=reward)
        return env, reward, burst, currents, action_channel

    def _rover_step(self, env: dict[str, Any], now: datetime, event_id: str) -> tuple[dict[str, Any], float, dict[str, Any], dict[str, float]]:
        error = float(env["heading_error"])
        currents = {
            "prediction_due": _clip(max(0.0, error)),
            "error_recovery": _clip(max(0.0, -error)),
            "opportunity": _clip(1.0 - abs(error)),
            "threat_risk": _clip(abs(error)),
            "curiosity": 0.22,
        }
        burst = self._stimulate(currents, now, event_id)
        options = (("LEFT", "prediction_due"), ("RIGHT", "error_recovery"), ("FORWARD", "opportunity"))
        state_key = self._policy_bucket("ROVER", env, currents)
        action, action_channel = self._choose(
            name="ROVER", state_key=state_key,
            delta=burst.get("delta_readout_hz") or {}, options=options, event_id=event_id,
        )
        if action == "LEFT":
            error -= 0.16
        elif action == "RIGHT":
            error += 0.16
        else:
            env["progress"] = float(env["progress"]) + max(0.0, 0.08 * (1.0 - abs(error)))
        drift = (self._phase(f"rover:{env['episode']}:{env['step']}") - 0.5) * 0.05
        error = _signed_unit(error + drift)
        env["heading_error"] = error
        reward = 0.05 * (1.0 - abs(error)) if action == "FORWARD" else 0.01 * (1.0 - abs(error))
        if abs(error) > 0.92:
            env["off_course"] = int(env["off_course"]) + 1
            reward = -0.6
            env["heading_error"] = (self._phase(f"rover-reset:{env['episode']}") - 0.5) * 1.2
        if float(env["progress"]) >= 1.0:
            reward = 1.0
            env["gates"] = int(env["gates"]) + 1
            env["episode"] = int(env["episode"]) + 1
            env["progress"] = 0.0
            env["heading_error"] = (self._phase(f"rover-gate:{env['episode']}") - 0.5) * 1.2
        env["last_action"] = action
        self._learn_policy(name="ROVER", state_key=state_key, action=action, reward=reward)
        return env, reward, burst, currents, action_channel

    def _generated_specs(self) -> dict[str, dict[str, Any]]:
        raw = {}
        try:
            raw = json.loads(
                (self.state_root / "application" / "developmental_agency.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        worlds = raw.get("worlds") if isinstance(raw.get("worlds"), Mapping) else {}
        return {
            str(world_id).upper(): dict(spec)
            for world_id, spec in worlds.items()
            if isinstance(spec, Mapping)
            and str(spec.get("status") or "") not in {"REJECTED", "RETIRED"}
        }

    @staticmethod
    def _generated_default(spec: Mapping[str, Any]) -> dict[str, Any]:
        actions = [str(value) for value in spec.get("actions") or ("A0", "A1", "A2")]
        return {
            "episode": 0,
            "step": 0,
            "episode_step": 0,
            "reward_sum": 0.0,
            "correct": 0,
            "incorrect": 0,
            "last_action": actions[0],
            "last_target_action": actions[0],
            "last_reward": 0.0,
        }

    def _generated_step(
        self,
        *,
        name: str,
        spec: Mapping[str, Any],
        env: dict[str, Any],
        now: datetime,
        event_id: str,
    ) -> tuple[dict[str, Any], float, dict[str, Any], dict[str, float]]:
        seed = str(spec.get("seed") or name)
        sensors = [str(value) for value in spec.get("sensor_channels") or ()]
        actions = [str(value) for value in spec.get("actions") or ()]
        action_channels = {
            str(key): str(value)
            for key, value in (spec.get("action_channels") or {}).items()
        }
        if not sensors or not actions:
            raise ValueError(f"generated world {name} has no sensors/actions")
        period = max(1, int(spec.get("context_switch_period") or 6))
        episode_step = int(env.get("episode_step") or 0)
        context_slot = episode_step // period
        noise = _clip(float(spec.get("noise") or 0.0), 0.0, 0.25)
        currents: dict[str, float] = {}
        for channel in sensors:
            base = self._phase(
                f"{seed}:episode:{int(env.get('episode') or 0)}:context:{context_slot}:{channel}"
            )
            jitter = (
                self._phase(
                    f"{seed}:episode:{int(env.get('episode') or 0)}:step:{episode_step}:{channel}:noise"
                )
                - 0.5
            ) * 2.0 * noise
            currents[channel] = _clip(base + jitter)

        latent_weights = spec.get("latent_weights") if isinstance(spec.get("latent_weights"), Mapping) else {}
        ranked_targets: list[tuple[float, str]] = []
        for action in actions:
            weights = latent_weights.get(action) if isinstance(latent_weights.get(action), Mapping) else {}
            score = sum(
                float(weights.get(channel) or 0.0) * (float(currents[channel]) - 0.5)
                for channel in sensors
            )
            ranked_targets.append((score, action))
        ranked_targets.sort(key=lambda row: (-row[0], row[1]))
        target_action = ranked_targets[0][1]

        burst = self._stimulate(currents, now, event_id)
        options = tuple(
            (action, action_channels.get(action) or sensors[index % len(sensors)])
            for index, action in enumerate(actions)
        )
        state_key = self._policy_bucket(name, env, currents)
        action, action_channel = self._choose(
            name=name, state_key=state_key, delta=burst.get("delta_readout_hz") or {}, options=options, event_id=event_id,
        )
        reward_spec = spec.get("reward") if isinstance(spec.get("reward"), Mapping) else {}
        reward = float(
            reward_spec.get("correct") if action == target_action else reward_spec.get("incorrect")
        )
        reward += float(reward_spec.get("step_cost") or 0.0)

        env["episode_step"] = episode_step + 1
        env["last_action"] = action
        env["last_target_action"] = target_action
        if action == target_action:
            env["correct"] = int(env.get("correct") or 0) + 1
        else:
            env["incorrect"] = int(env.get("incorrect") or 0) + 1
        horizon = max(8, int(spec.get("episode_horizon") or 48))
        if int(env["episode_step"]) >= horizon:
            env["episode"] = int(env.get("episode") or 0) + 1
            env["episode_step"] = 0
        self._learn_policy(name=name, state_key=state_key, action=action, reward=reward)
        return env, reward, burst, currents, action_channel

    def step(self, environment: str, *, now: datetime | None = None) -> dict[str, Any]:
        name = str(environment).upper()
        generated = self._generated_specs()
        if name not in self.ENVIRONMENTS and name not in generated:
            raise ValueError(f"unknown brain-lab environment: {environment}")
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if name in generated:
            self.state.setdefault("environments", {}).setdefault(
                name, self._generated_default(generated[name])
            )
        env = dict(self.state["environments"][name])
        before_env = dict(env)
        env["step"] = int(env.get("step") or 0) + 1
        event_id = f"brain-lab:{name.lower()}:{int(env.get('episode') or 0)}:{int(env['step'])}"
        if name == "PONG":
            env, reward, burst, currents, action_channel = self._pong_step(env, now, event_id)
        elif name == "MAZE":
            env, reward, burst, currents, action_channel = self._maze_step(env, now, event_id)
        elif name == "FORAGING":
            env, reward, burst, currents, action_channel = self._foraging_step(env, now, event_id)
        elif name == "ROVER":
            env, reward, burst, currents, action_channel = self._rover_step(env, now, event_id)
        else:
            env, reward, burst, currents, action_channel = self._generated_step(
                name=name,
                spec=generated[name],
                env=env,
                now=now,
                event_id=event_id,
            )
        feedback = self._reward(event_id=event_id, action_channel=action_channel, reward=reward, now=now)
        macro_execution = self._settle_macro_step(
            name=name,
            reward=float(reward),
            now=now,
        )
        env["last_reward"] = round(float(reward), 6)
        env["reward_sum"] = round(float(env.get("reward_sum") or 0.0) + float(reward), 6)
        self.state["environments"][name] = env
        self.state["active_environment"] = name
        self.state["updated_at"] = now.isoformat()
        self.state["total_steps"] = int(self.state.get("total_steps") or 0) + 1
        row = {
            "at": now.isoformat(), "environment": name, "event_id": event_id,
            "action": env.get("last_action"),
            "target_action": env.get("last_target_action") if name in generated else None,
            "reward": round(float(reward), 6),
            "spikes": int(burst.get("spikes") or 0),
            "active_neurons": int(burst.get("active_neurons") or 0),
            "changed_synapses": int(feedback.get("changed_synapse_count") or 0),
            "action_channel": action_channel,
            "macro_execution": macro_execution,
        }
        row["state_key"] = self._experience_state_key(name, before_env)
        row["next_state_key"] = self._experience_state_key(name, env)
        recent = list(self.state.get("recent") or ())
        recent.append(row)
        self.state["recent"] = recent[-self.MAX_RECENT:]
        self._write()
        self.game_intelligence.record_transition(
            environment=name,
            episode_id=f"brain-lab:{name.lower()}:{int(before_env.get('episode') or 0)}",
            state_key=str(row["state_key"]),
            action=str(row.get("action") or ""),
            reward=float(row.get("reward") or 0.0),
            next_state_key=str(row["next_state_key"]),
            skill_id=f"{name}:{row.get('action')}",
            success=(True if float(row.get("reward") or 0.0) >= 0.9 else None),
            observed_at=now,
            source="BRAIN_LAB",
        )
        return row

    def run_session(self, *, environment: str | None = None, steps: int = 12, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if environment is None:
            index = int(self.state.get("total_sessions") or 0) % len(self.ENVIRONMENTS)
            environment = self.ENVIRONMENTS[index]
        count = max(1, min(64, int(steps)))
        rows = [self.step(str(environment), now=now) for _ in range(count)]
        self.state["total_sessions"] = int(self.state.get("total_sessions") or 0) + 1
        self.state["updated_at"] = now.isoformat()
        self._write()
        session_reward = round(sum(float(row["reward"]) for row in rows), 6)
        session_id = f"brain-session:{str(environment).lower()}:{int(self.state['total_sessions'])}"
        self.game_intelligence.record_episode(
            environment=str(environment).upper(),
            episode_id=session_id,
            reward=max(-1.0, min(1.0, session_reward)),
            success=session_reward > 0.0,
            metrics={"steps": len(rows), "raw_reward": session_reward},
            evidence_ref=rows[-1].get("event_id"),
            observed_at=now,
            source="BRAIN_LAB_SESSION",
        )
        self.game_intelligence.discover_macro_skills(str(environment).upper(), min_support=3)
        return {
            "status": "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM" if self.substrate is not None else "CONNECTOME_UNAVAILABLE",
            "environment": str(environment).upper(),
            "steps": len(rows),
            "reward": session_reward,
            "changed_synapses": sum(int(row["changed_synapses"]) for row in rows),
            "latest": rows[-1],
            "projection": self.projection(),
        }

    def run_accelerated_arena(
        self,
        *,
        environments: tuple[str, ...] | None = None,
        sessions_per_environment: int = 4,
        base_steps: int = 32,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Run many headless sessions in one wake instead of scheduler-paced play."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        names = tuple(str(value).upper() for value in (environments or self.ENVIRONMENTS))
        sessions = max(1, min(32, int(sessions_per_environment)))
        results: list[dict[str, Any]] = []
        total_steps = 0
        for name in names:
            plan = self.game_intelligence.training_plan(name, base_steps=base_steps)
            for _ in range(sessions):
                row = self.run_session(
                    environment=name,
                    steps=int(plan["steps"]),
                    now=now,
                )
                total_steps += int(row["steps"])
                results.append({
                    "environment": name,
                    "reward": row["reward"],
                    "steps": row["steps"],
                    "curriculum_level": int(
                        self.game_intelligence.curriculum(name).get("level") or 1
                    ),
                })
        transfers: list[dict[str, Any]] = []
        for index, source in enumerate(names):
            for target in names[index + 1:]:
                transfers.append(
                    self.game_intelligence.evaluate_transfer(
                        source_environment=source,
                        target_environment=target,
                        now=now,
                    )
                )
        payload = {
            "mode": "HEADLESS_ACCELERATED_ARENA",
            "environment_count": len(names),
            "session_count": len(results),
            "total_steps": total_steps,
            "results": results[-64:],
            "transfer_evaluations": transfers[-24:],
        }
        self.game_intelligence.record_accelerated_batch(payload, now=now)
        return payload

    def projection(self) -> dict[str, Any]:
        generated = self._generated_specs()
        names = list(self.ENVIRONMENTS) + sorted(generated)
        environments = {
            name: dict(
                self.state.get("environments", {}).get(
                    name,
                    self._generated_default(generated[name]) if name in generated else self._default_env(name),
                )
            )
            for name in names
        }
        generated_meta = {}
        for name, spec in generated.items():
            env = environments[name]
            attempts = int(env.get("correct") or 0) + int(env.get("incorrect") or 0)
            generated_meta[name] = {
                "status": spec.get("status"),
                "target_task": spec.get("target_task"),
                "avoids_environment": spec.get("avoids_environment"),
                "sensor_channels": list(spec.get("sensor_channels") or ()),
                "actions": list(spec.get("actions") or ()),
                "correct_rate": None if attempts == 0 else round(int(env.get("correct") or 0) / attempts, 6),
                "episodes": int(env.get("episode") or 0),
                "steps": int(env.get("step") or 0),
                "reward_sum": float(env.get("reward_sum") or 0.0),
            }
        pong = environments["PONG"]
        attempts = int(pong.get("hits") or 0) + int(pong.get("misses") or 0)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": self.state.get("updated_at"),
            "same_production_connectome": self.substrate is not None,
            "active_environment": self.state.get("active_environment"),
            "total_steps": int(self.state.get("total_steps") or 0),
            "total_sessions": int(self.state.get("total_sessions") or 0),
            "environments": environments,
            "ancestral_environments": list(self.ENVIRONMENTS),
            "generated_worlds": generated_meta,
            "available_environments": names,
            "pong_hit_rate": None if attempts == 0 else round(int(pong.get("hits") or 0) / attempts, 6),
            "policy_learning": {
                name: {
                    "state_count": len((self.state.get("policies", {}).get(name) or {}).get("states") or {}),
                    "updates": int((self.state.get("policies", {}).get(name) or {}).get("updates") or 0),
                    "explorations": int((self.state.get("policies", {}).get(name) or {}).get("explorations") or 0),
                }
                for name in names
            },
            "recent": [dict(row) for row in (self.state.get("recent") or [])[-24:]],
            "learning_rule": "connectome action prior + persistent state-action reward learning + empirical world model + provenance-linked recurrent feedback",
            "game_intelligence": self.game_intelligence.projection(),
        }


__all__ = ["FerroBrainLab"]
