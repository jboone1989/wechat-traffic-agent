"""Ferro's endogenous developmental agency.

N26-N28:
- detect persistent developmental needs from real outcomes without an LLM,
- convert neural pressure into bounded prospective development proposals,
- generate bounded Brain Lab worlds from those needs.

The module deliberately does not decide that every failure means "grow more".
It preserves evidence, severity, pressure, and reversibility so later evolution
stages can reject an intervention when reality does not support it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "ferro-developmental-agency.v1"
NEGATIVE_TRANSFER_THRESHOLD = -0.15
MOTOR_FAILURE_MIN = 4
MOTOR_FAILURE_MEAN_THRESHOLD = -0.30
SUSTAINED_FRUSTRATION_THRESHOLD = 0.62
MAX_EVIDENCE = 48
MAX_NEEDS = 64
MAX_PROPOSALS = 64
MAX_WORLDS = 32

NEURAL_CHANNELS = (
    "curiosity",
    "threat_risk",
    "social_attention",
    "task_urgency",
    "prediction_due",
    "resource_pressure",
    "opportunity",
    "error_recovery",
)
TARGET_CHANNELS = {
    "CHECK_X": ("social_attention", "curiosity", "opportunity"),
    "RESOURCE_REVIEW": ("resource_pressure", "error_recovery", "task_urgency"),
    "CONTENT_COGNITION": ("prediction_due", "curiosity", "task_urgency"),
    "OBSERVE_WORLD": ("opportunity", "curiosity", "threat_risk"),
    "REFLECT": ("curiosity", "prediction_due", "error_recovery"),
}


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


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


def _fingerprint(*parts: Any, length: int = 20) -> str:
    raw = "\0".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(part, (dict, list, tuple))
        else str(part)
        for part in parts
    ).encode()
    return sha256(raw).hexdigest()[:length]


class DevelopmentalAgency:
    """Evidence-bound developmental needs and non-LLM proposals."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "developmental_agency.json"
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        else:
            self.state = {
                "schema_version": SCHEMA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "need_seq": 0,
                "proposal_seq": 0,
                "world_seq": 0,
                "needs": {},
                "proposals": {},
                "worlds": {},
            }
            _write(self.path, self.state)

    @staticmethod
    def _channels_for_target(target_task: str | None) -> tuple[str, ...]:
        channels = TARGET_CHANNELS.get(str(target_task or ""), ("curiosity", "error_recovery"))
        return tuple(channel for channel in channels if channel in NEURAL_CHANNELS)

    @staticmethod
    def _task_for_developmental_domain(domain: str | None) -> str:
        raw = str(domain or "").strip()
        candidate = raw.rsplit(":", 1)[-1].upper() if raw else ""
        return candidate if candidate in TARGET_CHANNELS else "REFLECT"

    def _developmental_history_observations(
        self,
        *,
        developmental_history: Mapping[str, Any],
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        open_rows = [
            dict(row)
            for row in developmental_history.get("open_developmental_needs") or ()
            if isinstance(row, Mapping)
            and str(row.get("kind") or "") == "MASTERY_NEED"
            and str(row.get("transition_id") or "")
        ]
        domains = (
            developmental_history.get("domains")
            if isinstance(developmental_history.get("domains"), Mapping)
            else {}
        )
        observations: list[dict[str, Any]] = []
        open_transition_ids: set[str] = set()
        for row in open_rows:
            transition_id = str(row.get("transition_id") or "")
            open_transition_ids.add(transition_id)
            domain = str(row.get("domain") or "")
            stats = domains.get(domain) if isinstance(domains.get(domain), Mapping) else {}
            attempts = max(1, int(stats.get("attempts") or 0))
            failures = max(0, int(stats.get("failures") or 0))
            successes = max(0, int(stats.get("successes") or 0))
            competence = _clip(float(stats.get("competence") or 0.0))
            failure_ratio = _clip(failures / attempts)
            severity = _clip(max(1.0 - competence, failure_ratio))
            evidence_strength = _clip(failures / max(3.0, float(attempts)))
            target = self._task_for_developmental_domain(domain)
            observations.append({
                "key": f"mastery-transition:{transition_id}",
                "kind": "EVIDENCE_BOUND_MASTERY_NEED",
                "status": "ACTIVE",
                "capability": domain or target.lower(),
                "target_task": target,
                "source_environment": None,
                "source_transition_id": transition_id,
                "severity": severity,
                "evidence_strength": evidence_strength,
                "neural_pressure": _clip(
                    0.55 * severity + 0.45 * max(0.25, evidence_strength)
                ),
                "pressure_channels": list(self._channels_for_target(target)),
                "evidence": {
                    "kind": "DEVELOPMENTAL_MASTERY_NEED",
                    "observed_at": str(row.get("observed_at") or observed_at.isoformat()),
                    "transition_id": transition_id,
                    "trigger_event_id": row.get("trigger_event_id"),
                    "domain": domain,
                    "attempts": attempts,
                    "successes": successes,
                    "failures": failures,
                    "competence": round(competence, 6),
                },
            })

        # When the originating developmental transition disappears from the
        # open set, close the mirrored agency need as well. This keeps one
        # durable weakness lifecycle instead of two diverging ledgers.
        for prior in (self.state.get("needs") or {}).values():
            if not isinstance(prior, Mapping):
                continue
            key = str(prior.get("key") or "")
            if not key.startswith("mastery-transition:"):
                continue
            transition_id = str(prior.get("source_transition_id") or key.split(":", 1)[1])
            if transition_id in open_transition_ids or prior.get("status") == "RESOLVED":
                continue
            observations.append({
                "key": key,
                "kind": str(prior.get("kind") or "EVIDENCE_BOUND_MASTERY_NEED"),
                "status": "RESOLVED",
                "capability": prior.get("capability"),
                "target_task": prior.get("target_task") or "REFLECT",
                "source_environment": prior.get("source_environment"),
                "source_transition_id": transition_id,
                "severity": 0.0,
                "evidence_strength": float(prior.get("evidence_strength") or 0.0),
                "neural_pressure": 0.0,
                "pressure_channels": list(prior.get("pressure_channels") or ()),
                "evidence": {
                    "kind": "DEVELOPMENTAL_MASTERY_NEED_RESOLVED",
                    "transition_id": transition_id,
                    "resolution_source": "developmental_history",
                },
            })
        return observations

    def _transfer_evidence_refs(self, source: str, target: str) -> list[str]:
        raw = _read(self.state_root / "application" / "neural_transfer.json")
        refs = []
        for row in raw.get("experiments") or ():
            if (
                isinstance(row, Mapping)
                and row.get("status") == "SETTLED"
                and row.get("source_environment") == source
                and row.get("target_task") == target
            ):
                ref = str(row.get("experiment_id") or "")
                if ref:
                    refs.append(ref)
        return refs[-24:]

    def _negative_transfer_observations(
        self,
        *,
        transfer: Mapping[str, Any],
        neural_life: Mapping[str, Any],
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        observations = []
        affect = neural_life.get("affect") if isinstance(neural_life.get("affect"), Mapping) else {}
        drives = neural_life.get("drives") if isinstance(neural_life.get("drives"), Mapping) else {}
        frustration = _clip(float(affect.get("frustration") or 0.0))
        evolution = _clip(float(drives.get("evolution") or 0.0))
        for key, raw in (transfer.get("strata") or {}).items():
            if not isinstance(raw, Mapping) or not bool(raw.get("complete")):
                continue
            effect = raw.get("estimated_target_utility_effect")
            if effect is None:
                continue
            effect = float(effect)
            source = str(raw.get("source_environment") or key.split("->", 1)[0])
            target = str(raw.get("target_task") or (key.split("->", 1)[1] if "->" in str(key) else ""))
            arms = raw.get("arms") if isinstance(raw.get("arms"), Mapping) else {}
            treatment_n = int((arms.get("TREATMENT") or {}).get("count") or 0)
            control_n = int((arms.get("CONTROL") or {}).get("count") or 0)
            evidence_strength = _clip(min(treatment_n, control_n) / 3.0)
            if effect <= NEGATIVE_TRANSFER_THRESHOLD:
                severity = _clip(abs(effect))
                pressure = _clip(
                    0.50 * severity
                    + 0.20 * evidence_strength
                    + 0.18 * evolution
                    + 0.12 * frustration
                )
                observations.append({
                    "key": f"negative-transfer:{source}->{target}",
                    "kind": "NEGATIVE_TRANSFER",
                    "status": "ACTIVE",
                    "capability": target,
                    "target_task": target,
                    "source_environment": source,
                    "severity": severity,
                    "evidence_strength": evidence_strength,
                    "neural_pressure": pressure,
                    "pressure_channels": list(self._channels_for_target(target)),
                    "evidence": {
                        "kind": "STRATIFIED_TRANSFER_EFFECT",
                        "observed_at": observed_at.isoformat(),
                        "source_environment": source,
                        "target_task": target,
                        "effect": round(effect, 6),
                        "treatment_count": treatment_n,
                        "control_count": control_n,
                        "causal_design": transfer.get("causal_design"),
                        "experiment_refs": self._transfer_evidence_refs(source, target),
                    },
                })
            else:
                observations.append({
                    "key": f"negative-transfer:{source}->{target}",
                    "kind": "NEGATIVE_TRANSFER",
                    "status": "RESOLVED",
                    "capability": target,
                    "target_task": target,
                    "source_environment": source,
                    "severity": 0.0,
                    "evidence_strength": evidence_strength,
                    "neural_pressure": 0.0,
                    "pressure_channels": list(self._channels_for_target(target)),
                    "evidence": {
                        "kind": "TRANSFER_EFFECT_RECOVERED",
                        "observed_at": observed_at.isoformat(),
                        "source_environment": source,
                        "target_task": target,
                        "effect": round(effect, 6),
                        "treatment_count": treatment_n,
                        "control_count": control_n,
                        "experiment_refs": self._transfer_evidence_refs(source, target),
                    },
                })
        return observations

    def _affect_observations(
        self,
        *,
        neural_life: Mapping[str, Any],
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        history = [row for row in (neural_life.get("history") or ()) if isinstance(row, Mapping)][-12:]
        if len(history) < 8:
            return []
        frustration = [
            float((row.get("affect") or {}).get("frustration") or 0.0)
            for row in history
        ]
        confidence = [
            float((row.get("affect") or {}).get("confidence") or 0.0)
            for row in history
        ]
        mean_frustration = sum(frustration) / len(frustration)
        mean_confidence = sum(confidence) / len(confidence)
        if mean_frustration < SUSTAINED_FRUSTRATION_THRESHOLD or mean_confidence > 0.55:
            return [{
                "key": "sustained-affective-mismatch",
                "kind": "SUSTAINED_AFFECTIVE_MISMATCH",
                "status": "RESOLVED",
                "capability": "GENERAL_ADAPTATION",
                "target_task": "REFLECT",
                "severity": 0.0,
                "evidence_strength": _clip(len(history) / 12.0),
                "neural_pressure": 0.0,
                "pressure_channels": ["error_recovery", "curiosity", "prediction_due"],
                "evidence": {"kind": "AFFECT_PATTERN_RECOVERED", "observed_at": observed_at.isoformat(),
                             "mean_frustration": round(mean_frustration, 6), "mean_confidence": round(mean_confidence, 6),
                             "history_start": history[0].get("at"), "history_end": history[-1].get("at")},
            }]
        severity = _clip(mean_frustration * (1.0 - mean_confidence))
        return [{
            "key": "sustained-affective-mismatch",
            "kind": "SUSTAINED_AFFECTIVE_MISMATCH",
            "status": "ACTIVE",
            "capability": "GENERAL_ADAPTATION",
            "target_task": "REFLECT",
            "source_environment": None,
            "severity": severity,
            "evidence_strength": _clip(len(history) / 12.0),
            "neural_pressure": _clip(0.65 * severity + 0.35 * mean_frustration),
            "pressure_channels": ["error_recovery", "curiosity", "prediction_due"],
            "evidence": {
                "kind": "PERSISTENT_AFFECT_PATTERN",
                "observed_at": observed_at.isoformat(),
                "sample_count": len(history),
                "mean_frustration": round(mean_frustration, 6),
                "mean_confidence": round(mean_confidence, 6),
                "history_start": history[0].get("at"),
                "history_end": history[-1].get("at"),
            },
        }]

    def _motor_observations(
        self,
        *,
        neural_motor: Mapping[str, Any],
        observed_at: datetime,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in neural_motor.get("recent_decisions") or ():
            if not isinstance(row, Mapping) or str(row.get("status") or "") != "SETTLED":
                continue
            outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
            if outcome.get("reward") is None:
                continue
            task = str(row.get("task") or "")
            if task:
                grouped.setdefault(task, []).append(row)
        observations = []
        for task, rows in grouped.items():
            if len(rows) < MOTOR_FAILURE_MIN:
                continue
            rewards = [float((row.get("outcome") or {}).get("reward") or 0.0) for row in rows]
            mean_reward = sum(rewards) / len(rewards)
            negative = sum(value < 0 for value in rewards)
            if mean_reward > MOTOR_FAILURE_MEAN_THRESHOLD or negative < MOTOR_FAILURE_MIN:
                observations.append({
                    "key": f"motor-failure:{task}", "kind": "BEHAVIORAL_FAILURE_CLUSTER",
                    "status": "RESOLVED", "capability": task, "target_task": task,
                    "severity": 0.0, "evidence_strength": _clip(len(rows) / 8.0),
                    "neural_pressure": 0.0, "pressure_channels": list(self._channels_for_target(task)),
                    "evidence": {"kind": "MOTOR_PERFORMANCE_RECOVERED", "observed_at": observed_at.isoformat(),
                                 "sample_count": len(rows), "mean_reward": round(mean_reward, 6),
                                 "decision_refs": [str(row.get("decision_id") or "") for row in rows][-16:]},
                })
                continue
            severity = _clip(abs(mean_reward))
            refs = [str(row.get("decision_id") or "") for row in rows if row.get("decision_id")]
            observations.append({
                "key": f"motor-failure:{task}",
                "kind": "BEHAVIORAL_FAILURE_CLUSTER",
                "status": "ACTIVE",
                "capability": task,
                "target_task": task,
                "source_environment": None,
                "severity": severity,
                "evidence_strength": _clip(len(rows) / 8.0),
                "neural_pressure": _clip(0.60 * severity + 0.40 * (negative / len(rows))),
                "pressure_channels": list(self._channels_for_target(task)),
                "evidence": {
                    "kind": "SETTLED_MOTOR_FAILURE_CLUSTER",
                    "observed_at": observed_at.isoformat(),
                    "task": task,
                    "sample_count": len(rows),
                    "negative_count": negative,
                    "mean_reward": round(mean_reward, 6),
                    "decision_refs": refs[-16:],
                },
            })
        return observations

    @staticmethod
    def _need_id(key: str) -> str:
        return "development-need:" + _fingerprint(key)

    @staticmethod
    def _evidence_id(need_id: str, evidence: Mapping[str, Any]) -> str:
        # observed_at is deliberately excluded: polling the same factual state
        # must not manufacture additional evidence.
        material = {k: v for k, v in evidence.items() if k != "observed_at"}
        return "development-evidence:" + _fingerprint(need_id, material)

    def _apply_observation(self, observation: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
        needs = dict(self.state.get("needs") or {})
        need_id = self._need_id(str(observation["key"]))
        prior = dict(needs.get(need_id) or {})
        if not prior:
            self.state["need_seq"] = int(self.state.get("need_seq") or 0) + 1
            prior = {
                "need_id": need_id,
                "seq": int(self.state["need_seq"]),
                "key": observation["key"],
                "created_at": observed_at.isoformat(),
                "evidence": [],
                "evidence_count": 0,
            }
        evidence = dict(observation["evidence"])
        evidence["evidence_id"] = self._evidence_id(need_id, evidence)
        rows = [dict(row) for row in prior.get("evidence") or () if isinstance(row, Mapping)]
        if evidence["evidence_id"] not in {row.get("evidence_id") for row in rows}:
            rows.append(evidence)
        prior.update({
            "kind": observation["kind"],
            "status": observation["status"],
            "last_observed_at": observed_at.isoformat(),
            "capability": observation.get("capability"),
            "target_task": observation.get("target_task"),
            "source_environment": observation.get("source_environment"),
            "source_transition_id": observation.get("source_transition_id"),
            "severity": round(float(observation.get("severity") or 0.0), 6),
            "evidence_strength": round(float(observation.get("evidence_strength") or 0.0), 6),
            "neural_pressure": round(float(observation.get("neural_pressure") or 0.0), 6),
            "pressure_channels": list(observation.get("pressure_channels") or ()),
            "evidence": rows[-MAX_EVIDENCE:],
            "evidence_count": len(rows),
            "llm_used_for_detection": False,
        })
        needs[need_id] = prior
        self.state["needs"] = needs
        return prior

    def _proposal_for_need(self, need: Mapping[str, Any], observed_at: datetime) -> dict[str, Any] | None:
        if need.get("status") != "ACTIVE" or float(need.get("neural_pressure") or 0.0) < 0.25:
            return None
        if need.get("kind") == "NEGATIVE_TRANSFER":
            strategy = "ALTERNATIVE_TRAINING_WORLD"
            intervention = "SELF_GENERATED_WORLD_TRAINING"
        elif need.get("kind") == "BEHAVIORAL_FAILURE_CLUSTER":
            strategy = "MOTOR_CURRICULUM_SEARCH"
            intervention = "BOUNDED_SENSORIMOTOR_CURRICULUM"
        else:
            strategy = "DEVELOPMENTAL_EXPLORATION"
            intervention = "BOUNDED_DEVELOPMENT_EXPERIMENT"

        proposal_id = "development-proposal:" + _fingerprint(need["need_id"], strategy)
        proposals = dict(self.state.get("proposals") or {})
        prior = dict(proposals.get(proposal_id) or {})
        if not prior:
            self.state["proposal_seq"] = int(self.state.get("proposal_seq") or 0) + 1
            prior = {
                "proposal_id": proposal_id,
                "seq": int(self.state["proposal_seq"]),
                "created_at": observed_at.isoformat(),
                "status": "PROPOSED",
            }
        prior.update({
            "updated_at": observed_at.isoformat(),
            "need_id": need["need_id"],
            "strategy": strategy,
            "intervention": intervention,
            "authority": "NEURAL_DEVELOPMENT_PRESSURE",
            "neural_pressure": float(need.get("neural_pressure") or 0.0),
            "pressure_channels": list(need.get("pressure_channels") or ()),
            "target_task": need.get("target_task"),
            "avoid_source_environment": need.get("source_environment"),
            "experiment_contract": {
                "prospective_assignment_required": True,
                "treatment": intervention,
                "control": "NO_INTERVENTION",
                "target_task": need.get("target_task"),
                "minimum_treatment_outcomes": 4,
                "minimum_control_outcomes": 4,
                "adopt_only_if_world_outcome_improves": True,
                "rollback_required": True,
            },
            "world_requirements": {
                "bounded": True,
                "llm_in_motor_loop": False,
                "use_same_production_connectome": True,
                "sensor_channels": list(need.get("pressure_channels") or ()),
                "unsemantic_actions": True,
                "real_world_authority": False,
            },
            "llm_used_for_proposal": False,
        })
        proposals[proposal_id] = prior
        self.state["proposals"] = proposals
        return prior

    def _generate_world(self, proposal: Mapping[str, Any], observed_at: datetime) -> dict[str, Any] | None:
        if proposal.get("strategy") not in {
            "ALTERNATIVE_TRAINING_WORLD", "MOTOR_CURRICULUM_SEARCH", "DEVELOPMENTAL_EXPLORATION",
        }:
            return None
        worlds = dict(self.state.get("worlds") or {})
        existing = next(
            (
                dict(row) for row in worlds.values()
                if isinstance(row, Mapping)
                and row.get("proposal_id") == proposal.get("proposal_id")
                and row.get("status") not in {"REJECTED", "REVISE_REQUIRED", "RETIRED"}
            ),
            None,
        )
        if existing is not None:
            return existing

        self.state["world_seq"] = int(self.state.get("world_seq") or 0) + 1
        world_id = f"WORLD_{int(self.state['world_seq']):04d}"
        seed = _fingerprint(proposal["proposal_id"], world_id, length=32)
        sensors = [
            channel for channel in proposal.get("pressure_channels") or ()
            if channel in NEURAL_CHANNELS
        ]
        # Add deterministic contrast channels so the generated problem is not a
        # one-signal lookup table. These choices emerge from the proposal seed.
        start = int(seed[:8], 16) % len(NEURAL_CHANNELS)
        for offset in range(len(NEURAL_CHANNELS)):
            channel = NEURAL_CHANNELS[(start + offset) % len(NEURAL_CHANNELS)]
            if channel not in sensors:
                sensors.append(channel)
            if len(sensors) >= 4:
                break
        action_count = 3 + (int(seed[8:10], 16) % 2)
        actions = [f"A{i}" for i in range(action_count)]
        action_channels = {
            action: sensors[index % len(sensors)]
            for index, action in enumerate(actions)
        }
        latent_weights: dict[str, dict[str, float]] = {}
        for action in actions:
            weights = {}
            for channel in sensors:
                raw = int(_fingerprint(seed, action, channel, length=8), 16)
                weights[channel] = round(((raw % 2001) - 1000) / 1000.0, 4)
            latent_weights[action] = weights

        world = {
            "world_id": world_id,
            "status": "CANDIDATE",
            "created_at": observed_at.isoformat(),
            "proposal_id": proposal["proposal_id"],
            "need_id": proposal["need_id"],
            "target_task": proposal.get("target_task") or "REFLECT",
            "strategy": proposal.get("strategy"),
            "avoids_environment": proposal.get("avoid_source_environment"),
            "generation": "FERRO_BORN",
            "generator": "DEVELOPMENTAL_AGENCY_V1",
            "seed": seed,
            "sensor_channels": sensors,
            "actions": actions,
            "action_channels": action_channels,
            "latent_weights": latent_weights,
            "noise": round(0.04 + (int(seed[10:12], 16) % 10) / 100.0, 4),
            "context_switch_period": 4 + (int(seed[12:14], 16) % 9),
            "episode_horizon": 48 + (int(seed[14:16], 16) % 17),
            "reward": {"correct": 1.0, "incorrect": -0.35, "step_cost": -0.01},
            "trial_count": 0,
            "training_steps": 0,
            "reward_sum": 0.0,
            "changed_synapses": 0,
            "last_trial_at": None,
            "safety": {
                "brain_lab_only": True,
                "no_external_actions": True,
                "no_llm_motor_loop": True,
                "prospective_transfer_required_before_adoption": True,
            },
        }
        worlds[world_id] = world
        self.state["worlds"] = worlds
        return world

    def record_world_trial(
        self,
        *,
        world_id: str,
        result: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        worlds = dict(self.state.get("worlds") or {})
        row = dict(worlds.get(str(world_id).upper()) or {})
        if not row:
            raise KeyError(f"unknown generated world: {world_id}")
        row["status"] = "EXPERIMENTING"
        row["trial_count"] = int(row.get("trial_count") or 0) + 1
        row["training_steps"] = int(row.get("training_steps") or 0) + int(result.get("steps") or 0)
        row["reward_sum"] = round(
            float(row.get("reward_sum") or 0.0) + float(result.get("reward") or 0.0),
            6,
        )
        row["changed_synapses"] = (
            int(row.get("changed_synapses") or 0) + int(result.get("changed_synapses") or 0)
        )
        row["last_trial_at"] = observed_at.isoformat()
        projection = result.get("projection") if isinstance(result.get("projection"), Mapping) else {}
        generated = projection.get("generated_worlds") if isinstance(projection.get("generated_worlds"), Mapping) else {}
        metrics = generated.get(str(world_id).upper()) if isinstance(generated.get(str(world_id).upper()), Mapping) else {}
        row["latest_training_metrics"] = {
            "correct_rate": metrics.get("correct_rate"),
            "episodes": metrics.get("episodes"),
            "steps": metrics.get("steps"),
            "reward_sum": metrics.get("reward_sum"),
        }
        worlds[str(world_id).upper()] = row
        self.state["worlds"] = worlds
        self.state["updated_at"] = observed_at.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def apply_world_transfer_evaluation(
        self,
        *,
        world_id: str,
        evaluation: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        worlds = dict(self.state.get("worlds") or {})
        key = str(world_id).upper()
        row = dict(worlds.get(key) or {})
        if not row:
            raise KeyError(f"unknown generated world: {world_id}")
        decision = str(evaluation.get("decision") or "CONTINUE_EXPERIMENT")
        status = {
            "ADOPT": "ADOPTED",
            "REJECT": "REJECTED",
            "REVISE": "REVISE_REQUIRED",
        }.get(decision, "EXPERIMENTING")
        row["status"] = status
        row["transfer_evaluation"] = dict(evaluation)
        row["last_transfer_evaluation_at"] = observed_at.isoformat()
        worlds[key] = row
        self.state["worlds"] = worlds

        proposals = dict(self.state.get("proposals") or {})
        proposal_id = str(row.get("proposal_id") or "")
        proposal = dict(proposals.get(proposal_id) or {})
        if proposal:
            proposal["status"] = {
                "ADOPT": "ADOPTED",
                "REJECT": "REJECTED",
                "REVISE": "REVISION_REQUIRED",
            }.get(decision, "EXPERIMENTING")
            proposal["latest_world_evaluation"] = dict(evaluation)
            proposal["updated_at"] = observed_at.isoformat()
            proposals[proposal_id] = proposal
            self.state["proposals"] = proposals

        needs = dict(self.state.get("needs") or {})
        need_id = str(row.get("need_id") or "")
        need = dict(needs.get(need_id) or {})
        if need and decision == "ADOPT":
            need.update({
                "status": "RESOLVED",
                "resolved_at": observed_at.isoformat(),
                "resolution": "REAL_WORLD_TRANSFER_ADOPT",
                "resolution_world_id": key,
                "resolution_effect": evaluation.get("estimated_real_world_effect"),
            })
            needs[need_id] = need
            self.state["needs"] = needs

        self.state["updated_at"] = observed_at.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def next_candidate_world(self) -> dict[str, Any] | None:
        active_need_ids = {
            str(row.get("need_id"))
            for row in (self.state.get("needs") or {}).values()
            if isinstance(row, Mapping) and row.get("status") == "ACTIVE"
        }
        candidates = [
            dict(row)
            for row in (self.state.get("worlds") or {}).values()
            if isinstance(row, Mapping)
            and row.get("status") in {"CANDIDATE", "EXPERIMENTING"}
            and str(row.get("need_id")) in active_need_ids
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda row: (
                int(row.get("trial_count") or 0),
                int(str(row.get("world_id") or "WORLD_9999").split("_")[-1]),
            )
        )
        return candidates[0]

    def refresh(
        self,
        *,
        now: datetime | None,
        neural_life: Mapping[str, Any],
        neural_transfer: Mapping[str, Any],
        neural_motor: Mapping[str, Any],
        developmental_history: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        observations = []
        observations.extend(self._negative_transfer_observations(
            transfer=neural_transfer,
            neural_life=neural_life,
            observed_at=now,
        ))
        observations.extend(self._affect_observations(
            neural_life=neural_life,
            observed_at=now,
        ))
        observations.extend(self._motor_observations(
            neural_motor=neural_motor,
            observed_at=now,
        ))
        if isinstance(developmental_history, Mapping):
            observations.extend(self._developmental_history_observations(
                developmental_history=developmental_history,
                observed_at=now,
            ))

        touched = []
        for observation in observations:
            if observation.get("status") == "RESOLVED" and self._need_id(str(observation["key"])) not in (self.state.get("needs") or {}):
                continue
            touched.append(self._apply_observation(observation, now))

        for need in list((self.state.get("needs") or {}).values()):
            if not isinstance(need, Mapping):
                continue
            proposal = self._proposal_for_need(need, now)
            if proposal is not None:
                self._generate_world(proposal, now)

        # Keep persistent identity/history while bounding corrupt/unbounded growth.
        needs = list((self.state.get("needs") or {}).values())
        if len(needs) > MAX_NEEDS:
            keep = sorted(needs, key=lambda row: (row.get("status") != "ACTIVE", row.get("last_observed_at") or ""), reverse=True)[:MAX_NEEDS]
            self.state["needs"] = {str(row["need_id"]): row for row in keep}
        proposals = list((self.state.get("proposals") or {}).values())
        if len(proposals) > MAX_PROPOSALS:
            keep = sorted(proposals, key=lambda row: row.get("updated_at") or "", reverse=True)[:MAX_PROPOSALS]
            self.state["proposals"] = {str(row["proposal_id"]): row for row in keep}
        worlds = list((self.state.get("worlds") or {}).values())
        if len(worlds) > MAX_WORLDS:
            keep = sorted(worlds, key=lambda row: row.get("created_at") or "", reverse=True)[:MAX_WORLDS]
            self.state["worlds"] = {str(row["world_id"]): row for row in keep}

        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        needs = [dict(row) for row in (self.state.get("needs") or {}).values() if isinstance(row, Mapping)]
        proposals = [dict(row) for row in (self.state.get("proposals") or {}).values() if isinstance(row, Mapping)]
        worlds = [dict(row) for row in (self.state.get("worlds") or {}).values() if isinstance(row, Mapping)]
        needs.sort(key=lambda row: (row.get("status") != "ACTIVE", -float(row.get("neural_pressure") or 0.0), int(row.get("seq") or 0)))
        proposals.sort(key=lambda row: int(row.get("seq") or 0))
        worlds.sort(key=lambda row: int(str(row.get("world_id") or "0").split("_")[-1] or 0))
        active = [row for row in needs if row.get("status") == "ACTIVE"]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "active_need_count": len(active),
            "need_count": len(needs),
            "proposal_count": len(proposals),
            "ferro_born_world_count": len(worlds),
            "top_neural_pressure": None if not active else float(active[0].get("neural_pressure") or 0.0),
            "needs": needs[-16:] if not active else (active[:8] + [row for row in needs if row.get("status") != "ACTIVE"][-8:]),
            "proposals": proposals[-12:],
            "worlds": worlds[-12:],
            "authority": "EVIDENCE_PLUS_NEURAL_PRESSURE",
            "llm_used_for_need_detection": False,
            "llm_used_for_proposals": False,
        }


__all__ = ["DevelopmentalAgency", "SCHEMA", "TARGET_CHANNELS"]
