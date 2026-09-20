"""Prospective cross-domain transfer experiments for Ferro's nervous system.

A treatment window trains the same persistent connectome in one Brain Lab world;
a control window does not. Both arms then receive the same target-domain probe
and execute the same real runtime task. Actual task outcomes settle the trial.
The experiment is prospective and assignment is persisted before intervention.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "ferro-neural-transfer.v1"
MAX_EXPERIMENTS = 256
PAIRS = (
    ("MAZE", "OBSERVE_WORLD"),
    ("FORAGING", "RESOURCE_REVIEW"),
    ("PONG", "CONTENT_COGNITION"),
    ("ROVER", "CHECK_X"),
)

_TARGET_PROBES = {
    "OBSERVE_WORLD": {"curiosity": 0.85, "opportunity": 0.55, "prediction_due": 0.30},
    "RESOURCE_REVIEW": {"resource_pressure": 0.90, "error_recovery": 0.35, "task_urgency": 0.45},
    "CONTENT_COGNITION": {"prediction_due": 0.80, "curiosity": 0.65, "task_urgency": 0.55},
    "CHECK_X": {"social_attention": 0.90, "opportunity": 0.50, "curiosity": 0.25},
}
_TARGET_CHANNELS = {
    "OBSERVE_WORLD": ("curiosity", "opportunity"),
    "RESOURCE_REVIEW": ("resource_pressure", "error_recovery"),
    "CONTENT_COGNITION": ("prediction_due", "curiosity"),
    "CHECK_X": ("social_attention", "opportunity"),
}


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class CrossDomainTransferLab:
    def __init__(self, state_root: Path, *, substrate: Any | None, brain_lab: Any | None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.brain_lab = brain_lab
        self.path = self.state_root / "application" / "neural_transfer.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            return raw
        return {
            "schema_version": SCHEMA,
            "updated_at": None,
            "seq": 0,
            "experiments": [],
        }

    @staticmethod
    def _assignment(seq: int) -> str:
        # Assignment is fixed before intervention and independent of outcomes.
        bit = sha256(f"ferro-transfer-assignment:{seq}".encode()).digest()[0] & 1
        return "TREATMENT" if bit else "CONTROL"

    def _structure(self) -> dict[str, Any]:
        if self.substrate is None or not hasattr(self.substrate, "projection"):
            return {}
        projection = self.substrate.projection()
        development = projection.get("developmental_neurogenesis") if isinstance(projection, Mapping) else {}
        development = development if isinstance(development, Mapping) else {}
        return {
            "neuron_count": int(projection.get("neuron_count") or 0),
            "synthetic_neuron_count": int(development.get("synthetic_neuron_count") or 0),
            "stable_circuit_count": int(development.get("stable_circuit_count") or 0),
            "organ_count": int(development.get("organ_count") or 0),
            "species_stage": development.get("species_stage"),
        }

    def _probe(self, *, target_task: str, now: datetime, label: str) -> dict[str, Any]:
        if self.substrate is None:
            return {"status": "CONNECTOME_UNAVAILABLE", "score": None}
        currents = dict(_TARGET_PROBES[target_task])
        burst = dict(self.substrate.stimulate(
            currents,
            now,
            source_event_id=f"transfer-probe:{label}",
            feedback_eligible=False,
        ) or {})
        delta = burst.get("delta_readout_hz") if isinstance(burst.get("delta_readout_hz"), Mapping) else {}
        channels = _TARGET_CHANNELS[target_task]
        score = sum(max(0.0, float(delta.get(channel) or 0.0)) for channel in channels)
        return {
            "status": "PROBED",
            "score": round(score, 6),
            "channels": {channel: round(float(delta.get(channel) or 0.0), 6) for channel in channels},
            "spikes": int(burst.get("spikes") or 0),
            "active_neurons": int(burst.get("active_neurons") or 0),
        }

    def pending(self) -> dict[str, Any] | None:
        for row in reversed(self.state.get("experiments") or ()):
            if str(row.get("status") or "") == "AWAITING_TARGET_OUTCOME":
                return dict(row)
        return None

    def begin(self, *, now: datetime | None = None, training_steps: int = 12) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        existing = self.pending()
        if existing is not None:
            return {"status": "PENDING_EXISTS", "experiment": existing}
        if self.substrate is None or self.brain_lab is None:
            return {"status": "CONNECTOME_UNAVAILABLE", "experiment": None}

        seq = int(self.state.get("seq") or 0) + 1
        source_environment, target_task = PAIRS[(seq - 1) % len(PAIRS)]
        assignment = self._assignment(seq)
        experiment_id = f"transfer:{seq:06d}"
        row: dict[str, Any] = {
            "experiment_id": experiment_id,
            "seq": seq,
            "created_at": now.isoformat(),
            "assignment": assignment,
            "assignment_rule": "sha256_pre_intervention.v1",
            "source_environment": source_environment,
            "target_task": target_task,
            "status": "ASSIGNED",
            "training_steps": int(training_steps) if assignment == "TREATMENT" else 0,
            "pre_structure": self._structure(),
            "pre_probe": self._probe(target_task=target_task, now=now, label=experiment_id + ":pre"),
            "training": None,
            "post_probe": None,
            "target_outcome": None,
        }
        # Persist the assignment before any source-domain training occurs.
        experiments = list(self.state.get("experiments") or ())
        experiments.append(row)
        self.state.update({"seq": seq, "updated_at": now.isoformat(), "experiments": experiments[-MAX_EXPERIMENTS:]})
        _write(self.path, self.state)

        if assignment == "TREATMENT":
            row["training"] = self.brain_lab.run_session(
                environment=source_environment,
                steps=max(1, min(32, int(training_steps))),
                now=now,
            )
        row["post_probe"] = self._probe(target_task=target_task, now=now, label=experiment_id + ":post")
        row["post_structure"] = self._structure()
        pre_score = row["pre_probe"].get("score")
        post_score = row["post_probe"].get("score")
        row["probe_delta"] = (
            None if pre_score is None or post_score is None
            else round(float(post_score) - float(pre_score), 6)
        )
        row["status"] = "AWAITING_TARGET_OUTCOME"
        experiments[-1] = row
        self.state.update({"updated_at": now.isoformat(), "experiments": experiments[-MAX_EXPERIMENTS:]})
        _write(self.path, self.state)
        return {"status": "STARTED", "experiment": dict(row)}

    @staticmethod
    def _utility(result: Mapping[str, Any]) -> float:
        status = str(result.get("status") or "UNKNOWN").upper()
        if status in {"OK", "PUBLISHED", "SETTLED", "NO_NEW_OUTCOMES", "DREAMED", "TRAINED_ON_PRODUCTION_NERVOUS_SYSTEM"}:
            return 0.75
        if status in {"ERROR", "DEGRADED", "SHUTDOWN_CHECKPOINT"}:
            return -0.85
        if status in {"DISABLED", "NOOP", "ALREADY_PROCESSED"}:
            return -0.10
        return 0.15

    def settle_target(
        self,
        *,
        task: str,
        result: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        experiments = list(self.state.get("experiments") or ())
        index = next(
            (
                i for i in range(len(experiments) - 1, -1, -1)
                if str(experiments[i].get("status") or "") == "AWAITING_TARGET_OUTCOME"
                and str(experiments[i].get("target_task") or "") == str(task)
            ),
            None,
        )
        if index is None:
            return None
        row = dict(experiments[index])
        row["target_outcome"] = {
            "observed_at": observed_at.isoformat(),
            "status": str(result.get("status") or "UNKNOWN"),
            "utility": self._utility(result),
        }
        row["status"] = "SETTLED"
        experiments[index] = row
        self.state.update({"updated_at": observed_at.isoformat(), "experiments": experiments[-MAX_EXPERIMENTS:]})
        _write(self.path, self.state)
        return row

    def projection(self) -> dict[str, Any]:
        rows = [dict(row) for row in self.state.get("experiments") or ()]
        settled = [row for row in rows if str(row.get("status") or "") == "SETTLED"]
        arms: dict[str, dict[str, Any]] = {}
        for assignment in ("TREATMENT", "CONTROL"):
            subset = [row for row in settled if row.get("assignment") == assignment]
            utilities = [float((row.get("target_outcome") or {}).get("utility") or 0.0) for row in subset]
            deltas = [float(row["probe_delta"]) for row in subset if row.get("probe_delta") is not None]
            arms[assignment] = {
                "count": len(subset),
                "mean_target_utility": None if not utilities else round(sum(utilities) / len(utilities), 6),
                "mean_probe_delta": None if not deltas else round(sum(deltas) / len(deltas), 6),
            }
        treatment = arms["TREATMENT"]["mean_target_utility"]
        control = arms["CONTROL"]["mean_target_utility"]
        effect = None if treatment is None or control is None else round(float(treatment) - float(control), 6)
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "experiment_count": len(rows),
            "settled_count": len(settled),
            "pending": self.pending(),
            "arms": arms,
            "estimated_target_utility_effect": effect,
            "causal_claim_ready": bool(arms["TREATMENT"]["count"] >= 4 and arms["CONTROL"]["count"] >= 4),
            "recent": rows[-12:],
        }


__all__ = ["CrossDomainTransferLab", "PAIRS", "SCHEMA"]
