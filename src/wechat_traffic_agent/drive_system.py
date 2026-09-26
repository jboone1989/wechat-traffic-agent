"""Unified drive competition for Ferro.

The drive engine is deterministic and model-free.  Existing neural state supplies
biases, while organism state supplies homeostatic pressure.  The winner proposes
bounded work; it never bypasses runtime admission or human/security boundaries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-drive-system.v1"
MAX_DECISIONS = 256


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
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
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _signal(organism: Mapping[str, Any], name: str, default: float = 0.0) -> float:
    signals = organism.get("signals") if isinstance(organism.get("signals"), Mapping) else {}
    row = signals.get(name) if isinstance(signals.get(name), Mapping) else {}
    value = row.get("value")
    return _clamp(float(value)) if isinstance(value, (int, float)) else _clamp(default)


class DriveEngine:
    """Persistent homeostatic drives plus auditable arbitration receipts."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "drive_system.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "drives": {},
            "last_decision": None,
            "decisions": [],
        }

    @staticmethod
    def _candidate(
        drive: str,
        task: str,
        *,
        reason: str,
        expected_utility: float,
        urgency: float,
        resource_cost: float,
        risk: float,
        long_term_benefit: float,
        identity_consistency: float,
    ) -> dict[str, Any]:
        score = (
            0.26 * _clamp(expected_utility)
            + 0.24 * _clamp(urgency)
            + 0.19 * _clamp(long_term_benefit)
            + 0.12 * _clamp(identity_consistency)
            - 0.11 * _clamp(resource_cost)
            - 0.08 * _clamp(risk)
        )
        return {
            "drive": drive,
            "task": task,
            "reason": reason,
            "expected_utility": round(_clamp(expected_utility), 6),
            "urgency": round(_clamp(urgency), 6),
            "resource_cost": round(_clamp(resource_cost), 6),
            "risk": round(_clamp(risk), 6),
            "long_term_benefit": round(_clamp(long_term_benefit), 6),
            "identity_consistency": round(_clamp(identity_consistency), 6),
            "arbitration_score": round(score, 6),
        }

    def refresh(
        self,
        *,
        now: datetime,
        organism: Mapping[str, Any],
        neural_life: Mapping[str, Any],
        personality: Mapping[str, Any],
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("drive refresh requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        summary = organism.get("summary") if isinstance(organism.get("summary"), Mapping) else {}
        affect = neural_life.get("affect") if isinstance(neural_life.get("affect"), Mapping) else {}
        legacy = neural_life.get("drives") if isinstance(neural_life.get("drives"), Mapping) else {}
        traits = personality.get("traits") if isinstance(personality.get("traits"), Mapping) else {}

        survival = _clamp(
            0.42 * (1.0 - float(summary.get("vitality") or 0.0))
            + 0.18 * _signal(organism, "threat")
            + 0.14 * (1.0 - _signal(organism, "service_health", 0.0))
            + 0.10 * (1.0 - _signal(organism, "network_health", 0.0))
            + 0.16 * float(legacy.get("survival") or 0.0)
        )
        resource = _clamp(
            0.36 * max(
                _signal(organism, "host_resource_pressure"),
                _signal(organism, "model_resource_pressure"),
                _signal(organism, "storage_pressure"),
                _signal(organism, "monetary_pressure"),
                _signal(organism, "token_pressure"),
            )
            + 0.20 * (1.0 - _signal(organism, "model_availability", 0.0))
            + 0.18 * float(affect.get("resource_hunger") or 0.0)
            + 0.16 * float(legacy.get("survival") or 0.0)
            + 0.10 * float(traits.get("caution") or 0.5)
        )
        curiosity = _clamp(
            0.38 * _signal(organism, "novelty_need")
            + 0.27 * float(affect.get("curiosity") or 0.0)
            + 0.20 * float(legacy.get("evolution") or 0.0)
            + 0.15 * float(traits.get("novelty_seeking") or 0.5)
        )
        mastery = _clamp(
            0.43 * float(affect.get("frustration") or 0.0)
            + 0.24 * (1.0 - float(affect.get("confidence") or 0.5))
            + 0.18 * float(traits.get("persistence") or 0.5)
            + 0.15 * float(legacy.get("evolution") or 0.0)
        )
        social = _clamp(
            0.42 * _signal(organism, "social_need")
            + 0.28 * float(legacy.get("reproduction") or 0.0)
            + 0.30 * float(traits.get("sociality") or 0.5)
        )
        creation = _clamp(
            0.26 * _signal(organism, "opportunity")
            + 0.18 * _signal(organism, "model_availability", 0.0)
            + 0.16 * _signal(organism, "energy", 0.0)
            + 0.16 * float(affect.get("confidence") or 0.5)
            + 0.12 * float(traits.get("playfulness") or 0.5)
            + 0.12 * float(traits.get("persistence") or 0.5)
        )
        rest = _clamp(
            0.45 * _signal(organism, "fatigue")
            + 0.25 * (1.0 - _signal(organism, "energy", 0.0))
            + 0.18 * _signal(organism, "workload_pressure")
            + 0.12 * (1.0 - float(affect.get("confidence") or 0.5))
        )
        propagation = _clamp(
            0.32 * float(legacy.get("reproduction") or 0.0)
            + 0.20 * _signal(organism, "opportunity")
            + 0.16 * _signal(organism, "network_health", 0.0)
            + 0.12 * _signal(organism, "service_health", 0.0)
            + 0.12 * float(traits.get("independence") or 0.5)
            + 0.08 * float(traits.get("sociality") or 0.5)
        )

        values = {
            "survival": survival,
            "resource": resource,
            "curiosity": curiosity,
            "mastery": mastery,
            "social": social,
            "creation": creation,
            "rest": rest,
            "propagation": propagation,
        }
        prior = self.state.get("drives") if isinstance(self.state.get("drives"), Mapping) else {}
        drives: dict[str, dict[str, Any]] = {}
        for name, intensity in values.items():
            old = prior.get(name) if isinstance(prior.get(name), Mapping) else {}
            prior_intensity = float(old.get("intensity") or 0.0)
            last_satisfied_at = old.get("last_satisfied_at")
            satisfaction_credit = float(old.get("outcome_satisfaction") or 0.0)
            if last_satisfied_at:
                try:
                    satisfied_at = datetime.fromisoformat(
                        str(last_satisfied_at).replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    age_seconds = max(0.0, (now - satisfied_at).total_seconds())
                    satisfaction_credit *= math.exp(-age_seconds / 1800.0)
                except ValueError:
                    satisfaction_credit = 0.0
            satisfaction_credit = _clamp(satisfaction_credit)
            frustration = _clamp(
                float(old.get("frustration") or 0.0) * 0.72
                + max(0.0, intensity - float(old.get("satisfaction") or 0.0)) * 0.28
                - 0.30 * satisfaction_credit
            )
            satisfaction = _clamp(max(1.0 - intensity, satisfaction_credit))
            habituation = _clamp(float(old.get("habituation") or 0.0) * 0.94 + 0.06 * prior_intensity)
            urgency = _clamp(
                intensity
                * (1.0 + frustration * 0.25)
                * (1.0 - 0.45 * satisfaction_credit)
            )
            drives[name] = {
                "intensity": round(intensity, 6),
                "baseline": round(0.5 * prior_intensity + 0.5 * intensity, 6),
                "urgency": round(urgency, 6),
                "frustration": round(frustration, 6),
                "satisfaction": round(satisfaction, 6),
                "habituation": round(habituation, 6),
                "last_satisfied_at": last_satisfied_at,
                "last_attempted_at": old.get("last_attempted_at"),
                "last_outcome_status": old.get("last_outcome_status"),
                "last_outcome_utility": old.get("last_outcome_utility"),
                "last_outcome_evidence_ref": old.get("last_outcome_evidence_ref"),
                "outcome_satisfaction": round(satisfaction_credit, 6),
                "no_effect_streak": int(old.get("no_effect_streak") or 0),
                "failure_streak": int(old.get("failure_streak") or 0),
                "cooldown_until": old.get("cooldown_until"),
                "evidence": [
                    "organism_state:" + str(organism.get("updated_at") or "unknown"),
                    "neural_life:" + str(neural_life.get("updated_at") or "unknown"),
                    "neural_personality:" + str(personality.get("updated_at") or "unknown"),
                ],
            }

        candidates = [
            self._candidate("survival", "RESOURCE_REVIEW", reason="restore threatened organism state", expected_utility=survival, urgency=float(drives["survival"]["urgency"]), resource_cost=0.12, risk=0.05, long_term_benefit=0.82, identity_consistency=0.95),
            self._candidate("resource", "RESOURCE_REVIEW", reason="reduce resource pressure or recover a viable route", expected_utility=resource, urgency=float(drives["resource"]["urgency"]), resource_cost=0.10, risk=0.04, long_term_benefit=0.78, identity_consistency=0.94),
            self._candidate("curiosity", "OBSERVE_WORLD", reason="resolve novelty or uncertainty with fresh world evidence", expected_utility=curiosity, urgency=float(drives["curiosity"]["urgency"]) * 0.72, resource_cost=0.20, risk=0.08, long_term_benefit=0.72, identity_consistency=0.80),
            self._candidate("mastery", "REFLECT", reason="turn repeated error/frustration into a bounded learning hypothesis", expected_utility=mastery, urgency=float(drives["mastery"]["urgency"]) * 0.70, resource_cost=0.24, risk=0.08, long_term_benefit=0.86, identity_consistency=0.88),
            self._candidate("social", "CHECK_X", reason="seek fresh social evidence rather than assume relationship state", expected_utility=social, urgency=float(drives["social"]["urgency"]) * 0.64, resource_cost=0.18, risk=0.10, long_term_benefit=0.62, identity_consistency=0.72),
            self._candidate("creation", "CONTENT_COGNITION", reason="convert available opportunity into a world-facing artifact", expected_utility=creation, urgency=float(drives["creation"]["urgency"]) * 0.62, resource_cost=0.42, risk=0.12, long_term_benefit=0.70, identity_consistency=0.82),
            self._candidate("rest", "REST", reason="reduce fatigue and workload before additional optional work", expected_utility=rest, urgency=float(drives["rest"]["urgency"]), resource_cost=0.0, risk=0.01, long_term_benefit=0.66, identity_consistency=0.92),
            self._candidate("propagation", "NEURAL_DEVELOPMENT", reason="run bounded development that may expand future capability", expected_utility=propagation, urgency=float(drives["propagation"]["urgency"]) * 0.45, resource_cost=0.48, risk=0.15, long_term_benefit=0.84, identity_consistency=0.78),
        ]

        resource_emergency = (
            str(summary.get("status") or "").upper() == "CRITICAL"
            or max(
                _signal(organism, "host_resource_pressure"),
                _signal(organism, "model_resource_pressure"),
                _signal(organism, "storage_pressure"),
                _signal(organism, "monetary_pressure"),
                _signal(organism, "token_pressure"),
            ) >= 0.90
        )
        for row in candidates:
            feedback = drives.get(str(row["drive"])) or {}
            cooldown_active = False
            cooldown_until = feedback.get("cooldown_until")
            if cooldown_until:
                try:
                    cooldown_at = datetime.fromisoformat(
                        str(cooldown_until).replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    cooldown_active = now < cooldown_at
                except ValueError:
                    cooldown_active = False
            penalty = 0.12 * float(feedback.get("outcome_satisfaction") or 0.0)
            if cooldown_active:
                penalty += min(
                    0.28,
                    0.14
                    + 0.04 * int(feedback.get("no_effect_streak") or 0)
                    + 0.06 * int(feedback.get("failure_streak") or 0),
                )
            if resource_emergency and row["drive"] in {"resource", "survival"}:
                penalty = min(penalty, 0.04)
            if penalty > 0.0:
                row["pre_feedback_arbitration_score"] = row["arbitration_score"]
                row["feedback_penalty"] = round(penalty, 6)
                row["arbitration_score"] = round(
                    float(row["arbitration_score"]) - penalty,
                    6,
                )

        unknown = int(summary.get("unknown_signal_count") or 0)
        if unknown:
            # Uncertainty cannot look like a cheap opportunity to create more work.
            for row in candidates:
                if row["drive"] not in {"survival", "resource", "rest"}:
                    row["arbitration_score"] = round(float(row["arbitration_score"]) - min(0.25, unknown * 0.03), 6)

        ranked = sorted(candidates, key=lambda row: (-float(row["arbitration_score"]), row["task"], row["drive"]))
        winner = dict(ranked[0])
        decision_id = "drive-decision:" + sha256(
            f"{now.isoformat()}|{winner['drive']}|{winner['task']}|{winner['arbitration_score']}".encode()
        ).hexdigest()[:20]
        decision = {
            "decision_id": decision_id,
            "observed_at": now.isoformat(),
            "winner": winner,
            "candidates": ranked,
            "organism_state_at": organism.get("updated_at"),
            "neural_life_at": neural_life.get("updated_at"),
            "personality_at": personality.get("updated_at"),
            "authority": "HOMEOSTATIC_DRIVE_ARBITRATION",
            "bypasses_admission": False,
        }
        decisions = list(self.state.get("decisions") or ())
        decisions.append(decision)
        self.state.update({
            "updated_at": now.isoformat(),
            "drives": drives,
            "last_decision": decision,
            "decisions": decisions[-MAX_DECISIONS:],
        })
        _write(self.path, self.state)
        return self.projection()

    def settle(
        self,
        *,
        drive: str,
        task: str,
        status: str,
        utility: float | None,
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Bind a real task outcome back to the drive that selected it."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        drive = str(drive or "").strip()
        if not drive:
            raise ValueError("drive is required")
        drives = self.state.get("drives")
        if not isinstance(drives, dict):
            drives = {}
            self.state["drives"] = drives
        row = dict(drives.get(drive) or {})
        status = str(status or "UNKNOWN").upper()
        numeric_utility = (
            None
            if utility is None
            else max(-1.0, min(1.0, float(utility)))
        )
        positive = numeric_utility is not None and numeric_utility > 0.05
        failed = (
            status in {"FAILED", "ERROR", "DEGRADED"}
            or (
                numeric_utility is not None
                and numeric_utility < -0.05
            )
        )

        failure_streak = int(row.get("failure_streak") or 0)
        no_effect_streak = int(row.get("no_effect_streak") or 0)
        if positive:
            failure_streak = 0
            no_effect_streak = 0
            row["last_satisfied_at"] = now.isoformat()
            row["outcome_satisfaction"] = round(
                max(
                    float(row.get("outcome_satisfaction") or 0.0),
                    min(1.0, max(0.25, numeric_utility)),
                ),
                6,
            )
            cooldown_minutes = 10
        elif failed:
            failure_streak += 1
            no_effect_streak = 0
            row["outcome_satisfaction"] = 0.0
            cooldown_minutes = min(
                60,
                5 * (2 ** min(failure_streak - 1, 4)),
            )
        else:
            failure_streak = max(0, failure_streak - 1)
            no_effect_streak += 1
            cooldown_minutes = min(
                60,
                5 * (2 ** min(no_effect_streak - 1, 4)),
            )

        row.update({
            "last_attempted_at": now.isoformat(),
            "last_outcome_status": status,
            "last_outcome_utility": numeric_utility,
            "last_outcome_evidence_ref": str(evidence_ref or "")[:300],
            "last_outcome_task": str(task or "")[:100],
            "failure_streak": failure_streak,
            "no_effect_streak": no_effect_streak,
            "cooldown_until": (
                now + timedelta(minutes=cooldown_minutes)
            ).isoformat(),
        })
        drives[drive] = row
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "drives": {k: dict(v) for k, v in (self.state.get("drives") or {}).items()},
            "last_decision": dict(self.state.get("last_decision") or {}),
            "decision_count": len(self.state.get("decisions") or ()),
            "model_generated_desire": False,
            "security_boundary_bypass": False,
        }


__all__ = ["DriveEngine", "SCHEMA"]
