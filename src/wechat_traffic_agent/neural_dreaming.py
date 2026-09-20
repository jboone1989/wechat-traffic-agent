"""Neural replay and preconcept formation for Ferro.

Dreaming here is deliberately not an LLM summary. Persisted high-salience traces
are replayed through the same recurrent connectome, recombined, and only then
compressed into inspectable preconcepts that an Ego/LLM may later verbalize.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


class FerroDreamEngine:
    SCHEMA_VERSION = "ferro-dreams.v1"
    MAX_DREAMS = 64
    MAX_PRECONCEPTS = 64

    def __init__(self, state_root: Path, *, substrate: Any | None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.path = self.state_root / "application" / "neural_dreams.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        payload = _read_json(self.path)
        if payload.get("schema_version") == self.SCHEMA_VERSION:
            return payload
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": None,
            "dream_count": 0,
            "dreams": [],
            "preconcepts": [],
            "coactivations": {},
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @staticmethod
    def _trace_currents(trace: Mapping[str, Any], index: int) -> dict[str, float]:
        explicit = trace.get("currents")
        if isinstance(explicit, Mapping) and explicit:
            return {
                str(key): min(1.5, max(0.0, float(value)))
                for key, value in explicit.items()
                if isinstance(value, (int, float))
            }
        env = str(trace.get("environment") or "")
        reward = float(trace.get("reward") or 0.0)
        action = str(trace.get("action") or "")
        currents: dict[str, float] = {
            "curiosity": 0.25 + min(0.55, abs(reward) * 0.5),
            "opportunity": max(0.0, reward) * 0.8,
            "error_recovery": max(0.0, -reward) * 0.8,
        }
        env_channel = {
            "PONG": "prediction_due",
            "MAZE": "task_urgency",
            "FORAGING": "resource_pressure",
            "ROVER": "threat_risk",
        }.get(env)
        if env_channel:
            currents[env_channel] = 0.35 + min(0.55, abs(reward))
        action_channel = {
            "UP": "prediction_due", "DOWN": "error_recovery",
            "LEFT": "social_attention", "RIGHT": "opportunity",
            "FORWARD": "task_urgency", "STAY": "curiosity",
        }.get(action)
        if action_channel:
            currents[action_channel] = max(currents.get(action_channel, 0.0), 0.30 + (index % 4) * 0.08)
        return currents

    @staticmethod
    def _world_trace(row: Mapping[str, Any]) -> dict[str, Any]:
        outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        reward = float(outcome.get("reward") or 0.0)
        activation = max(0.0, min(1.0, float(row.get("activation") or 0.0)))
        channels = [str(value) for value in (row.get("stimulus_channels") or ()) if value]
        currents = {
            channel: round(0.18 + (0.42 * activation), 6)
            for channel in channels
        }
        currents["curiosity"] = max(currents.get("curiosity", 0.0), 0.18 + 0.25 * abs(reward))
        if reward > 0:
            currents["opportunity"] = max(currents.get("opportunity", 0.0), min(1.2, 0.25 + reward * 0.6))
        elif reward < 0:
            currents["error_recovery"] = max(currents.get("error_recovery", 0.0), min(1.2, 0.25 + abs(reward) * 0.6))
        task = str(row.get("task") or "")
        task_channel = {
            "RESOURCE_REVIEW": "resource_pressure",
            "CHECK_X": "social_attention",
            "CONTENT_COGNITION": "prediction_due",
            "REFLECT": "curiosity",
            "NEURAL_PLAY": "curiosity",
            "NEURAL_DREAM": "prediction_due",
            "OBSERVE_WORLD": "opportunity",
        }.get(task)
        if task_channel:
            currents[task_channel] = max(currents.get(task_channel, 0.0), 0.35 + activation * 0.35)
        return {
            "event_id": str(row.get("decision_id") or row.get("source_event_id") or ""),
            "source_kind": "WORLD_MOTOR",
            "environment": None,
            "action": task or str(row.get("motor_id") or ""),
            "reward": reward,
            "spikes": int((row.get("burst") or {}).get("spikes") or 0),
            "currents": currents,
            "observed_at": outcome.get("observed_at") or row.get("created_at"),
        }

    def _salient_traces(self) -> list[dict[str, Any]]:
        lab = _read_json(self.state_root / "application" / "brain_lab.json")
        game_rows = []
        for raw in (lab.get("recent") or []):
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            row["source_kind"] = "BRAIN_LAB"
            game_rows.append(row)

        motor = _read_json(self.state_root / "application" / "neural_motor.json")
        world_rows = [
            self._world_trace(row)
            for row in (motor.get("recent_decisions") or [])
            if isinstance(row, Mapping)
            and isinstance(row.get("outcome"), Mapping)
            and str(row.get("status") or "") == "SETTLED"
        ]
        rows = game_rows + world_rows
        rows.sort(
            key=lambda row: (
                -abs(float(row.get("reward") or 0.0)),
                -int(row.get("spikes") or 0),
                str(row.get("event_id") or ""),
            )
        )
        # Keep both life/world experience and Brain Lab experience represented
        # when both exist, instead of allowing one high-volume source to crowd
        # the other out of every dream.
        selected: list[dict[str, Any]] = []
        for kind in ("WORLD_MOTOR", "BRAIN_LAB"):
            selected.extend([row for row in rows if row.get("source_kind") == kind][:6])
        if len(selected) < 12:
            seen = {str(row.get("event_id") or "") for row in selected}
            selected.extend(
                row for row in rows
                if str(row.get("event_id") or "") not in seen
            )
        return selected[:12]

    @staticmethod
    def _merge_currents(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, float]:
        keys = set(a) | set(b)
        return {
            key: min(1.5, max(0.0, (float(a.get(key, 0.0)) * 0.58) + (float(b.get(key, 0.0)) * 0.58)))
            for key in keys
        }

    def dream(self, *, now: datetime | None = None, replay_count: int = 6) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        traces = self._salient_traces()
        if self.substrate is None:
            return {"status": "CONNECTOME_UNAVAILABLE", "replays": 0, "preconcept": None}
        if not traces:
            return {"status": "NO_EXPERIENCE_TO_REPLAY", "replays": 0, "preconcept": None}

        dream_seq = int(self.state.get("dream_count") or 0) + 1
        count = max(1, min(int(replay_count), 16))
        replay_rows: list[dict[str, Any]] = []
        aggregate: dict[str, float] = {}
        for index in range(count):
            left = traces[index % len(traces)]
            seed = int.from_bytes(
                sha256(f"dream:{dream_seq}:{index}".encode()).digest()[:4], "little"
            )
            right = traces[(seed + index + 1) % len(traces)]
            currents = self._merge_currents(
                self._trace_currents(left, index),
                self._trace_currents(right, index + 1),
            )
            event_id = f"dream:{dream_seq}:{index}"
            burst = dict(self.substrate.stimulate(
                currents, now, source_event_id=event_id, feedback_eligible=False
            ) or {})
            delta = {
                str(k): float(v) for k, v in (burst.get("delta_readout_hz") or {}).items()
                if isinstance(v, (int, float))
            }
            strongest = sorted(delta.items(), key=lambda item: (-item[1], item[0]))[:4]
            for channel, value in strongest:
                if value > 0:
                    aggregate[channel] = aggregate.get(channel, 0.0) + value
            replay_rows.append({
                "event_id": event_id,
                "source_events": [left.get("event_id"), right.get("event_id")],
                "source_kinds": [left.get("source_kind"), right.get("source_kind")],
                "source_environments": [left.get("environment"), right.get("environment")],
                "source_actions": [left.get("action"), right.get("action")],
                "currents": {k: round(v, 5) for k, v in sorted(currents.items())},
                "strongest_channels": [[k, round(v, 5)] for k, v in strongest],
                "spikes": int(burst.get("spikes") or 0),
                "active_neurons": int(burst.get("active_neurons") or 0),
            })

        ranking = sorted(aggregate.items(), key=lambda item: (-item[1], item[0]))
        top_channels = [name for name, value in ranking[:3] if value > 0]
        signature_raw = "|".join(top_channels[:3]) or "quiet"
        signature = f"sha256:{sha256(signature_raw.encode()).hexdigest()[:24]}"
        coactivations = dict(self.state.get("coactivations") or {})
        coactivations[signature] = int(coactivations.get(signature) or 0) + 1
        self.state["coactivations"] = coactivations

        preconcept = None
        if len(top_channels) >= 2:
            strength = min(1.0, sum(max(0.0, value) for _, value in ranking[:3]) / max(1.0, 18.0 * count))
            preconcept_id = f"preconcept:{sha256((signature + ':' + str(dream_seq)).encode()).hexdigest()[:20]}"
            preconcept = {
                "preconcept_id": preconcept_id,
                "formed_at": now.isoformat(),
                "channels": top_channels,
                "association_strength": round(strength, 6),
                "recurrence_count": coactivations[signature],
                "origin": "NEURAL_REPLAY_RECOMBINATION",
                "status": "PRECONCEPT",
                "verbalized": False,
                "source_dream": f"dream:{dream_seq}",
            }
            concepts = list(self.state.get("preconcepts") or ())
            concepts.append(preconcept)
            self.state["preconcepts"] = concepts[-self.MAX_PRECONCEPTS:]

        source_kind_counts: dict[str, int] = {}
        for trace in traces:
            kind = str(trace.get("source_kind") or "UNKNOWN")
            source_kind_counts[kind] = source_kind_counts.get(kind, 0) + 1
        dream = {
            "dream_id": f"dream:{dream_seq}",
            "dreamed_at": now.isoformat(),
            "replay_count": len(replay_rows),
            "source_trace_count": len(traces),
            "source_kind_counts": source_kind_counts,
            "replays": replay_rows,
            "dominant_channels": [[k, round(v, 5)] for k, v in ranking[:6]],
            "preconcept_id": preconcept.get("preconcept_id") if preconcept else None,
            "llm_used_for_replay": False,
        }
        dreams = list(self.state.get("dreams") or ())
        dreams.append(dream)
        self.state.update({
            "updated_at": now.isoformat(),
            "dream_count": dream_seq,
            "dreams": dreams[-self.MAX_DREAMS:],
        })
        self._write()
        return {"status": "DREAMED", "dream": dream, "preconcept": preconcept}

    def latest_unverbalized_preconcept(self) -> dict[str, Any] | None:
        for row in reversed(self.state.get("preconcepts") or []):
            if not bool(row.get("verbalized")):
                return dict(row)
        return None

    def mark_verbalized(self, preconcept_id: str, *, hypothesis: str, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        for row in reversed(self.state.get("preconcepts") or []):
            if str(row.get("preconcept_id")) == str(preconcept_id):
                row["verbalized"] = True
                row["verbalized_at"] = now.isoformat()
                row["hypothesis"] = str(hypothesis)[:1000]
                row["status"] = "EGO_INTERPRETED"
                self._write()
                return dict(row)
        raise KeyError(preconcept_id)

    def projection(self) -> dict[str, Any]:
        latest = (self.state.get("dreams") or [])[-1] if self.state.get("dreams") else None
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": self.state.get("updated_at"),
            "dream_count": int(self.state.get("dream_count") or 0),
            "latest_dream": dict(latest) if isinstance(latest, Mapping) else None,
            "latest_preconcept": self.latest_unverbalized_preconcept(),
            "preconcept_count": len(self.state.get("preconcepts") or []),
            "llm_used_for_replay": False,
        }


__all__ = ["FerroDreamEngine"]
