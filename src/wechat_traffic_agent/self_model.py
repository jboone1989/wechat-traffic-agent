"""Evidence-bound, falsifiable self model for Ferro."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "ferro-self-model.v1"
MAX_BELIEFS = 128


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


class SelfModel:
    """Three-layer self model: core identity, stable traits, current state."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "self_model.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "core_identity": {},
            "stable_traits": {},
            "current_state": {},
            "capability_beliefs": {},
        }

    def observe_capability(
        self,
        *,
        capability: str,
        success: bool,
        evidence_ref: str,
        now: datetime | None = None,
        weight: float = 1.0,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        capability = str(capability or "").strip().lower()
        evidence_ref = str(evidence_ref or "").strip()
        if not capability or not evidence_ref:
            raise ValueError("capability belief requires capability and evidence")
        beliefs = dict(self.state.get("capability_beliefs") or {})
        row = dict(beliefs.get(capability) or {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "confidence": 0.5,
            "supporting_evidence": [],
            "counter_evidence": [],
            "last_verified": None,
        })
        digest = sha256(f"{capability}|{success}|{evidence_ref}".encode()).hexdigest()[:20]
        seen = set(row.get("evidence_digests") or ())
        if digest in seen:
            return row
        seen.add(digest)
        row["attempts"] = int(row.get("attempts") or 0) + 1
        if success:
            row["successes"] = int(row.get("successes") or 0) + 1
            support = list(row.get("supporting_evidence") or ())
            support.append(evidence_ref)
            row["supporting_evidence"] = support[-24:]
        else:
            row["failures"] = int(row.get("failures") or 0) + 1
            counter = list(row.get("counter_evidence") or ())
            counter.append(evidence_ref)
            row["counter_evidence"] = counter[-24:]
        attempts = max(1, int(row["attempts"]))
        empirical = (int(row.get("successes") or 0) + 1.0) / (attempts + 2.0)
        prior = float(row.get("confidence") or 0.5)
        alpha = min(0.45, 0.12 * max(0.25, float(weight)))
        row["confidence"] = round(max(0.0, min(1.0, prior + (empirical - prior) * alpha)), 6)
        row["last_verified"] = now.isoformat()
        row["evidence_digests"] = sorted(seen)[-64:]
        beliefs[capability] = row
        self.state["capability_beliefs"] = dict(list(sorted(beliefs.items()))[:MAX_BELIEFS])
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return row

    def refresh(
        self,
        *,
        now: datetime,
        organism: Mapping[str, Any],
        personality: Mapping[str, Any],
        development: Mapping[str, Any],
        autonomous_life: Mapping[str, Any],
        cortex: Mapping[str, Any],
        continuity: Mapping[str, Any],
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self model refresh requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        organism_id = str(cortex.get("organism_id") or continuity.get("organism_id") or "").strip()
        if organism_id:
            prior_id = str((self.state.get("core_identity") or {}).get("organism_id") or "")
            if prior_id and prior_id != organism_id:
                raise ValueError("self-model organism identity mismatch")

        network_identity = _read(self.state_root / "application" / "network" / "identity.json")
        self.state["core_identity"] = {
            "organism_id": organism_id or None,
            "agent_id": network_identity.get("agent_id"),
            "identity_basis": "durable runtime/cortex/network evidence",
            "prompt_defined": False,
        }
        self.state["stable_traits"] = {
            "personality": dict(personality.get("traits") or {}),
            "developmental_domains": {
                name: {
                    "competence": row.get("competence"),
                    "stability": row.get("stability"),
                    "attempts": row.get("attempts"),
                }
                for name, row in (development.get("domains") or {}).items()
                if isinstance(row, Mapping)
            },
            "milestone_count": len(development.get("milestones") or {}),
        }
        summary = organism.get("summary") if isinstance(organism.get("summary"), Mapping) else {}
        self.state["current_state"] = {
            "observed_at": now.isoformat(),
            "vitality": summary.get("vitality"),
            "uncertainty": summary.get("uncertainty"),
            "resource_status": summary.get("status"),
            "active_autonomous_goals": int(autonomous_life.get("active_goal_count") or 0),
        }

        # Developmental evidence becomes falsifiable capability belief, never a
        # textual claim imported from a prompt.
        beliefs = dict(self.state.get("capability_beliefs") or {})
        for name, row in (development.get("domains") or {}).items():
            if not isinstance(row, Mapping):
                continue
            attempts = int(row.get("attempts") or 0)
            if attempts <= 0:
                continue
            beliefs[str(name)] = {
                "attempts": attempts,
                "successes": int(row.get("successes") or 0),
                "failures": int(row.get("failures") or 0),
                "confidence": row.get("competence"),
                "supporting_evidence": list((beliefs.get(str(name)) or {}).get("supporting_evidence") or ()),
                "counter_evidence": list((beliefs.get(str(name)) or {}).get("counter_evidence") or ()),
                "last_verified": row.get("last_event_at"),
                "source": "developmental_history",
            }
        self.state["capability_beliefs"] = dict(list(sorted(beliefs.items()))[:MAX_BELIEFS])
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        beliefs = {}
        for name, row in (self.state.get("capability_beliefs") or {}).items():
            if not isinstance(row, Mapping):
                continue
            beliefs[name] = {
                "attempts": int(row.get("attempts") or 0),
                "successes": int(row.get("successes") or 0),
                "failures": int(row.get("failures") or 0),
                "confidence": row.get("confidence"),
                "last_verified": row.get("last_verified"),
                "supporting_evidence_count": len(row.get("supporting_evidence") or ()),
                "counter_evidence_count": len(row.get("counter_evidence") or ()),
                "source": row.get("source") or "direct_evidence",
            }
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "core_identity": dict(self.state.get("core_identity") or {}),
            "stable_traits": dict(self.state.get("stable_traits") or {}),
            "current_state": dict(self.state.get("current_state") or {}),
            "capability_beliefs": beliefs,
            "falsifiable": True,
            "prompt_claims_are_evidence": False,
        }


__all__ = ["SCHEMA", "SelfModel"]
