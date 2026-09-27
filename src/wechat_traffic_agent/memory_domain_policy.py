"""Bounded, derived recall policies; canonical facts and authority are never mutated."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


DOMAIN_SPECS = {
    "DECAY_PARAMETER_CHANGE": ("decay", "memory.decay.policy"),
    "INDEX_CONFIGURATION_CHANGE": ("index", "memory.index.configuration"),
    "SALIENCE_WEIGHT_CHANGE": ("salience", "memory.salience.policy"),
}
DEFAULTS = {
    "decay": {"age_multiplier": 1.0},
    "index": {"budget_scale": 1.0},
    "salience": {"boost_gain": 1.0},
}
BOUNDS = {"age_multiplier": (0.5, 2.0), "budget_scale": (0.25, 2.0), "boost_gain": (0.0, 2.0)}


def validate_policy(domain: str, value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != set(DEFAULTS[domain]):
        raise ValueError("domain policy may only change its bounded parameter")
    result = {}
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (float, int)):
            raise ValueError("domain policy parameters must be finite numbers")
        number = float(raw)
        lo, hi = BOUNDS[key]
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError("domain policy parameter outside protected bounds")
        result[key] = number
    return result


def policy_ref(domain: str, value: Mapping[str, Any]) -> str:
    validated = validate_policy(domain, value)
    digest = hashlib.sha256(json.dumps(validated, sort_keys=True).encode()).hexdigest()[:24]
    capability = next(cap for name, cap in DOMAIN_SPECS.values() if name == domain)
    return capability + ":" + digest


def load_policies(state_root: Path) -> dict[str, dict[str, float]]:
    policies = {name: dict(value) for name, value in DEFAULTS.items()}
    for domain in policies:
        path = Path(state_root) / "application" / "memory_system" / f"active_{domain}_policy.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if (row.get("authority") != "agent-runtime-evolution"
                    or row.get("schema") != "memory-domain-policy.v1"
                    or row.get("status") not in {"PROMOTED", "ROLLED_BACK"}
                    or not row.get("capability_version_id")):
                continue
            policies[domain] = validate_policy(domain, row["parameters"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return policies


def challenger_policies(domain: str, baseline: Mapping[str, Any]) -> list[dict[str, float]]:
    key = next(iter(DEFAULTS[domain]))
    lo, hi = BOUNDS[key]
    step = 0.25 if domain != "salience" else 0.5
    values = [max(lo, float(baseline[key]) - step), min(hi, float(baseline[key]) + step)]
    return [{key: value} for value in dict.fromkeys(values) if value != baseline[key]]
