"""Persist fault episodes and recovery receipts through injected storage ports.

The ledger owns durable episode changes only; not LLM calls or repair actions.
"""
from __future__ import annotations
from typing import Any, Mapping


class InteroceptionEpisodeLedger:
    def __init__(self, *, open_episodes, episode_path, read_json, write_json,
                 episode_schema, recovery_streak):
        self.open_episodes, self.episode_path = open_episodes, episode_path
        self.read_json, self.write_json = read_json, write_json
        self.episode_schema, self.recovery_streak = episode_schema, recovery_streak

    def _abnormal(self, *, task, now, subsystem, primary_symptom, severity,
                  classification, l1_health, observation_id, cycle):
        # Any new pathology means the capability has not yet recovered.
        # Reset recovery evidence for every still-open episode of this task,
        # not only the newly observed symptom signature.
        for open_path, open_episode in self.open_episodes():
            if str(open_episode.get("task") or "") != task:
                continue
            open_episode["healthy_streak"] = 0
            open_episode["last_recurrence_at"] = now.isoformat()
            if str(open_episode.get("status") or "") == "RECOVERING":
                open_episode["status"] = "OPEN"
            self.write_json(open_path, open_episode)

        episode_id, episode_path = self.episode_path(task, subsystem, primary_symptom)
        previous = self.read_json(episode_path) or {}
        previous_status = str(previous.get("status") or "")
        occurrences = int(previous.get("occurrences") or 0) + 1
        should_diagnose = (
            not previous.get("diagnosis_requested_at")
            and (
                severity >= 0.45
                or (occurrences >= 3 and severity >= 0.30)
            )
        )
        episode_payload = {
            "schema_version": self.episode_schema,
            "episode_id": episode_id,
            "status": (
                previous_status
                if previous_status in {"DIAGNOSING", "REPAIR_PROPOSED", "REPAIR_REQUESTED"}
                else "OPEN"
            ),
            "task": task,
            "subsystem": subsystem,
            "primary_symptom": primary_symptom,
            "first_seen_at": previous.get("first_seen_at") or now.isoformat(),
            "last_seen_at": now.isoformat(),
            "occurrences": occurrences,
            "max_severity": round(max(float(previous.get("max_severity") or 0.0), severity), 6),
            "current_severity": round(severity, 6),
            "healthy_streak": 0,
            "symptoms": classification["symptoms"],
            "l1_health": dict(l1_health),
            "latest_observation_id": observation_id,
            "evidence_refs": list(dict.fromkeys([
                *[str(x) for x in previous.get("evidence_refs") or () if str(x)],
                observation_id,
                f"wakeful-task:{task}:{cycle}:result",
            ]))[-24:],
            "diagnosis_requested_at": (
                now.isoformat()
                if should_diagnose
                else previous.get("diagnosis_requested_at")
            ),
            "recovered_at": None,
        }
        self.write_json(episode_path, episode_payload)
        return episode_id, episode_payload, should_diagnose

    def _healthy(self, *, task, now, observation_id, cycle, classification):
        for episode_path, previous in self.open_episodes():
            if str(previous.get("task") or "") != task:
                continue
            healthy_streak = int(previous.get("healthy_streak") or 0) + 1
            previous["healthy_streak"] = healthy_streak
            previous["last_healthy_at"] = now.isoformat()
            previous["recovery_evidence_refs"] = list(dict.fromkeys([
                *[str(x) for x in previous.get("recovery_evidence_refs") or () if str(x)],
                observation_id,
                f"wakeful-task:{task}:{cycle}:result",
            ]))[-16:]
            if healthy_streak >= self.recovery_streak:
                previous["status"] = "RECOVERED_VERIFIED"
                previous["recovered_at"] = now.isoformat()
                previous["recovery_verification"] = {
                    "required_consecutive_healthy": self.recovery_streak,
                    "observed_consecutive_healthy": healthy_streak,
                    "latest_result_status": classification["primary_status"],
                }
            else:
                previous["status"] = "RECOVERING"
            self.write_json(episode_path, previous)
    def update(self, *, task, now, subsystem, primary_symptom,
               severity, classification, l1_health, observation_id, cycle):
        if classification["abnormal"]:
            return self._abnormal(
                task=task, now=now, subsystem=subsystem, primary_symptom=primary_symptom,
                severity=severity, classification=classification,
                l1_health=l1_health, observation_id=observation_id, cycle=cycle,
            )
        self._healthy(
            task=task, now=now, observation_id=observation_id, cycle=cycle,
            classification=classification,
        )
        return None, None, False
