"""Append-only boundary between independent workers and Ferro state.

Business workers emit small durable events. Only the state worker applies them
to ContentWakefulRuntime, so no business worker writes the canonical Ferro
state directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from typing import Any


class FerroStateEventBus:
    def __init__(self, state_root: Path) -> None:
        root = Path(state_root) / "application" / "state_events"
        self.pending_root = root / "pending"
        self.applied_root = root / "applied"
        self.pending_root.mkdir(parents=True, exist_ok=True)
        self.applied_root.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        event_id = "state-event:" + uuid4().hex
        row = {
            "schema_version": "ferro-state-event.v1",
            "event_id": event_id,
            "kind": str(kind).strip().upper(),
            "observed_at": now.isoformat(),
            "payload": dict(payload or {}),
        }
        path = self.pending_root / (event_id.replace(":", "_") + ".json")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return {
            "status": "QUEUED",
            "event_id": event_id,
            "kind": row["kind"],
            "observed_at": row["observed_at"],
        }

    def pending(self) -> tuple[tuple[Path, dict[str, Any]], ...]:
        rows: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.pending_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            rows.append((path, payload))
        return tuple(rows)

    def acknowledge(self, path: Path, result: dict[str, Any]) -> None:
        event = json.loads(path.read_text(encoding="utf-8"))
        receipt = {
            **event,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "result": dict(result or {}),
        }
        target = self.applied_root / path.name
        tmp = target.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
        path.unlink(missing_ok=True)

    def result(self, event_id: str) -> dict[str, Any] | None:
        name = str(event_id).replace(":", "_") + ".json"
        path = self.applied_root / name
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        value = row.get("result")
        return dict(value) if isinstance(value, dict) else {}

    def request(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        observed_at: datetime | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        receipt = self.emit(kind, payload, observed_at=observed_at)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while time.monotonic() < deadline:
            result = self.result(str(receipt["event_id"]))
            if result is not None:
                if str(result.get("status") or "").upper() == "ERROR":
                    raise RuntimeError(
                        str(result.get("error_type") or "StateEventError")
                        + ": "
                        + str(result.get("message") or "")
                    )
                return result
            time.sleep(0.02)
        raise TimeoutError(f"Ferro state request timed out: {kind}")


class FerroStateEventApplier:
    def __init__(self, wakeful) -> None:
        self.wakeful = wakeful

    @staticmethod
    def _dt(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("state event timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    def apply(self, event: dict[str, Any]) -> dict[str, Any]:
        kind = str(event.get("kind") or "").upper()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        observed_at = self._dt(event.get("observed_at"))

        if kind == "SET_RESOURCE_PRESSURE":
            self.wakeful.set_resource_pressure(str(payload.get("pressure") or "UNKNOWN"))
            return {"status": "APPLIED"}
        if kind == "X_MENTIONS":
            self.wakeful.emit_x_mentions(
                count=int(payload.get("count") or 0),
                observed_at=observed_at,
            )
            return {"status": "APPLIED"}
        if kind == "NETWORK_EVIDENCE":
            self.wakeful.emit_network_evidence(
                evidence_ref=str(payload.get("evidence_ref") or ""),
                observed_at=observed_at,
            )
            return {"status": "APPLIED"}
        if kind == "NETWORK_ERROR":
            self.wakeful.emit_network_error(
                code=str(payload.get("code") or "UNKNOWN"),
                observed_at=observed_at,
            )
            return {"status": "APPLIED"}
        if kind == "INTEROCEPTIVE_FAULT":
            return dict(self.wakeful.emit_interoceptive_fault(
                payload,
                observed_at=observed_at,
            ))
        if kind == "PREDICTION_OUTCOME":
            return dict(self.wakeful.apply_prediction_outcome_feedback(payload))
        if kind == "BRAIN_CORTEX_SETTLEMENT":
            return dict(self.wakeful.settle_brain_cortex(
                cycle=str(payload.get("cycle") or ""),
                utility=float(payload.get("utility") or 0.0),
                evidence_refs=tuple(payload.get("evidence_refs") or ()),
                observed_at=observed_at,
            ))
        if kind == "PREDICTION_DUE":
            self.wakeful.emit_prediction_due(
                prediction_ref=str(payload.get("prediction_ref") or ""),
                observed_at=observed_at,
            )
            return {"status": "APPLIED"}
        if kind == "ACTION_COMMITMENT":
            return dict(self.wakeful.emit_action_commitment(
                prediction_id=str(payload.get("prediction_id") or ""),
                observed_at=observed_at,
            ))
        if kind == "MARK_COGNITION":
            self.wakeful.mark_cognition(observed_at)
            return {"status": "APPLIED"}
        if kind == "RANK_NEURAL_TASTE":
            return dict(self.wakeful.rank_neural_taste(
                list(payload.get("topics") or ()),
                observed_at,
            ))
        if kind == "CHOOSE_BRAIN_OR_CORTEX":
            return dict(self.wakeful.choose_brain_or_cortex(
                cycle=str(payload.get("cycle") or ""),
                neural_choice=payload.get("neural_choice"),
                ego_choice=payload.get("ego_choice"),
                now=observed_at,
            ))
        if kind == "BEHAVIORAL_PROJECTION":
            return {
                "status": "OK",
                "projection": dict(self.wakeful.behavioral_projection()),
            }
        if kind == "PUBLIC_SNAPSHOT":
            return {
                "status": "OK",
                "snapshot": dict(self.wakeful.public_snapshot()),
            }
        if kind == "SOCIAL_OBSERVATION":
            return dict(self.wakeful.observe_social_interaction(
                actor=str(payload.get("actor") or ""),
                category=str(payload.get("category") or "SOCIAL"),
                signal=str(payload.get("signal") or "QUESTION"),
                now=observed_at,
            ))
        raise ValueError(f"unsupported Ferro state event: {kind}")


class FerroStateClient:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.bus = FerroStateEventBus(self.state_root)

    def rank_neural_taste(self, topics, now: datetime) -> dict[str, Any]:
        return self.bus.request(
            "RANK_NEURAL_TASTE",
            {"topics": list(topics or ())},
            observed_at=now,
        )

    def choose_brain_or_cortex(
        self,
        *,
        cycle: str,
        neural_choice: str | None,
        ego_choice: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        return self.bus.request(
            "CHOOSE_BRAIN_OR_CORTEX",
            {
                "cycle": cycle,
                "neural_choice": neural_choice,
                "ego_choice": ego_choice,
            },
            observed_at=now,
        )

    def behavioral_projection(self) -> dict[str, Any]:
        result = self.bus.request(
            "BEHAVIORAL_PROJECTION",
            {},
            observed_at=datetime.now(timezone.utc),
        )
        return dict(result.get("projection") or {})

    def public_snapshot(self) -> dict[str, Any]:
        result = self.bus.request(
            "PUBLIC_SNAPSHOT",
            {},
            observed_at=datetime.now(timezone.utc),
        )
        return dict(result.get("snapshot") or {})

    def observe_social_interaction(
        self,
        *,
        actor: str,
        category: str,
        signal: str,
        now: datetime,
    ) -> dict[str, Any]:
        return self.bus.request(
            "SOCIAL_OBSERVATION",
            {
                "actor": actor,
                "category": category,
                "signal": signal,
            },
            observed_at=now,
        )

    def relationship(self, raw_actor: str) -> dict[str, Any] | None:
        normalized = str(raw_actor or "").strip()
        actor = (
            "anonymous"
            if not normalized
            else "actor:" + sha256(normalized.encode("utf-8")).hexdigest()[:24]
        )
        path = self.state_root / "application" / "neural_social.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        relationships = row.get("relationships")
        if not isinstance(relationships, dict):
            return None
        value = relationships.get(actor)
        return dict(value) if isinstance(value, dict) else None


__all__ = ["FerroStateEventBus", "FerroStateEventApplier", "FerroStateClient"]
