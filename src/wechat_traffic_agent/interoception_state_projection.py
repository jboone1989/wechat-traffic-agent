"""Pure projection of active fault episodes into bounded interoception state.

Receives an immutable read view and returns the next projection. No
filesystem access, task scheduling or LLM invocation.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any


def compose_interoception_state(
        *, previous_state, observation, active, distress, l1_health,
        now, bounded, schema, recent_limit):
    recent = [
        dict(row)
        for row in list(previous_state.get("recent_observations") or [])
        if isinstance(row, Mapping)
    ]
    recent.append(observation)
    recent = recent[-recent_limit:]

    fault_scores: dict[str, dict[str, Any]] = {}
    for payload in active:
        domain = str(payload.get("subsystem") or "unknown")
        severity_value = bounded(
            payload.get("current_severity"),
            bounded(payload.get("max_severity")),
        )
        occurrences = max(1, int(payload.get("occurrences") or 1))
        confidence = min(0.98, 0.45 + 0.09 * min(5, occurrences) + 0.30 * severity_value)
        prior = fault_scores.get(domain)
        row = {
            "subsystem": domain,
            "confidence": round(confidence, 6),
            "severity": round(severity_value, 6),
            "episode_ids": [str(payload.get("episode_id") or "")],
            "reason": str(payload.get("primary_symptom") or "unlocalized"),
        }
        if prior is None:
            fault_scores[domain] = row
        else:
            prior["confidence"] = round(max(float(prior["confidence"]), confidence), 6)
            prior["severity"] = round(max(float(prior["severity"]), severity_value), 6)
            prior["episode_ids"] = list(dict.fromkeys([
                *list(prior.get("episode_ids") or []),
                str(payload.get("episode_id") or ""),
            ]))[:8]

    suspected = sorted(
        fault_scores.values(),
        key=lambda row: (-float(row["confidence"]), -float(row["severity"]), str(row["subsystem"])),
    )[:8]
    active_ids = [
        str(payload.get("episode_id") or "")
        for payload in active
        if str(payload.get("episode_id") or "")
    ]
    symptom_names = list(dict.fromkeys(
        str(payload.get("primary_symptom") or "")
        for payload in active
        if str(payload.get("primary_symptom") or "")
    ))[:12]
    if active and distress < max(bounded(row.get("current_severity")) for row in active):
        distress = max(bounded(row.get("current_severity")) for row in active)
    status = (
        "ILL" if distress >= 0.55 or any(float(row["severity"]) >= 0.60 for row in suspected)
        else "WATCH" if active or distress >= 0.20
        else "HEALTHY"
    )
    new_state = {
        "schema_version": schema,
        "status": status,
        "updated_at": now.isoformat(),
        "distress": round(distress, 6),
        "confidence_in_self": round(max(0.0, 1.0 - 0.80 * distress), 6),
        "active_episode_ids": active_ids,
        "suspected_faults": suspected,
        "symptoms": symptom_names,
        "latest_observation_id": observation["observation_id"],
        "l1_health": dict(l1_health),
        "recent_observations": recent,
    }
    return new_state
