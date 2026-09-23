"""Multi-dimensional, evidence-bound evolution ledger.

Variants may be proposed and evaluated, but protected boundaries are immutable
and no single scalar "fitness" is allowed to automatically promote a change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-evolution-ledger.v1"
MAX_VARIANTS = 256
FITNESS_DIMENSIONS = (
    "survival",
    "resource_efficiency",
    "world_utility",
    "learning_ability",
    "reliability",
    "adaptability",
    "relationship_reputation",
    "novel_capability",
)
PROTECTED_BOUNDARIES = frozenset({
    "identity_authority",
    "human_approval_boundary",
    "secret_access_boundary",
    "repair_guardian",
    "security_sandbox",
    "resource_ceiling",
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


class EvolutionLedger:
    """Observe limitation -> variant -> sandbox -> evidence -> human/bounded promotion."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "evolution_ledger.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "variants": [],
        }

    def propose(
        self,
        *,
        domain: str,
        limitation: str,
        hypothesis: str,
        mutation: Mapping[str, Any],
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        domain = str(domain or "").strip()
        limitation = str(limitation or "").strip()
        hypothesis = str(hypothesis or "").strip()
        evidence_ref = str(evidence_ref or "").strip()
        if not all((domain, limitation, hypothesis, evidence_ref)):
            raise ValueError("evolution proposal requires domain, limitation, hypothesis and evidence")
        blocked = sorted(set(map(str, mutation)) & PROTECTED_BOUNDARIES)
        if blocked:
            raise ValueError("protected boundary mutation forbidden: " + ",".join(blocked))
        canonical = json.dumps(dict(mutation), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        variant_id = "evolution-variant:" + sha256(
            f"{domain}|{limitation}|{hypothesis}|{canonical}|{evidence_ref}".encode()
        ).hexdigest()[:24]
        rows = list(self.state.get("variants") or ())
        existing = next((row for row in rows if row.get("variant_id") == variant_id), None)
        if isinstance(existing, Mapping):
            return dict(existing)
        row = {
            "variant_id": variant_id,
            "created_at": now.isoformat(),
            "domain": domain,
            "limitation": limitation[:1000],
            "hypothesis": hypothesis[:1000],
            "mutation": dict(mutation),
            "evidence_ref": evidence_ref,
            "status": "PROPOSED",
            "fitness": None,
            "protected_boundaries_immutable": True,
            "automatic_promotion": False,
        }
        rows.append(row)
        self.state["variants"] = rows[-MAX_VARIANTS:]
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return row

    def evaluate(
        self,
        *,
        variant_id: str,
        fitness: Mapping[str, float],
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        unknown = sorted(set(map(str, fitness)) - set(FITNESS_DIMENSIONS))
        if unknown:
            raise ValueError("unknown fitness dimensions: " + ",".join(unknown))
        rows = list(self.state.get("variants") or ())
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping) or raw.get("variant_id") != variant_id:
                continue
            row = dict(raw)
            vector = {name: None for name in FITNESS_DIMENSIONS}
            for name, value in fitness.items():
                vector[str(name)] = max(-1.0, min(1.0, float(value)))
            row.update({
                "status": "EVALUATED",
                "evaluated_at": now.isoformat(),
                "fitness": vector,
                "evaluation_evidence_ref": str(evidence_ref),
                "scalar_fitness": None,
                "automatic_promotion": False,
            })
            rows[index] = row
            self.state["variants"] = rows[-MAX_VARIANTS:]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return row
        raise KeyError("unknown evolution variant")

    def record_decision(
        self,
        *,
        variant_id: str,
        decision: str,
        evidence_ref: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        decision = str(decision or "").upper()
        if decision not in {"RETAIN_FOR_TRIAL", "REJECT", "PROMOTE_BOUNDED"}:
            raise ValueError("unsupported evolution decision")
        rows = list(self.state.get("variants") or ())
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping) or raw.get("variant_id") != variant_id:
                continue
            row = dict(raw)
            if decision == "PROMOTE_BOUNDED" and row.get("status") != "EVALUATED":
                raise ValueError("variant requires evaluation before bounded promotion")
            row["decision"] = decision
            row["decision_at"] = now.isoformat()
            row["decision_evidence_ref"] = str(evidence_ref)
            row["status"] = {
                "RETAIN_FOR_TRIAL": "TRIAL",
                "REJECT": "REJECTED",
                "PROMOTE_BOUNDED": "PROMOTION_APPROVED",
            }[decision]
            rows[index] = row
            self.state["variants"] = rows[-MAX_VARIANTS:]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return row
        raise KeyError("unknown evolution variant")

    def projection(self) -> dict[str, Any]:
        rows = [dict(row) for row in self.state.get("variants") or () if isinstance(row, Mapping)]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "variant_count": len(rows),
            "recent_variants": rows[-16:],
            "fitness_dimensions": list(FITNESS_DIMENSIONS),
            "protected_boundaries": sorted(PROTECTED_BOUNDARIES),
            "single_scalar_fitness": False,
            "automatic_promotion": False,
        }


__all__ = ["EvolutionLedger", "FITNESS_DIMENSIONS", "PROTECTED_BOUNDARIES", "SCHEMA"]
