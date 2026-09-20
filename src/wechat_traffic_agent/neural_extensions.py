"""N32 bounded self-extension of Ferro's sensory and motor body.

Extensions are born from persistent, evidence-backed developmental needs. A new
sensor is not arbitrary code: it is selected from a read-only allowlist of real
state signals and receives an opaque sensor identity plus a deterministic sparse
population code into the existing connectome. A motor extension grows new
unsemantic motor units only; it never grants a new external action authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-neural-extensions.v1"
MAX_EXTENSIONS = 64
SENSOR_SAMPLE_COOLDOWN_SECONDS = 300
BASE_CHANNELS = (
    "curiosity",
    "threat_risk",
    "social_attention",
    "task_urgency",
    "prediction_due",
    "resource_pressure",
    "opportunity",
    "error_recovery",
)

SAFE_SENSOR_CATALOG = {
    "CHECK_X": (
        {
            "kind": "X_MISSION_PRESSURE",
            "relative_path": "content/x/growth_mission.json",
            "json_path": ("pressure",),
            "normalization": "unit",
        },
        {
            "kind": "X_RESOURCE_WILLINGNESS",
            "relative_path": "content/x/growth_mission.json",
            "json_path": ("resource_willingness",),
            "normalization": "unit",
        },
        {
            "kind": "X_PROGRESS",
            "relative_path": "content/x/growth_mission.json",
            "json_path": ("progress",),
            "normalization": "unit",
        },
    ),
    "CONTENT_COGNITION": (
        {
            "kind": "DREAM_STABLE_ATTRACTOR_FRACTION",
            "relative_path": "application/neural_dreams.json",
            "json_path": ("attractors",),
            "normalization": "stable_fraction",
        },
    ),
    "OBSERVE_WORLD": (
        {
            "kind": "NETWORK_HEALTH",
            "relative_path": "application/health.json",
            "json_path": ("ok",),
            "normalization": "bool",
        },
    ),
}


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class NeuralSelfExtension:
    """Evidence-bound receptor/motor growth without LLM authorship."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "neural_extensions.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "extension_seq": 0,
            "extensions": {},
        }

    @staticmethod
    def _has_revised_world(agency: Mapping[str, Any], need_id: str) -> bool:
        return any(
            isinstance(row, Mapping)
            and str(row.get("need_id") or "") == str(need_id)
            and str(row.get("status") or "") in {"REVISE_REQUIRED", "REJECTED"}
            for row in agency.get("worlds") or ()
        )

    @staticmethod
    def _population_code(sensor_id: str) -> dict[str, float]:
        ranked = []
        for channel in BASE_CHANNELS:
            raw = int.from_bytes(
                sha256(f"{sensor_id}|{channel}".encode()).digest()[:4], "big"
            )
            ranked.append((raw, channel))
        ranked.sort(reverse=True)
        selected = ranked[:3]
        return {
            channel: round(0.45 + 0.45 * (raw / 0xFFFFFFFF), 6)
            for raw, channel in selected
        }

    def _new_extension_id(self, prefix: str) -> str:
        self.state["extension_seq"] = int(self.state.get("extension_seq") or 0) + 1
        return f"{prefix}_{int(self.state['extension_seq']):04d}"

    def _choose_sensor_spec(self, target_task: str, need_id: str) -> dict[str, Any] | None:
        catalog = SAFE_SENSOR_CATALOG.get(str(target_task), ())
        if not catalog:
            return None
        token = int.from_bytes(sha256(str(need_id).encode()).digest()[:4], "big")
        return dict(catalog[token % len(catalog)])

    def refresh(
        self,
        *,
        now: datetime | None,
        agency: Mapping[str, Any],
        motor_cortex: Any,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        extensions = dict(self.state.get("extensions") or {})
        active_needs = [
            dict(row)
            for row in agency.get("needs") or ()
            if isinstance(row, Mapping)
            and row.get("status") == "ACTIVE"
            and float(row.get("neural_pressure") or 0.0) >= 0.25
        ]

        for need in active_needs:
            need_id = str(need.get("need_id") or "")
            target_task = str(need.get("target_task") or need.get("capability") or "")
            if not need_id or not self._has_revised_world(agency, need_id):
                continue

            existing_sensor = next(
                (
                    row for row in extensions.values()
                    if isinstance(row, Mapping)
                    and row.get("need_id") == need_id
                    and row.get("extension_type") == "SENSOR"
                    and row.get("status") not in {"REJECTED", "RETIRED"}
                ),
                None,
            )
            if existing_sensor is None:
                spec = self._choose_sensor_spec(target_task, need_id)
                if spec is not None:
                    extension_id = self._new_extension_id("sensor_ext")
                    sensor_id = "sensor_" + sha256(
                        f"{need_id}|{spec['kind']}".encode()
                    ).hexdigest()[:12]
                    extensions[extension_id] = {
                        "extension_id": extension_id,
                        "extension_type": "SENSOR",
                        "sensor_id": sensor_id,
                        "status": "ACTIVE_EXPERIMENT",
                        "born_at": now.isoformat(),
                        "need_id": need_id,
                        "target_task": target_task,
                        "reason": "REAL_WORLD_TRAINING_WORLD_FAILED_TO_IMPROVE_TARGET",
                        "source": spec,
                        "population_code": self._population_code(sensor_id),
                        "sample_count": 0,
                        "last_sample_at": None,
                        "last_value": None,
                        "llm_used_for_birth": False,
                        "external_side_effect_authority": False,
                    }

            existing_motor = next(
                (
                    row for row in extensions.values()
                    if isinstance(row, Mapping)
                    and row.get("need_id") == need_id
                    and row.get("extension_type") == "MOTOR_CAPACITY"
                    and row.get("status") not in {"REJECTED", "RETIRED"}
                ),
                None,
            )
            if existing_motor is None:
                motor_projection = motor_cortex.projection()
                habits = motor_projection.get("habits") or ()
                target_is_habit = any(
                    isinstance(row, Mapping) and row.get("meaning") == target_task
                    for row in habits
                )
                if not target_is_habit:
                    extension_id = self._new_extension_id("motor_ext")
                    extensions[extension_id] = {
                        "extension_id": extension_id,
                        "extension_type": "MOTOR_CAPACITY",
                        "status": "PROPOSED",
                        "born_at": now.isoformat(),
                        "need_id": need_id,
                        "target_task": target_task,
                        "reason": "PERSISTENT_NEED_WITHOUT_STABLE_TARGET_HABIT",
                        "requested_units": 4,
                        "born_motor_ids": [],
                        "llm_used_for_birth": False,
                        "new_external_actions_granted": False,
                    }

        self.state["extensions"] = extensions

        for extension_id, raw in list(extensions.items()):
            if (
                isinstance(raw, Mapping)
                and raw.get("extension_type") == "MOTOR_CAPACITY"
                and raw.get("status") == "PROPOSED"
            ):
                row = dict(raw)
                born = motor_cortex.extend_units(
                    count=int(row.get("requested_units") or 0),
                    origin_ref=extension_id,
                    now=now,
                )
                row["born_motor_ids"] = list(born)
                row["status"] = "ACTIVE_EXPERIMENT" if born else "CAPACITY_LIMIT"
                row["activated_at"] = now.isoformat()
                extensions[extension_id] = row

        self.state.update({"updated_at": now.isoformat(), "extensions": extensions})
        _write(self.path, self.state)
        return self.projection()

    @staticmethod
    def _extract(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = payload
        for key in path:
            if not isinstance(current, Mapping):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _normalize(value: Any, mode: str) -> float | None:
        if mode == "unit":
            return _clip(float(value)) if isinstance(value, (int, float)) else None
        if mode == "bool":
            return 1.0 if value is True else 0.0 if value is False else None
        if mode == "stable_fraction":
            if not isinstance(value, Mapping) or not value:
                return 0.0
            rows = [row for row in value.values() if isinstance(row, Mapping)]
            if not rows:
                return 0.0
            return _clip(sum(row.get("status") == "STABLE" for row in rows) / len(rows))
        return None

    def sample_active_sensors(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        extensions = dict(self.state.get("extensions") or {})
        samples: list[dict[str, Any]] = []
        changed = False
        for extension_id, raw in extensions.items():
            if not isinstance(raw, Mapping):
                continue
            if raw.get("extension_type") != "SENSOR" or raw.get("status") != "ACTIVE_EXPERIMENT":
                continue
            row = dict(raw)
            last = _parse_time(row.get("last_sample_at"))
            if last is not None and (now - last).total_seconds() < SENSOR_SAMPLE_COOLDOWN_SECONDS:
                continue
            source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
            relative = str(source.get("relative_path") or "")
            allowed = {
                str(spec["relative_path"])
                for specs in SAFE_SENSOR_CATALOG.values()
                for spec in specs
            }
            if relative not in allowed:
                continue
            payload = _read(self.state_root / relative)
            json_path = tuple(str(x) for x in source.get("json_path") or ())
            raw_value = self._extract(payload, json_path)
            value = self._normalize(raw_value, str(source.get("normalization") or ""))
            if value is None:
                continue
            code = row.get("population_code") if isinstance(row.get("population_code"), Mapping) else {}
            currents = {
                str(channel): round(_clip(value) * float(weight), 6)
                for channel, weight in code.items()
                if channel in BASE_CHANNELS and isinstance(weight, (int, float))
            }
            sample = {
                "extension_id": extension_id,
                "sensor_id": row.get("sensor_id"),
                "need_id": row.get("need_id"),
                "target_task": row.get("target_task"),
                "observed_at": now.isoformat(),
                "value": round(float(value), 6),
                "currents": currents,
                "source_kind": source.get("kind"),
                "llm_used": False,
            }
            samples.append(sample)
            row["sample_count"] = int(row.get("sample_count") or 0) + 1
            row["last_sample_at"] = now.isoformat()
            row["last_value"] = sample["value"]
            extensions[extension_id] = row
            changed = True
        if changed:
            self.state.update({"updated_at": now.isoformat(), "extensions": extensions})
            _write(self.path, self.state)
        return samples

    def projection(self) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in (self.state.get("extensions") or {}).values()
            if isinstance(row, Mapping)
        ]
        rows.sort(key=lambda row: str(row.get("extension_id") or ""))
        sensors = [row for row in rows if row.get("extension_type") == "SENSOR"]
        motors = [row for row in rows if row.get("extension_type") == "MOTOR_CAPACITY"]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "extension_count": len(rows),
            "sensor_extension_count": len(sensors),
            "motor_extension_count": len(motors),
            "active_sensor_count": sum(row.get("status") == "ACTIVE_EXPERIMENT" for row in sensors),
            "ferro_born_motor_unit_count": sum(len(row.get("born_motor_ids") or ()) for row in motors),
            "extensions": rows[-16:],
            "sensor_source_policy": "READ_ONLY_ALLOWLIST",
            "motor_policy": "INTERNAL_UNSEMANTIC_CAPACITY_ONLY",
            "arbitrary_code_generation": False,
            "new_external_action_authority": False,
            "llm_used_for_extension_birth": False,
        }


__all__ = ["NeuralSelfExtension", "SCHEMA", "SAFE_SENSOR_CATALOG"]
