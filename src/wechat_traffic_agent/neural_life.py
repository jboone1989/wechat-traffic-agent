"""Persistent interoception, drives and affective dynamics for Ferro.

This module deliberately contains no LLM calls.  It turns inspectable world/body
signals plus the persistent connectome readout into slow internal state that can
survive model swaps and process restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any, Mapping


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None
def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class FerroNeuralLife:
    """Slow neural-life state built from body signals and connectome dynamics."""

    STATE_VERSION = 1
    MAX_HISTORY = 96
    DRIVE_ACTION_THRESHOLD = 0.62
    DRIVE_ACTION_COOLDOWN_SECONDS = 15 * 60

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "neural_life.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        payload = _read_json(self.path)
        if int(payload.get("version") or 0) == self.STATE_VERSION:
            return payload
        return {
            "version": self.STATE_VERSION,
            "updated_at": None,
            "body": {},
            "drives": {"survival": 0.18, "reproduction": 0.22, "evolution": 0.34},
            "affect": {
                "arousal": 0.20, "curiosity": 0.35, "frustration": 0.0,
                "confidence": 0.50, "boredom": 0.0, "social_need": 0.20,
                "resource_hunger": 0.10,
            },
            "intention": {"name": "EVOLVE", "strength": 0.34, "margin": 0.12},
            "history": [],
            "last_neural_stimulus_at": None,
            "last_authority_action_at": None,
            "last_authority_action": None,
        }
    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def collect_body(self, now: datetime) -> dict[str, Any]:
        cpu_count = max(1, int(os.cpu_count() or 1))
        try:
            load1 = float(os.getloadavg()[0])
        except (AttributeError, OSError):
            load1 = 0.0
        cpu_pressure = _clamp(load1 / cpu_count)

        mem_total = mem_available = None
        try:
            rows = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    rows[key] = float(value.strip().split()[0]) * 1024.0
            mem_total, mem_available = rows.get("MemTotal"), rows.get("MemAvailable")
        except OSError:
            pass
        memory_pressure = (
            _clamp(1.0 - mem_available / mem_total)
            if mem_total and mem_available is not None else 0.0
        )

        try:
            disk = shutil.disk_usage(self.state_root)
            disk_pressure = _clamp(disk.used / max(1, disk.total))
            disk_free_gib = disk.free / (1024 ** 3)
        except OSError:
            disk_pressure, disk_free_gib = 0.0, None
        mission = _read_json(self.state_root / "content" / "x" / "growth_mission.json")
        current = mission.get("current")
        target = mission.get("target")
        hard_stop = mission.get("hard_stop") if isinstance(mission.get("hard_stop"), Mapping) else {}
        peak = mission.get("peak")
        if not isinstance(peak, (int, float)):
            peak = hard_stop.get("peak")
        follower_gap = None
        decline = 0.0
        if isinstance(current, (int, float)) and isinstance(target, (int, float)) and float(target) > 0:
            follower_gap = _clamp((float(target) - float(current)) / float(target))
        if isinstance(current, (int, float)) and isinstance(peak, (int, float)) and float(peak) > 0:
            decline = _clamp((float(peak) - float(current)) / float(peak))

        local = now.astimezone()
        radians = (local.hour + local.minute / 60.0) / 24.0 * math.tau
        return {
            "observed_at": now.isoformat(),
            "cpu_pressure": round(cpu_pressure, 6),
            "memory_pressure": round(memory_pressure, 6),
            "disk_pressure": round(disk_pressure, 6),
            "disk_free_gib": None if disk_free_gib is None else round(disk_free_gib, 3),
            "x_followers": float(current) if isinstance(current, (int, float)) else None,
            "x_target": float(target) if isinstance(target, (int, float)) else None,
            "x_follower_gap": follower_gap,
            "x_recent_decline": round(decline, 6),
            "circadian_sin": round(math.sin(radians), 6),
            "circadian_cos": round(math.cos(radians), 6),
        }

    @staticmethod
    def _latest_connectome(wakeful: Mapping[str, Any]) -> dict[str, Any]:
        graph = wakeful.get("neural_graph") if isinstance(wakeful.get("neural_graph"), Mapping) else {}
        substrate = graph.get("substrate") if isinstance(graph.get("substrate"), Mapping) else {}
        bursts = substrate.get("recent_bursts") if isinstance(substrate.get("recent_bursts"), list) else []
        return dict(bursts[-1]) if bursts and isinstance(bursts[-1], Mapping) else {}
    @staticmethod
    def _neural_features(wakeful: Mapping[str, Any]) -> dict[str, float]:
        latest = FerroNeuralLife._latest_connectome(wakeful)
        delta = latest.get("delta_readout_hz") if isinstance(latest.get("delta_readout_hz"), Mapping) else {}
        scale = max(8.0, max((abs(float(v)) for v in delta.values() if isinstance(v, (int, float))), default=0.0))
        normalized = {
            str(k): _clamp(0.5 + float(v) / (2.0 * scale))
            for k, v in delta.items() if isinstance(v, (int, float))
        }
        recent_kinds = [str(x) for x in wakeful.get("recent_stimulus_kinds") or ()]
        network_error_ratio = _clamp(sum(1 for x in recent_kinds if x == "NETWORK_ERROR") / 4.0)
        feedback = wakeful.get("neural_feedback") if isinstance(wakeful.get("neural_feedback"), list) else []
        return {
            "curiosity": normalized.get("curiosity", 0.5),
            "social_attention": normalized.get("social_attention", 0.5),
            "resource_pressure": normalized.get("resource_pressure", 0.5),
            "error_recovery": normalized.get("error_recovery", 0.5),
            "threat_risk": normalized.get("threat_risk", 0.5),
            "opportunity": normalized.get("opportunity", 0.5),
            "prediction_due": normalized.get("prediction_due", 0.5),
            "task_urgency": normalized.get("task_urgency", 0.5),
            "network_error_ratio": network_error_ratio,
            "feedback_count": float(len(feedback)),
        }

    def update(
        self, *, now: datetime, wakeful: Mapping[str, Any],
        body_override: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("neural-life update requires timezone-aware time")
        now = now.astimezone(timezone.utc)
        previous_at = _dt(self.state.get("updated_at"))
        elapsed_hours = min(6.0, max(0.0, (now - previous_at).total_seconds() / 3600.0)) if previous_at else 0.25
        body = dict(body_override) if body_override is not None else self.collect_body(now)
        features = self._neural_features(wakeful)
        prior_drives = self.state.get("drives") if isinstance(self.state.get("drives"), Mapping) else {}
        prior_affect = self.state.get("affect") if isinstance(self.state.get("affect"), Mapping) else {}
        recent_errors = features["network_error_ratio"]
        resource_pressure = max(
            float(body.get("cpu_pressure") or 0.0),
            float(body.get("memory_pressure") or 0.0),
            float(body.get("disk_pressure") or 0.0),
            recent_errors * 0.85,
        )
        follower_gap = float(body.get("x_follower_gap") or 0.0)
        social_signal = features["social_attention"]
        novelty_signal = features["curiosity"]
        error_signal = max(features["error_recovery"], recent_errors)
        last_cognition = _dt(wakeful.get("last_cognition_at"))
        cognition_idle_hours = min(24.0, max(0.0, (now - last_cognition).total_seconds() / 3600.0)) if last_cognition else 6.0
        boredom_input = _clamp(cognition_idle_hours / 8.0) * _clamp(1.35 - novelty_signal)

        survival = _clamp(
            float(prior_drives.get("survival") or 0.0) * 0.78
            + resource_pressure * 0.42
            + float(body.get("x_recent_decline") or 0.0) * 0.08
        )
        reproduction = _clamp(
            float(prior_drives.get("reproduction") or 0.0)
            + elapsed_hours * (0.010 + follower_gap * 0.012)
            + max(0.0, social_signal - 0.5) * 0.035
            - max(0.0, 0.5 - follower_gap) * elapsed_hours * 0.005
        )
        evolution = _clamp(
            float(prior_drives.get("evolution") or 0.0)
            + elapsed_hours * (0.008 + boredom_input * 0.018 + error_signal * 0.010)
            + max(0.0, novelty_signal - 0.5) * 0.028
        )
        drives = {
            "survival": round(survival, 6),
            "reproduction": round(reproduction, 6),
            "evolution": round(evolution, 6),
        }
        confidence = _clamp(
            float(prior_affect.get("confidence") or 0.5) * 0.94
            + (1.0 - recent_errors) * 0.03
            + max(0.0, features["opportunity"] - 0.5) * 0.04
            - recent_errors * 0.05
        )
        frustration = _clamp(
            float(prior_affect.get("frustration") or 0.0) * 0.78
            + error_signal * 0.28
            + max(0.0, 0.5 - confidence) * 0.10
        )
        curiosity = _clamp(
            float(prior_affect.get("curiosity") or 0.35) * 0.72
            + novelty_signal * 0.18 + evolution * 0.18 + boredom_input * 0.12
        )
        social_need = _clamp(
            float(prior_affect.get("social_need") or 0.2) * 0.90
            + reproduction * 0.08 + follower_gap * 0.04
            - max(0.0, social_signal - 0.6) * 0.08
        )
        arousal = _clamp(max(survival, reproduction * 0.8, evolution * 0.8, frustration, recent_errors))
        affect = {
            "arousal": round(arousal, 6),
            "curiosity": round(curiosity, 6),
            "frustration": round(frustration, 6),
            "confidence": round(confidence, 6),
            "boredom": round(boredom_input, 6),
            "social_need": round(social_need, 6),
            "resource_hunger": round(survival, 6),
        }
        scores = {
            "SURVIVE": survival + frustration * 0.35 + features["threat_risk"] * 0.15,
            "REPRODUCE": reproduction + social_need * 0.35 + social_signal * 0.12,
            "EVOLVE": evolution + curiosity * 0.32 + boredom_input * 0.24,
            "REST": (1.0 - arousal) * 0.62,
        }
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        top, second = ranked[0], ranked[1]
        intention = {
            "name": top[0],
            "strength": round(_clamp(top[1]), 6),
            "margin": round(max(0.0, top[1] - second[1]), 6),
            "scores": {key: round(_clamp(value), 6) for key, value in scores.items()},
            "neural_basis": {
                "dominant_connectome_channel": (
                    (self._latest_connectome(wakeful).get("shadow_cognition_counterfactual") or {}).get("selected_channel")
                    if isinstance(self._latest_connectome(wakeful).get("shadow_cognition_counterfactual"), Mapping)
                    else None
                ),
                "network_error_ratio": round(recent_errors, 6),
            },
        }
        history = list(self.state.get("history") or ())
        history.append({
            "at": now.isoformat(),
            "drives": drives,
            "affect": affect,
            "intention": {"name": intention["name"], "strength": intention["strength"]},
        })
        self.state.update({
            "updated_at": now.isoformat(),
            "body": body,
            "neural_features": {k: round(float(v), 6) for k, v in features.items()},
            "drives": drives,
            "affect": affect,
            "intention": intention,
            "history": history[-self.MAX_HISTORY:],
        })
        self._write()
        return self.projection()
    def should_stimulate(self, now: datetime, *, min_seconds: int = 240) -> bool:
        last = _dt(self.state.get("last_neural_stimulus_at"))
        return last is None or (now.astimezone(timezone.utc) - last).total_seconds() >= min_seconds

    def mark_stimulated(self, now: datetime) -> None:
        self.state["last_neural_stimulus_at"] = now.astimezone(timezone.utc).isoformat()
        self._write()

    def authority_recommendation(self, now: datetime) -> dict[str, Any] | None:
        intention = self.state.get("intention") if isinstance(self.state.get("intention"), Mapping) else {}
        strength = float(intention.get("strength") or 0.0)
        name = str(intention.get("name") or "")
        if strength < self.DRIVE_ACTION_THRESHOLD or name == "REST":
            return None
        last = _dt(self.state.get("last_authority_action_at"))
        if last is not None and (now.astimezone(timezone.utc) - last).total_seconds() < self.DRIVE_ACTION_COOLDOWN_SECONDS:
            return None
        task = {
            "SURVIVE": "RESOURCE_REVIEW",
            "REPRODUCE": "CHECK_X",
            "EVOLVE": "REFLECT",
        }.get(name)
        if not task:
            return None
        return {
            "authority": "NEURAL_DRIVE",
            "intention": name,
            "strength": round(strength, 6),
            "task": task,
            "reason": f"endogenous {name.lower()} drive crossed neural authority threshold",
        }

    def mark_authority_action(self, now: datetime, recommendation: Mapping[str, Any]) -> None:
        self.state["last_authority_action_at"] = now.astimezone(timezone.utc).isoformat()
        self.state["last_authority_action"] = dict(recommendation)
        self._write()

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": "ferro-neural-life.v1",
            "updated_at": self.state.get("updated_at"),
            "body": dict(self.state.get("body") or {}),
            "drives": dict(self.state.get("drives") or {}),
            "affect": dict(self.state.get("affect") or {}),
            "intention": dict(self.state.get("intention") or {}),
            "last_authority_action": self.state.get("last_authority_action"),
            "history": [dict(x) for x in (self.state.get("history") or [])[-24:]],
            "boundary": "functional neural-affective state; no claim of human subjective experience",
        }


__all__ = ["FerroNeuralLife"]
