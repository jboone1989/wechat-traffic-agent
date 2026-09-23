"""Generic prospective world-learning ledger with delayed settlement.

This is deliberately domain-agnostic.  Domain collectors provide measured
outcomes; this ledger prevents action success from becoming its own reward and
keeps delayed/counterfactual evidence explicit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "ferro-world-learning.v1"
MAX_ACTIONS = 512
DEFAULT_WINDOWS = (("10m", timedelta(minutes=10)), ("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30)))


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


class WorldLearningLedger:
    """Prediction -> action -> delayed outcome -> learning signal."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "world_learning.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "actions": {},
        }

    def commit(
        self,
        *,
        action_kind: str,
        prediction: str,
        expected_utility: float,
        confidence: float,
        evidence_refs: Iterable[str],
        now: datetime | None = None,
        windows: Iterable[tuple[str, timedelta]] = DEFAULT_WINDOWS,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        if not refs:
            raise ValueError("world learning commitment requires evidence")
        action_kind = str(action_kind or "").strip().upper()
        prediction = str(prediction or "").strip()
        if not action_kind or not prediction:
            raise ValueError("world learning commitment requires action_kind and prediction")
        fingerprint = sha256(
            f"{action_kind}|{prediction}|{'|'.join(refs)}".encode()
        ).hexdigest()[:24]
        action_id = "world-action:" + fingerprint
        actions = dict(self.state.get("actions") or {})
        existing = actions.get(action_id)
        if isinstance(existing, Mapping):
            return dict(existing)
        settlements = {
            str(label): {
                "window": str(label),
                "due_at": (now + delta).isoformat(),
                "status": "PENDING",
                "observed_at": None,
                "actual_utility": None,
                "prediction_error": None,
                "counterfactual": None,
                "evidence_refs": [],
                "learning_signal": None,
            }
            for label, delta in windows
        }
        row = {
            "action_id": action_id,
            "committed_at": now.isoformat(),
            "action_kind": action_kind,
            "prediction": prediction,
            "expected_utility": max(-1.0, min(1.0, float(expected_utility))),
            "confidence": max(0.0, min(1.0, float(confidence))),
            "commitment_evidence_refs": list(refs),
            "metadata": dict(metadata or {}),
            "settlements": settlements,
            "status": "OPEN",
        }
        actions[action_id] = row
        self.state["actions"] = dict(list(actions.items())[-MAX_ACTIONS:])
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return dict(row)

    def find_by_metadata(self, key: str, value: object) -> dict[str, Any] | None:
        for row in (self.state.get("actions") or {}).values():
            if not isinstance(row, Mapping):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            if metadata.get(str(key)) == value:
                return dict(row)
        return None

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = []
        for action in (self.state.get("actions") or {}).values():
            if not isinstance(action, Mapping):
                continue
            for settlement in (action.get("settlements") or {}).values():
                if not isinstance(settlement, Mapping) or settlement.get("status") != "PENDING":
                    continue
                try:
                    due_at = datetime.fromisoformat(str(settlement.get("due_at")).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if due_at.tzinfo is not None and due_at <= now:
                    rows.append({
                        "action_id": action.get("action_id"),
                        "action_kind": action.get("action_kind"),
                        "window": settlement.get("window"),
                        "due_at": settlement.get("due_at"),
                    })
        rows.sort(key=lambda row: (str(row["due_at"]), str(row["action_id"]), str(row["window"])))
        return rows

    def settle(
        self,
        *,
        action_id: str,
        window: str,
        actual_utility: float,
        evidence_refs: Iterable[str],
        now: datetime | None = None,
        counterfactual: str | None = None,
        causal_factors: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        if not refs:
            raise ValueError("settlement requires world evidence")
        actions = dict(self.state.get("actions") or {})
        action = dict(actions.get(str(action_id)) or {})
        if not action:
            raise KeyError("unknown world action")
        settlements = dict(action.get("settlements") or {})
        current = dict(settlements.get(str(window)) or {})
        if not current:
            raise KeyError("unknown settlement window")
        if current.get("status") == "SETTLED":
            same_refs = set(current.get("evidence_refs") or ()) == set(refs)
            same_value = float(current.get("actual_utility") or 0.0) == float(actual_utility)
            if same_refs and same_value:
                return current
            raise ValueError("settlement already committed with different evidence")

        actual = max(-1.0, min(1.0, float(actual_utility)))
        expected = float(action.get("expected_utility") or 0.0)
        error = actual - expected
        confidence = float(action.get("confidence") or 0.0)
        learning_signal = max(-1.0, min(1.0, error * (0.35 + 0.65 * confidence)))
        factors = {
            str(k): max(-1.0, min(1.0, float(v)))
            for k, v in (causal_factors or {}).items()
        }
        current.update({
            "status": "SETTLED",
            "observed_at": now.isoformat(),
            "actual_utility": round(actual, 6),
            "prediction_error": round(error, 6),
            "counterfactual": str(counterfactual)[:1000] if counterfactual else None,
            "causal_factors": factors,
            "evidence_refs": list(refs),
            "learning_signal": round(learning_signal, 6),
        })
        settlements[str(window)] = current
        action["settlements"] = settlements
        action["status"] = (
            "SETTLED"
            if all(
                isinstance(row, Mapping) and row.get("status") == "SETTLED"
                for row in settlements.values()
            )
            else "PARTIALLY_SETTLED"
        )
        action["updated_at"] = now.isoformat()
        actions[str(action_id)] = action
        self.state["actions"] = actions
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return current

    def projection(self) -> dict[str, Any]:
        actions = [dict(row) for row in (self.state.get("actions") or {}).values() if isinstance(row, Mapping)]
        actions.sort(key=lambda row: str(row.get("committed_at") or ""), reverse=True)
        settled = 0
        pending = 0
        signals: list[float] = []
        for action in actions:
            for row in (action.get("settlements") or {}).values():
                if not isinstance(row, Mapping):
                    continue
                if row.get("status") == "SETTLED":
                    settled += 1
                    if isinstance(row.get("learning_signal"), (int, float)):
                        signals.append(float(row["learning_signal"]))
                elif row.get("status") == "PENDING":
                    pending += 1
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "action_count": len(actions),
            "pending_settlement_count": pending,
            "settled_window_count": settled,
            "mean_recent_learning_signal": (
                round(sum(signals[-32:]) / len(signals[-32:]), 6) if signals[-32:] else None
            ),
            "due": self.due(),
            "recent_actions": actions[:16],
            "function_success_is_reward": False,
            "delayed_credit_assignment": True,
        }


__all__ = ["DEFAULT_WINDOWS", "SCHEMA", "WorldLearningLedger"]
