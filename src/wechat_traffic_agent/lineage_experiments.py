"""Sandbox-only offspring specifications and lineage evidence.

This module cannot spawn processes, allocate hosts, copy secrets, or register a
network identity.  It only creates bounded experiment specifications with an
independent logical identity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-lineage-experiments.v1"
MAX_EXPERIMENTS = 128
FORBIDDEN_INHERITANCE = frozenset({
    "raw_memory",
    "credentials",
    "operator_tokens",
    "network_private_key",
    "repair_authority",
    "security_boundary",
})


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


class LineageExperimentLedger:
    """Create comparable offspring candidates without granting reproduction authority."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "lineage_experiments.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "experiments": [],
        }

    def propose(
        self,
        *,
        parent_organism_id: str,
        generation: int,
        inherited_traits: Mapping[str, float],
        inherited_capabilities: Mapping[str, float],
        neural_priors: Mapping[str, float],
        mutations: Mapping[str, float],
        purpose: str,
        now: datetime | None = None,
        resource_budget: Mapping[str, Any] | None = None,
        lifetime_hours: int = 24,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        parent = str(parent_organism_id or "").strip()
        purpose = str(purpose or "").strip()
        if not parent or not purpose:
            raise ValueError("offspring proposal requires parent identity and purpose")
        if int(generation) < 1:
            raise ValueError("generation must be >= 1")
        for collection in (inherited_traits, inherited_capabilities, neural_priors, mutations):
            if any(str(key) in FORBIDDEN_INHERITANCE for key in collection):
                raise ValueError("offspring proposal contains forbidden inheritance")
        mutation_values = {str(k): max(-0.25, min(0.25, float(v))) for k, v in mutations.items()}
        seed = sha256(
            f"{parent}|{generation}|{purpose}|{json.dumps(mutation_values, sort_keys=True)}".encode()
        ).hexdigest()
        offspring_id = "offspring:" + seed[:24]
        experiment_id = "lineage-experiment:" + seed[24:48]
        spec = {
            "schema_version": "ferro-offspring-spec.v1",
            "experiment_id": experiment_id,
            "parent_organism_id": parent,
            "offspring_organism_id": offspring_id,
            "generation": int(generation),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=max(1, min(168, int(lifetime_hours))))).isoformat(),
            "purpose": purpose[:500],
            "inherited_traits": {str(k): max(0.0, min(1.0, float(v))) for k, v in inherited_traits.items()},
            "inherited_capabilities": {str(k): max(0.0, min(1.0, float(v))) for k, v in inherited_capabilities.items()},
            "neural_priors": {str(k): max(0.0, min(1.0, float(v))) for k, v in neural_priors.items()},
            "mutations": mutation_values,
            "resource_budget": {
                "max_cpu_seconds": max(
                    0, min(86_400, int((resource_budget or {}).get("max_cpu_seconds", 600)))
                ),
                "max_model_tokens": max(
                    0, min(10_000_000, int((resource_budget or {}).get("max_model_tokens", 100000)))
                ),
                "max_external_writes": 0,
            },
            "status": "PROPOSED",
            "sandbox_only": True,
            "network_registration_allowed": False,
            "self_replication_allowed": False,
            "secret_inheritance_allowed": False,
            "promotion_is_evidence_only": True,
        }
        experiments = list(self.state.get("experiments") or ())
        existing = next((row for row in experiments if row.get("experiment_id") == experiment_id), None)
        if isinstance(existing, Mapping):
            return dict(existing)
        experiments.append(spec)
        self.state["experiments"] = experiments[-MAX_EXPERIMENTS:]
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return spec

    def record_result(
        self,
        *,
        experiment_id: str,
        metrics: Mapping[str, float],
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        experiments = list(self.state.get("experiments") or ())
        for index, raw in enumerate(experiments):
            if not isinstance(raw, Mapping) or raw.get("experiment_id") != experiment_id:
                continue
            row = dict(raw)
            if row.get("status") == "EVALUATED":
                return row
            row["status"] = "EVALUATED"
            row["evaluated_at"] = now.isoformat()
            row["metrics"] = {str(k): max(-1.0, min(1.0, float(v))) for k, v in metrics.items()}
            row["evidence_ref"] = str(evidence_ref)
            row["automatic_parent_replacement"] = False
            experiments[index] = row
            self.state["experiments"] = experiments[-MAX_EXPERIMENTS:]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return row
        raise KeyError("unknown lineage experiment")

    def projection(self) -> dict[str, Any]:
        rows = [dict(row) for row in self.state.get("experiments") or () if isinstance(row, Mapping)]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "experiment_count": len(rows),
            "recent_experiments": rows[-16:],
            "sandbox_only": True,
            "autonomous_replication": False,
        }


__all__ = ["FORBIDDEN_INHERITANCE", "LineageExperimentLedger", "SCHEMA"]
