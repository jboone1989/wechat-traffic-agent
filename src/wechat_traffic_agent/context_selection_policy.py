"""Bounded Context Builder selection policy owned by AWE hillclimb."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-context-selection-policy.v1"
AUTHORITY = "awe-hillclimb"
DEFAULT = {
    "relevance_floor": 0.60,
    "coverage_floor_min": 0.78,
    "coverage_ratio": 0.68,
    "multi_need_bonus": 0.16,
    "size_tax": 0.12,
}
BOUNDS = {
    "relevance_floor": (0.35, 1.00),
    "coverage_floor_min": (0.55, 0.95),
    "coverage_ratio": (0.45, 0.90),
    "multi_need_bonus": (0.00, 0.35),
    "size_tax": (0.00, 0.30),
}
STEPS = {
    "relevance_floor": 0.08,
    "coverage_floor_min": 0.05,
    "coverage_ratio": 0.05,
    "multi_need_bonus": 0.04,
    "size_tax": 0.04,
}


def validate_policy(value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != set(DEFAULT):
        raise ValueError("context selection policy must contain exactly the bounded policy keys")
    out: dict[str, float] = {}
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("context selection policy values must be finite numbers")
        number = float(raw)
        lo, hi = BOUNDS[key]
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError(f"context selection policy value outside bounds: {key}")
        out[key] = round(number, 6)
    return out


def policy_ref(value: Mapping[str, Any]) -> str:
    validated = validate_policy(value)
    digest = hashlib.sha256(
        json.dumps(validated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return "context-builder.selection-policy:" + digest


def active_policy_path(state_root: Path | str) -> Path:
    return Path(state_root) / "application" / "context_builder" / "active_policy.json"


def load_policy(state_root: Path | str | None) -> dict[str, float]:
    if state_root is None:
        return dict(DEFAULT)
    path = active_policy_path(state_root)
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        if (
            row.get("schema") != SCHEMA
            or row.get("authority") != AUTHORITY
            or row.get("status") not in {"PROMOTED", "ROLLED_BACK"}
        ):
            return dict(DEFAULT)
        return validate_policy(row["parameters"])
    except (FileNotFoundError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return dict(DEFAULT)


def challenger_policies(baseline: Mapping[str, Any]) -> list[dict[str, float]]:
    base = validate_policy(baseline)
    rows: list[dict[str, float]] = []
    seen: set[str] = set()
    for key in DEFAULT:
        lo, hi = BOUNDS[key]
        step = STEPS[key]
        for value in (max(lo, base[key] - step), min(hi, base[key] + step)):
            if value == base[key]:
                continue
            candidate = dict(base)
            candidate[key] = round(value, 6)
            ref = policy_ref(candidate)
            if ref in seen:
                continue
            seen.add(ref)
            rows.append(candidate)
    return rows


def changed_parameter(
    baseline: Mapping[str, Any],
    challenger: Mapping[str, Any],
) -> str:
    left = validate_policy(baseline)
    right = validate_policy(challenger)
    changed = [key for key in DEFAULT if left[key] != right[key]]
    if len(changed) != 1:
        raise ValueError("context hillclimb challenger must change exactly one parameter")
    return changed[0]


def materialize_policy(
    state_root: Path | str,
    *,
    parameters: Mapping[str, Any],
    experiment_id: str,
    decision: str,
    previous_ref: str,
) -> dict[str, Any]:
    validated = validate_policy(parameters)
    row = {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "status": "PROMOTED" if decision == "PROMOTE" else "ROLLED_BACK",
        "parameters": validated,
        "policy_ref": policy_ref(validated),
        "experiment_id": str(experiment_id),
        "previous_ref": str(previous_ref),
    }
    path = active_policy_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return row


__all__ = [
    "AUTHORITY",
    "BOUNDS",
    "DEFAULT",
    "SCHEMA",
    "active_policy_path",
    "challenger_policies",
    "changed_parameter",
    "load_policy",
    "materialize_policy",
    "policy_ref",
    "validate_policy",
]
