"""Experience-bound motor cortex and habits for Ferro.

Motor units begin without semantics. Recurrent neural activity selects a unit;
that unit explores a low-level runtime action, then real task outcomes strengthen
or weaken the motor->action association. Stable successful associations become
habits and can bypass language-model deliberation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping


MOTOR_SCHEMA = "ferro-neural-motor.v1"
MOTOR_UNIT_COUNT = 32
DEFAULT_ACTIONS = (
    "OBSERVE_WORLD",
    "RESOURCE_REVIEW",
    "CHECK_X",
    "CONTENT_COGNITION",
    "REFLECT",
    "NEURAL_PLAY",
    "NEURAL_DREAM",
)
POSITIVE_STATUSES = {
    "OK", "PUBLISHED", "SETTLED", "DREAMED", "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM",
    "NO_NEW_OUTCOMES", "NO_FRESH_EVIDENCE", "NO_RELEVANT_EVIDENCE",
}
NEGATIVE_STATUSES = {"ERROR", "DEGRADED", "SHUTDOWN_CHECKPOINT"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


class NeuralMotorCortex:
    """Unlabeled motor units that acquire runtime action meaning from outcomes."""

    MAX_DECISIONS = 256
    HABIT_MIN_TRIALS = 5
    HABIT_MEAN_REWARD = 0.55
    DORMANT_MIN_TRIALS = 8
    DORMANT_MEAN_REWARD = -0.20

    def __init__(
        self,
        state_root: Path,
        *,
        substrate: Any | None,
        allowed_actions: Iterable[str] = DEFAULT_ACTIONS,
    ) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.allowed_actions = tuple(dict.fromkeys(str(x) for x in allowed_actions if str(x)))
        self.path = self.state_root / "application" / "neural_motor.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        payload = _read_json(self.path)
        if payload.get("schema_version") == MOTOR_SCHEMA:
            return payload
        return {
            "schema_version": MOTOR_SCHEMA,
            "updated_at": None,
            "decision_seq": 0,
            "units": {
                f"motor_{index:03d}": {
                    "motor_id": f"motor_{index:03d}",
                    "meaning": None,
                    "state": "UNBOUND",
                    "associations": {},
                    "selected_count": 0,
                    "last_selected_at": None,
                }
                for index in range(MOTOR_UNIT_COUNT)
            },
            "decisions": [],
            "habit_count": 0,
        }

    @staticmethod
    def _currents(neural_life: Mapping[str, Any]) -> dict[str, float]:
        drives = neural_life.get("drives") if isinstance(neural_life.get("drives"), Mapping) else {}
        affect = neural_life.get("affect") if isinstance(neural_life.get("affect"), Mapping) else {}
        intention = neural_life.get("intention") if isinstance(neural_life.get("intention"), Mapping) else {}
        name = str(intention.get("name") or "")
        currents = {
            "resource_pressure": min(1.5, float(drives.get("survival") or 0.0) + 0.35 * float(affect.get("resource_hunger") or 0.0)),
            "social_attention": min(1.5, float(drives.get("reproduction") or 0.0) + 0.30 * float(affect.get("social_need") or 0.0)),
            "curiosity": min(1.5, float(drives.get("evolution") or 0.0) + 0.35 * float(affect.get("curiosity") or 0.0)),
            "error_recovery": min(1.5, float(affect.get("frustration") or 0.0)),
            "task_urgency": min(1.5, float(affect.get("arousal") or 0.0)),
            "opportunity": min(1.5, float(affect.get("confidence") or 0.0)),
            "prediction_due": min(1.5, float(affect.get("boredom") or 0.0)),
            "threat_risk": min(1.5, float(drives.get("survival") or 0.0) * 0.65),
        }
        if name == "SURVIVE":
            currents["resource_pressure"] = min(1.5, currents["resource_pressure"] + 0.35)
        elif name == "REPRODUCE":
            currents["social_attention"] = min(1.5, currents["social_attention"] + 0.35)
        elif name == "EVOLVE":
            currents["curiosity"] = min(1.5, currents["curiosity"] + 0.35)
        return currents

    @staticmethod
    def _motor_activations(burst: Mapping[str, Any]) -> dict[str, float]:
        signature = str(
            burst.get("response_spike_pattern_digest")
            or burst.get("state_digest")
            or burst.get("response_digest")
            or ""
        )
        seed = (
            signature
            + "|"
            + str(burst.get("spikes") or 0)
            + "|"
            + str(burst.get("active_neurons") or 0)
            + "|"
            + str(burst.get("synaptic_events") or 0)
        )
        digest = sha256(seed.encode("utf-8")).digest()
        return {
            f"motor_{index:03d}": round(digest[index] / 255.0, 6)
            for index in range(MOTOR_UNIT_COUNT)
        }

    @staticmethod
    def _association_mean(raw: Mapping[str, Any]) -> float:
        trials = int(raw.get("trials") or 0)
        return 0.0 if trials <= 0 else float(raw.get("reward_sum") or 0.0) / trials

    def _best_learned_action(self, unit: Mapping[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        associations = unit.get("associations") if isinstance(unit.get("associations"), Mapping) else {}
        candidates = []
        for action, raw in associations.items():
            if action not in self.allowed_actions or not isinstance(raw, Mapping):
                continue
            trials = int(raw.get("trials") or 0)
            mean = self._association_mean(raw)
            if trials > 0:
                candidates.append((mean, trials, str(action), dict(raw)))
        if not candidates:
            return None, None
        candidates.sort(key=lambda row: (-row[0], -row[1], row[2]))
        _mean, _trials, action, raw = candidates[0]
        return action, raw

    def _explore_action(self, motor_id: str, decision_seq: int) -> str:
        if not self.allowed_actions:
            raise RuntimeError("motor cortex has no allowed actions")
        # Exploration does not encode motor meaning. The mapping becomes meaningful
        # only after world outcomes strengthen a specific association.
        token = sha256(f"{motor_id}|explore|{decision_seq}".encode("utf-8")).digest()
        return self.allowed_actions[int.from_bytes(token[:4], "little") % len(self.allowed_actions)]

    def select(
        self,
        *,
        neural_life: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.substrate is None:
            return {"status": "CONNECTOME_UNAVAILABLE", "task": None}
        for prior in reversed(self.state.get("decisions") or ()):
            if str(prior.get("status") or "") == "PENDING_OUTCOME":
                return {**dict(prior), "status": "PENDING_OUTCOME_EXISTING"}
        seq = int(self.state.get("decision_seq") or 0) + 1
        event_id = f"motor-probe:{seq}"
        currents = self._currents(neural_life)
        burst = dict(self.substrate.stimulate(
            currents,
            now,
            source_event_id=event_id,
            feedback_eligible=True,
        ) or {})
        activations = self._motor_activations(burst)
        units = dict(self.state.get("units") or {})
        effective: dict[str, float] = {}
        for motor_id, neural_activation in activations.items():
            unit = units.get(motor_id) if isinstance(units.get(motor_id), Mapping) else {}
            _action, association = self._best_learned_action(unit)
            trials = int((association or {}).get("trials") or 0)
            learned_mean = max(0.0, self._association_mean(association or {}))
            # World-success makes a motor unit easier to re-enter, but never fully
            # overrides the current connectome state. That is how a habit stabilizes.
            effective[motor_id] = float(neural_activation) + 0.25 * learned_mean + 0.03 * min(5, trials)
        ranked = sorted(effective.items(), key=lambda item: (-item[1], item[0]))
        # Skip dormant units if another active unit is available.
        selected_motor = next(
            (motor for motor, _ in ranked if str((units.get(motor) or {}).get("state") or "") != "DORMANT"),
            ranked[0][0],
        )
        activation = float(activations[selected_motor])
        unit = dict(units.get(selected_motor) or {})
        learned_action, association = self._best_learned_action(unit)
        association_trials = int((association or {}).get("trials") or 0)
        association_mean = self._association_mean(association or {})
        is_habit = (
            learned_action is not None
            and association_trials >= self.HABIT_MIN_TRIALS
            and association_mean >= self.HABIT_MEAN_REWARD
        )
        if learned_action is not None and (is_habit or association_mean > 0.15):
            task = learned_action
            decision_kind = "HABIT" if is_habit else "LEARNED"
        else:
            task = self._explore_action(selected_motor, seq)
            decision_kind = "EXPLORATION"

        decision_id = f"motor-decision:{sha256(f'{seq}|{selected_motor}|{task}'.encode()).hexdigest()[:20]}"
        decision = {
            "decision_id": decision_id,
            "source_event_id": event_id,
            "seq": seq,
            "created_at": now.isoformat(),
            "motor_id": selected_motor,
            "activation": round(activation, 6),
            "stimulus_channels": [key for key, value in currents.items() if float(value) > 0],
            "task": task,
            "decision_kind": decision_kind,
            "authority": "NEURAL_HABIT" if is_habit else "NEURAL_MOTOR",
            "status": "PENDING_OUTCOME",
            "outcome": None,
            "burst": {
                "spikes": int(burst.get("spikes") or 0),
                "active_neurons": int(burst.get("active_neurons") or 0),
                "signature": str(burst.get("response_spike_pattern_digest") or ""),
            },
        }
        unit["selected_count"] = int(unit.get("selected_count") or 0) + 1
        unit["last_selected_at"] = now.isoformat()
        units[selected_motor] = unit
        decisions = list(self.state.get("decisions") or ())
        decisions.append(decision)
        self.state.update({
            "updated_at": now.isoformat(),
            "decision_seq": seq,
            "units": units,
            "decisions": decisions[-self.MAX_DECISIONS:],
        })
        _write_json(self.path, self.state)
        return dict(decision)

    @staticmethod
    def reward_for_result(result: Mapping[str, Any]) -> float:
        status = str(result.get("status") or "UNKNOWN").upper()
        if status in POSITIVE_STATUSES:
            return 0.75
        if status in NEGATIVE_STATUSES:
            return -0.85
        if status in {"DISABLED", "NOOP", "ALREADY_PROCESSED"}:
            return -0.10
        return 0.15

    def observe_outcome(
        self,
        *,
        task: str,
        result: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        decisions = list(self.state.get("decisions") or ())
        index = next(
            (
                i for i in range(len(decisions) - 1, -1, -1)
                if str(decisions[i].get("task") or "") == str(task)
                and str(decisions[i].get("status") or "") == "PENDING_OUTCOME"
            ),
            None,
        )
        if index is None:
            return None
        decision = dict(decisions[index])
        reward = self.reward_for_result(result)
        physical_feedback = None
        if self.substrate is not None and hasattr(self.substrate, "apply_feedback"):
            feedback = SimpleNamespace(
                outcome_id=f"{decision['decision_id']}:world-outcome",
                source_event_id=str(decision.get("source_event_id") or ""),
                observed_at=observed_at,
                pathway=tuple(str(x) for x in decision.get("stimulus_channels") or ()),
            )
            physical_feedback = dict(self.substrate.apply_feedback(
                feedback,
                utility=reward,
            ) or {})
        decision["status"] = "SETTLED"
        decision["outcome"] = {
            "observed_at": observed_at.isoformat(),
            "status": str(result.get("status") or "UNKNOWN"),
            "reward": reward,
        }
        decisions[index] = decision

        units = dict(self.state.get("units") or {})
        motor_id = str(decision["motor_id"])
        unit = dict(units.get(motor_id) or {})
        associations = dict(unit.get("associations") or {})
        action = str(decision["task"])
        association = dict(associations.get(action) or {})
        trials = int(association.get("trials") or 0) + 1
        reward_sum = float(association.get("reward_sum") or 0.0) + reward
        mean_reward = reward_sum / trials
        association.update({
            "action": action,
            "trials": trials,
            "reward_sum": round(reward_sum, 6),
            "mean_reward": round(mean_reward, 6),
            "last_outcome_at": observed_at.isoformat(),
        })
        associations[action] = association
        unit["associations"] = associations

        if trials >= self.HABIT_MIN_TRIALS and mean_reward >= self.HABIT_MEAN_REWARD:
            unit["state"] = "HABIT"
            unit["meaning"] = action
            unit["meaning_acquired_at"] = observed_at.isoformat()
        elif trials >= self.DORMANT_MIN_TRIALS and mean_reward <= self.DORMANT_MEAN_REWARD:
            unit["state"] = "DORMANT"
            if unit.get("meaning") == action:
                unit["meaning"] = None
        elif unit.get("state") != "HABIT":
            unit["state"] = "LEARNING"
        units[motor_id] = unit
        habit_count = sum(1 for raw in units.values() if str(raw.get("state") or "") == "HABIT")
        self.state.update({
            "updated_at": observed_at.isoformat(),
            "units": units,
            "decisions": decisions,
            "habit_count": habit_count,
        })
        _write_json(self.path, self.state)
        return {
            "decision_id": decision["decision_id"],
            "motor_id": motor_id,
            "task": action,
            "reward": reward,
            "association": association,
            "motor_state": unit["state"],
            "meaning": unit.get("meaning"),
            "physical_feedback": physical_feedback,
        }

    def projection(self) -> dict[str, Any]:
        units = list((self.state.get("units") or {}).values())
        habits = [
            {
                "motor_id": row.get("motor_id"),
                "meaning": row.get("meaning"),
                "selected_count": row.get("selected_count"),
                "meaning_acquired_at": row.get("meaning_acquired_at"),
                "association": (row.get("associations") or {}).get(str(row.get("meaning") or "")),
            }
            for row in units if str(row.get("state") or "") == "HABIT"
        ]
        learning = sum(1 for row in units if str(row.get("state") or "") == "LEARNING")
        dormant = sum(1 for row in units if str(row.get("state") or "") == "DORMANT")
        return {
            "schema_version": MOTOR_SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "motor_unit_count": MOTOR_UNIT_COUNT,
            "semantics_preassigned": False,
            "habit_count": len(habits),
            "learning_unit_count": learning,
            "dormant_unit_count": dormant,
            "habits": habits,
            "recent_decisions": [dict(row) for row in (self.state.get("decisions") or [])[-16:]],
        }


__all__ = ["DEFAULT_ACTIONS", "MOTOR_UNIT_COUNT", "NeuralMotorCortex"]
