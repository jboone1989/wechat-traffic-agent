"""Optional L1-health adapter enriching deterministic classification.

It may call the injected L1 provider, but does not read/write fault episodes
or schedule repair actions.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def enrich_l1_health(task: str, classification: dict[str, Any],
                     recent_same_task, *, l1):
    from .fast_decision import FastDecisionError

    try:
        l1_health = l1.assess_health(
            task=task,
            deterministic=classification,
            recent_observations=recent_same_task,
        )
    except FastDecisionError as exc:
        l1_health = {
            "schema_version": "ferro-l1-health.v1",
            "status": "ERROR",
            "health_state": "UNKNOWN",
            "subsystem": "interoception",
            "error_type": type(exc).__name__,
            "message": str(exc)[:400],
        }
        symptoms = list(classification.get("symptoms") or [])
        symptoms.append({
            "kind": "l1_diagnosis_failure",
            "severity": 0.48,
            "path": "interoception.l1_health",
            "detail": str(exc)[:240],
        })
        classification = {
            **classification,
            "abnormal": True,
            "primary_symptom": (
                classification.get("primary_symptom")
                if classification.get("primary_symptom") != "healthy"
                else "l1_diagnosis_failure"
            ),
            "subsystem": (
                classification.get("subsystem")
                if classification.get("primary_symptom") != "healthy"
                else "interoception"
            ),
            "base_severity": max(
                float(classification.get("base_severity") or 0.0),
                0.48,
            ),
            "symptoms": symptoms[:16],
        }
    else:
        health_state = str(l1_health.get("health_state") or "UNKNOWN")
        if health_state in {"DEGRADED", "STUCK", "BROKEN"}:
            severity_floor = {
                "DEGRADED": 0.42,
                "STUCK": 0.58,
                "BROKEN": 0.82,
            }[health_state]
            symptom_name = "l1_" + health_state.lower()
            symptoms = list(classification.get("symptoms") or [])
            if not any(
                str(row.get("kind") or "") == symptom_name
                for row in symptoms
                if isinstance(row, Mapping)
            ):
                symptoms.append({
                    "kind": symptom_name,
                    "severity": severity_floor,
                    "path": "interoception.l1_health",
                    "detail": (
                        f"{health_state}: "
                        f"{str(l1_health.get('subsystem') or 'unknown')}"
                    ),
                })
            classification = {
                **classification,
                "abnormal": True,
                "primary_symptom": (
                    classification.get("primary_symptom")
                    if classification.get("primary_symptom") != "healthy"
                    else symptom_name
                ),
                "subsystem": str(
                    l1_health.get("subsystem")
                    or classification.get("subsystem")
                    or "unknown"
                ),
                "base_severity": max(
                    float(classification.get("base_severity") or 0.0),
                    severity_floor,
                ),
                "symptoms": symptoms[:16],
            }

    return classification, l1_health
