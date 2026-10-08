"""Translate a persisted episode decision into a bounded diagnosis request.

The request is returned to the caller; scheduling repair remains the
responsibility of its existing orchestrator and permissions.
"""
from __future__ import annotations


def diagnostic_failure_receipt(
        *, task, classification, episode_payload, episode_id, observation_id,
        primary_symptom, subsystem, severity, should_diagnose):
    diagnostic_failure = None
    diagnosis_active = bool(
        episode_payload
        and episode_payload.get("diagnosis_requested_at")
    )
    if diagnosis_active and episode_id:
        diagnostic_failure = {
            "status": "ERROR",
            "task": task,
            "cycle": observation_id,
            "error_type": "INTEROCEPTIVE_FAULT",
            "message": f"interoceptive {primary_symptom} in {subsystem}; task={task}",
            "failure_site": f"interoception:{subsystem}",
            "interoceptive_episode_id": episode_id,
            "severity": round(severity, 6),
            # Keep the concrete evidence that produced the body signal.
            # Self-maintenance and the bounded repair planner need the
            # original result path/status instead of only the coarse
            # subsystem label ("network", "provider", ...).
            "symptoms": [dict(row) for row in classification["symptoms"][:16]],
            "status_evidence": [
                dict(row) for row in classification["status_evidence"][:32]
            ],
            "diagnostic_recurrence": not should_diagnose,
        }
    return diagnosis_active, diagnostic_failure
