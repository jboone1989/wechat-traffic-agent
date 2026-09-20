"""N34 bounded handoff from Python control to an emergent neural organ.

The first handoff target is deliberately low risk: choosing the next Brain Lab
training environment. The existing Python round-robin remains the baseline and
rollback path. A real emergent organ must first survive prospective non-inferior
A/B evidence before it gains authority. Even after handoff, Python audit trials
continue so regression can trigger automatic recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-neural-organ-handoff.v1"
FUNCTION_ID = "BRAIN_LAB_ENVIRONMENT_SELECTION"
MIN_PER_ARM = 4
NONINFERIOR_MARGIN = 0.03
ROLLBACK_MARGIN = 0.10
AUDIT_EVERY = 5
MAX_TRIALS = 256
DEFAULT_ENVIRONMENTS = ("PONG", "MAZE", "FORAGING", "ROVER")


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


class NeuralOrganHandoff:
    """Prospective, reversible authority handoff for one low-risk function."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "neural_organ_handoff.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "function_id": FUNCTION_ID,
            "phase": "EVALUATING",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "seq": 0,
            "candidate_organ_id": None,
            "selected_organ_id": None,
            "handoff_at": None,
            "rollback_at": None,
            "rollback_reason": None,
            "authority_decisions": 0,
            "trials": [],
            "associations": {},
        }

    @staticmethod
    def _development(substrate_projection: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = substrate_projection.get("developmental_neurogenesis")
        return raw if isinstance(raw, Mapping) else {}

    @classmethod
    def _organ_rows(cls, substrate_projection: Mapping[str, Any]) -> list[dict[str, Any]]:
        development = cls._development(substrate_projection)
        activity = development.get("last_organ_activity")
        activity = activity if isinstance(activity, Mapping) else {}
        rows = [
            dict(row)
            for row in activity.get("organs") or ()
            if isinstance(row, Mapping)
            and str(row.get("organ_id") or "")
        ]
        rows.sort(
            key=lambda row: (
                -float(row.get("activation") or 0.0),
                -int(row.get("member_count") or 0),
                str(row.get("organ_id") or ""),
            )
        )
        return rows

    @staticmethod
    def _baseline_environment(brain_lab: Mapping[str, Any]) -> str:
        environments = tuple(
            str(value).upper()
            for value in brain_lab.get("ancestral_environments") or DEFAULT_ENVIRONMENTS
            if value
        )
        if not environments:
            environments = DEFAULT_ENVIRONMENTS
        index = int(brain_lab.get("total_sessions") or 0) % len(environments)
        return environments[index]

    @staticmethod
    def _trial_assignment(seq: int, organ_id: str) -> str:
        block = (int(seq) - 1) // 2
        within = (int(seq) - 1) % 2
        first_neural = bool(
            sha256(f"organ-handoff:{organ_id}:{block}".encode()).digest()[0] & 1
        )
        neural = first_neural if within == 0 else not first_neural
        return "NEURAL_ORGAN" if neural else "PYTHON_BASELINE"

    def _association_rows(self, organ_id: str) -> dict[str, dict[str, Any]]:
        associations = self.state.get("associations")
        associations = associations if isinstance(associations, Mapping) else {}
        raw = associations.get(str(organ_id))
        raw = raw if isinstance(raw, Mapping) else {}
        return {
            str(environment): dict(value)
            for environment, value in raw.items()
            if isinstance(value, Mapping)
        }

    def _neural_environment(
        self,
        *,
        organ: Mapping[str, Any],
        environments: tuple[str, ...],
        decision_seq: int,
    ) -> tuple[str, dict[str, float]]:
        organ_id = str(organ["organ_id"])
        learned = self._association_rows(organ_id)
        active_members = tuple(
            sorted(str(value) for value in organ.get("active_members") or ())
        )
        # No semantic meaning is assigned to an organ or member neuron. Each
        # currently firing member contributes a deterministic random projection
        # into the existing Brain Lab action space. World reward then shapes the
        # downstream organ->environment association. If an organ is momentarily
        # silent, its identity supplies a weak fallback projection but cannot
        # manufacture an active firing pattern.
        projection_members = active_members or (f"silent:{organ_id}",)
        rows = []
        neural_scores: dict[str, float] = {}
        for environment in environments:
            raw = learned.get(environment) or {}
            trials = int(raw.get("trials") or 0)
            mean_reward = (
                0.0
                if trials <= 0
                else float(raw.get("reward_sum") or 0.0) / trials
            )
            votes = [
                int.from_bytes(
                    sha256(f"{member}|{environment}".encode()).digest()[:4],
                    "big",
                ) / 0xFFFFFFFF
                for member in projection_members
            ]
            pattern_score = sum(votes) / max(1, len(votes))
            activation = max(0.0, min(1.0, float(organ.get("activation") or 0.0)))
            expressed_score = pattern_score * (0.25 + 0.75 * activation)
            learned_bonus = 0.35 * mean_reward
            novelty_bonus = 0.18 if trials == 0 else 0.0
            tie_break = int.from_bytes(
                sha256(
                    f"{organ_id}|{environment}|{decision_seq}".encode()
                ).digest()[:2],
                "big",
            ) / 0xFFFF
            score = expressed_score + learned_bonus + novelty_bonus + 0.01 * tie_break
            neural_scores[environment] = round(score, 6)
            rows.append((score, -trials, environment))
        rows.sort(key=lambda row: (-row[0], row[1], row[2]))
        return rows[0][2], neural_scores

    def _evaluation_rows(self, *, phase: str = "EVALUATING") -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.state.get("trials") or ()
            if row.get("status") == "SETTLED" and row.get("phase") == phase
        ]

    @staticmethod
    def _arm_mean(rows: list[Mapping[str, Any]], arms: set[str]) -> tuple[int, float | None]:
        values = [
            float((row.get("outcome") or {}).get("utility") or 0.0)
            for row in rows
            if str(row.get("arm") or "") in arms
        ]
        return len(values), None if not values else round(sum(values) / len(values), 6)

    def _evaluate_initial_handoff(self, now: datetime) -> dict[str, Any]:
        rows = self._evaluation_rows(phase="EVALUATING")
        neural_count, neural_mean = self._arm_mean(rows, {"NEURAL_ORGAN"})
        python_count, python_mean = self._arm_mean(rows, {"PYTHON_BASELINE"})
        effect = (
            None
            if neural_mean is None or python_mean is None
            else round(neural_mean - python_mean, 6)
        )
        ready = neural_count >= MIN_PER_ARM and python_count >= MIN_PER_ARM
        status = "COLLECTING_EVIDENCE"
        if ready:
            if float(effect or 0.0) >= -NONINFERIOR_MARGIN:
                status = "HANDOFF"
                self.state["phase"] = "NEURAL_AUTHORITY"
                self.state["selected_organ_id"] = self.state.get("candidate_organ_id")
                self.state["handoff_at"] = now.isoformat()
                self.state["rollback_at"] = None
                self.state["rollback_reason"] = None
                self.state["authority_decisions"] = 0
            elif float(effect or 0.0) <= -ROLLBACK_MARGIN:
                status = "ROLLBACK"
                self.state["phase"] = "PYTHON_ROLLBACK"
                self.state["rollback_at"] = now.isoformat()
                self.state["rollback_reason"] = "NEURAL_ORGAN_INFERIOR_DURING_PROSPECTIVE_TRIAL"
            else:
                status = "CONTINUE_EVALUATION"
        return {
            "status": status,
            "ready": ready,
            "neural_count": neural_count,
            "python_count": python_count,
            "mean_neural_utility": neural_mean,
            "mean_python_utility": python_mean,
            "effect_neural_minus_python": effect,
            "noninferior_margin": NONINFERIOR_MARGIN,
            "rollback_margin": ROLLBACK_MARGIN,
        }

    def _evaluate_authority_audit(self, now: datetime) -> dict[str, Any]:
        rows = self._evaluation_rows(phase="NEURAL_AUTHORITY")
        neural_count, neural_mean = self._arm_mean(rows, {"NEURAL_ORGAN"})
        audit_count, audit_mean = self._arm_mean(rows, {"PYTHON_AUDIT"})
        effect = (
            None
            if neural_mean is None or audit_mean is None
            else round(neural_mean - audit_mean, 6)
        )
        ready = neural_count >= MIN_PER_ARM and audit_count >= MIN_PER_ARM
        status = "AUDITING"
        if ready and float(effect or 0.0) < -ROLLBACK_MARGIN:
            status = "ROLLED_BACK"
            self.state["phase"] = "PYTHON_ROLLBACK"
            self.state["rollback_at"] = now.isoformat()
            self.state["rollback_reason"] = "LONGITUDINAL_NEURAL_ORGAN_REGRESSION"
        elif ready:
            status = "NEURAL_AUTHORITY_HEALTHY"
        return {
            "status": status,
            "ready": ready,
            "neural_count": neural_count,
            "python_audit_count": audit_count,
            "mean_neural_utility": neural_mean,
            "mean_python_audit_utility": audit_mean,
            "effect_neural_minus_python": effect,
            "rollback_margin": ROLLBACK_MARGIN,
        }

    def choose(
        self,
        *,
        now: datetime | None,
        brain_lab: Mapping[str, Any],
        substrate_projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        baseline = self._baseline_environment(brain_lab)
        environments = tuple(
            str(value).upper()
            for value in brain_lab.get("ancestral_environments") or DEFAULT_ENVIRONMENTS
            if value
        ) or DEFAULT_ENVIRONMENTS
        organs = self._organ_rows(substrate_projection)
        phase = str(self.state.get("phase") or "EVALUATING")

        if phase == "PYTHON_ROLLBACK":
            return {
                "status": "PYTHON_ROLLBACK_ACTIVE",
                "function_id": FUNCTION_ID,
                "environment": baseline,
                "authority": "PYTHON_BASELINE",
                "trial": None,
                "external_action_authority": False,
            }

        locked_organ_id = (
            str(self.state.get("selected_organ_id") or "")
            if phase == "NEURAL_AUTHORITY"
            else str(self.state.get("candidate_organ_id") or "")
        )
        organ = next(
            (
                row for row in organs
                if str(row.get("organ_id") or "") == locked_organ_id
            ),
            None,
        )
        if organ is None and not locked_organ_id and organs:
            organ = organs[0]
            self.state["candidate_organ_id"] = str(organ["organ_id"])
            locked_organ_id = str(organ["organ_id"])
        if organ is None:
            return {
                "status": "NO_ELIGIBLE_EMERGENT_ORGAN",
                "function_id": FUNCTION_ID,
                "environment": baseline,
                "authority": "PYTHON_BASELINE",
                "trial": None,
                "external_action_authority": False,
            }

        seq = int(self.state.get("seq") or 0) + 1
        if phase == "NEURAL_AUTHORITY":
            authority_seq = int(self.state.get("authority_decisions") or 0) + 1
            self.state["authority_decisions"] = authority_seq
            arm = "PYTHON_AUDIT" if authority_seq % AUDIT_EVERY == 0 else "NEURAL_ORGAN"
        else:
            arm = self._trial_assignment(seq, str(organ["organ_id"]))

        if arm in {"PYTHON_BASELINE", "PYTHON_AUDIT"}:
            environment = baseline
            authority = arm
        else:
            environment, neural_scores = self._neural_environment(
                organ=organ,
                environments=environments,
                decision_seq=seq,
            )
            authority = "NEURAL_ORGAN"

        if arm in {"PYTHON_BASELINE", "PYTHON_AUDIT"}:
            neural_scores = {}

        decision_id = f"organ-handoff:{seq:06d}"
        trial = {
            "decision_id": decision_id,
            "seq": seq,
            "phase": phase,
            "function_id": FUNCTION_ID,
            "organ_id": str(organ["organ_id"]),
            "organ_activation": float(organ.get("activation") or 0.0),
            "active_member_count": int(organ.get("active_member_count") or 0),
            "active_members": list(organ.get("active_members") or ())[:32],
            "neural_environment_scores": neural_scores,
            "organ_source_event_id": (
                self._development(substrate_projection)
                .get("last_organ_activity", {})
                .get("source_event_id")
                if isinstance(
                    self._development(substrate_projection).get("last_organ_activity"),
                    Mapping,
                )
                else None
            ),
            "arm": arm,
            "environment": environment,
            "status": "ASSIGNED",
            "assigned_at": now.isoformat(),
            "outcome": None,
        }
        trials = list(self.state.get("trials") or ())
        trials.append(trial)
        self.state.update({
            "seq": seq,
            "updated_at": now.isoformat(),
            "trials": trials[-MAX_TRIALS:],
        })
        # Persist before Brain Lab receives the environment selection.
        _write(self.path, self.state)
        return {
            "status": "ASSIGNED",
            "function_id": FUNCTION_ID,
            "environment": environment,
            "authority": authority,
            "organ_id": str(organ["organ_id"]),
            "trial": dict(trial),
            "external_action_authority": False,
        }

    def settle(
        self,
        *,
        decision_id: str,
        result: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        trials = list(self.state.get("trials") or ())
        index = next(
            (
                i for i in range(len(trials) - 1, -1, -1)
                if trials[i].get("decision_id") == decision_id
                and trials[i].get("status") == "ASSIGNED"
            ),
            None,
        )
        if index is None:
            return None
        row = dict(trials[index])
        steps = max(1, int(result.get("steps") or 1))
        reward = float(result.get("reward") or 0.0)
        utility = _clip(reward / steps)
        row["status"] = "SETTLED"
        row["outcome"] = {
            "observed_at": observed_at.isoformat(),
            "status": str(result.get("status") or "UNKNOWN"),
            "reward": round(reward, 6),
            "steps": steps,
            "utility": round(utility, 6),
        }
        trials[index] = row

        if row.get("arm") == "NEURAL_ORGAN":
            associations = dict(self.state.get("associations") or {})
            organ_rows = dict(associations.get(str(row["organ_id"])) or {})
            env = str(row["environment"])
            assoc = dict(organ_rows.get(env) or {})
            assoc["trials"] = int(assoc.get("trials") or 0) + 1
            assoc["reward_sum"] = round(
                float(assoc.get("reward_sum") or 0.0) + utility,
                6,
            )
            assoc["mean_reward"] = round(
                float(assoc["reward_sum"]) / int(assoc["trials"]),
                6,
            )
            assoc["last_outcome_at"] = observed_at.isoformat()
            organ_rows[env] = assoc
            associations[str(row["organ_id"])] = organ_rows
            self.state["associations"] = associations

        self.state.update({
            "updated_at": observed_at.isoformat(),
            "trials": trials[-MAX_TRIALS:],
        })
        if row.get("phase") == "EVALUATING":
            evaluation = self._evaluate_initial_handoff(observed_at)
        else:
            evaluation = self._evaluate_authority_audit(observed_at)
        _write(self.path, self.state)
        return {**row, "evaluation": evaluation}

    def projection(self) -> dict[str, Any]:
        phase = str(self.state.get("phase") or "EVALUATING")
        initial = self._evaluate_initial_handoff(
            datetime.now(timezone.utc)
        ) if phase == "EVALUATING" else None
        audit = self._evaluate_authority_audit(
            datetime.now(timezone.utc)
        ) if phase == "NEURAL_AUTHORITY" else None
        return {
            "schema_version": SCHEMA,
            "function_id": FUNCTION_ID,
            "phase": self.state.get("phase"),
            "candidate_organ_id": self.state.get("candidate_organ_id"),
            "selected_organ_id": self.state.get("selected_organ_id"),
            "handoff_at": self.state.get("handoff_at"),
            "rollback_at": self.state.get("rollback_at"),
            "rollback_reason": self.state.get("rollback_reason"),
            "trial_count": len(self.state.get("trials") or ()),
            "initial_evaluation": initial,
            "longitudinal_audit": audit,
            "recent_trials": [
                dict(row) for row in (self.state.get("trials") or ())[-16:]
            ],
            "associations": dict(self.state.get("associations") or {}),
            "python_baseline_preserved": True,
            "automatic_rollback": True,
            "external_action_authority": False,
            "llm_used_for_handoff": False,
        }


__all__ = [
    "AUDIT_EVERY",
    "FUNCTION_ID",
    "MIN_PER_ARM",
    "NONINFERIOR_MARGIN",
    "NeuralOrganHandoff",
    "ROLLBACK_MARGIN",
    "SCHEMA",
]
