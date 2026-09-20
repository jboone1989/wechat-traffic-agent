"""Continuity monitor for replaceable LLM cortexes.

Ferro's persistent organism identity belongs to the neural/memory/runtime state,
not to whichever language model currently supplies high-level reasoning.  This
module watches the existing LLM audit ledger and records actual provider/model
changes while checking monotonic neural lineage markers across the swap.

It never treats equality of hidden neural state as the goal: a living state is
expected to change. Continuity means the ancestral seed remains the same and
persistent lineage counters do not reset when the cortex changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


SCHEMA = "ferro-cortex-continuity.v1"
MAX_SWAPS = 128


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _tail_json_rows(path: Path, *, max_bytes: int = 256 * 1024, limit: int = 64) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            raw = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    if size > max_bytes:
        raw = raw.split("\n", 1)[-1]
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _cortex_descriptor(row: Mapping[str, Any]) -> dict[str, Any]:
    provider = row.get("provider") if isinstance(row.get("provider"), Mapping) else {}
    routing = row.get("routing") if isinstance(row.get("routing"), Mapping) else {}
    return {
        "class": provider.get("class"),
        "provider_name": provider.get("provider_name"),
        "model": provider.get("model") or provider.get("model_name"),
        "base_url": provider.get("base_url"),
        "selected_resource_id": routing.get("selected_resource_id"),
        "scope": row.get("scope"),
    }


def _cortex_key(cortex: Mapping[str, Any]) -> str:
    material = {
        key: cortex.get(key)
        for key in ("class", "provider_name", "model", "base_url", "selected_resource_id")
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + sha256(raw).hexdigest()


class CortexContinuityMonitor:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "cortex_continuity.json"
        self.audit_path = self.state_root / "application" / "llm_audit" / "requests.jsonl"
        raw = _read_json(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        else:
            self.state = {
                "schema_version": SCHEMA,
                "organism_id": "ferro-organism:" + uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "baseline_ancestral_source_fingerprint": None,
                "current_cortex": None,
                "current_cortex_key": None,
                "last_request_id": None,
                "last_lineage": None,
                "swap_count": 0,
                "swaps": [],
            }
            _write_json(self.path, self.state)

    @staticmethod
    def lineage_snapshot(
        *,
        neural_graph: Mapping[str, Any],
        motor: Mapping[str, Any],
        personality: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        substrate = neural_graph.get("substrate") if isinstance(neural_graph.get("substrate"), Mapping) else {}
        development = (
            substrate.get("developmental_neurogenesis")
            if isinstance(substrate.get("developmental_neurogenesis"), Mapping)
            else {}
        )
        genome = (
            development.get("developmental_genome")
            if isinstance(development.get("developmental_genome"), Mapping)
            else {}
        )
        recent_decisions = motor.get("recent_decisions") if isinstance(motor.get("recent_decisions"), list) else []
        motor_seq = max((int(row.get("seq") or 0) for row in recent_decisions if isinstance(row, Mapping)), default=0)
        habits = [
            {
                "motor_id": row.get("motor_id"),
                "meaning": row.get("meaning"),
            }
            for row in (motor.get("habits") or ())
            if isinstance(row, Mapping)
        ]
        traits = (
            dict(personality.get("traits") or {})
            if isinstance(personality, Mapping) and isinstance(personality.get("traits"), Mapping)
            else {}
        )
        return {
            "ancestral_source_fingerprint": substrate.get("source_fingerprint"),
            "neuron_count": int(substrate.get("neuron_count") or 0),
            "synthetic_neuron_count": int(development.get("synthetic_neuron_count") or 0),
            "birth_seq": int(development.get("birth_seq") or 0),
            "development_event_seq": int(development.get("event_seq") or 0),
            "developmental_genome_fingerprint": genome.get("fingerprint"),
            "developmental_genome_mutation_count": int(genome.get("mutation_count") or 0),
            "species_stage": development.get("species_stage"),
            "motor_decision_seq": int(motor.get("decision_seq") or motor_seq),
            "motor_habits": habits,
            "personality_traits": traits,
        }

    @staticmethod
    def _continuity_checks(
        *,
        baseline_ancestral: str | None,
        previous: Mapping[str, Any] | None,
        current: Mapping[str, Any],
    ) -> dict[str, Any]:
        checks: dict[str, bool] = {}
        ancestral = current.get("ancestral_source_fingerprint")
        checks["ancestral_seed_preserved"] = (
            baseline_ancestral in {None, "", ancestral} or ancestral in {None, ""}
        )
        if previous:
            for key in (
                "birth_seq",
                "development_event_seq",
                "developmental_genome_mutation_count",
                "motor_decision_seq",
            ):
                checks[key + "_monotonic"] = int(current.get(key) or 0) >= int(previous.get(key) or 0)
            previous_habits = {
                (str(row.get("motor_id")), str(row.get("meaning")))
                for row in previous.get("motor_habits") or ()
                if isinstance(row, Mapping)
            }
            current_habits = {
                (str(row.get("motor_id")), str(row.get("meaning")))
                for row in current.get("motor_habits") or ()
                if isinstance(row, Mapping)
            }
            checks["learned_habits_not_reset"] = previous_habits.issubset(current_habits)
        return {
            "status": "PRESERVED" if all(checks.values()) else "DISCONTINUITY_DETECTED",
            "checks": checks,
        }

    def observe(
        self,
        *,
        neural_graph: Mapping[str, Any],
        motor: Mapping[str, Any],
        personality: Mapping[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = _tail_json_rows(self.audit_path)
        if not rows:
            return self.projection()
        latest = rows[-1]
        request_id = str(latest.get("request_id") or "")
        if request_id and request_id == str(self.state.get("last_request_id") or ""):
            return self.projection()

        cortex = _cortex_descriptor(latest)
        cortex_key = _cortex_key(cortex)
        lineage = self.lineage_snapshot(
            neural_graph=neural_graph,
            motor=motor,
            personality=personality,
        )
        baseline = self.state.get("baseline_ancestral_source_fingerprint")
        if not baseline and lineage.get("ancestral_source_fingerprint"):
            baseline = lineage["ancestral_source_fingerprint"]
            self.state["baseline_ancestral_source_fingerprint"] = baseline

        previous_cortex = self.state.get("current_cortex")
        previous_key = self.state.get("current_cortex_key")
        previous_lineage = self.state.get("last_lineage")
        continuity = self._continuity_checks(
            baseline_ancestral=str(baseline or "") or None,
            previous=previous_lineage if isinstance(previous_lineage, Mapping) else None,
            current=lineage,
        )

        if previous_key and previous_key != cortex_key:
            swaps = list(self.state.get("swaps") or ())
            swaps.append({
                "swap_id": "cortex-swap:" + uuid4().hex,
                "observed_at": observed_at.isoformat(),
                "request_id": request_id or None,
                "from": previous_cortex,
                "to": cortex,
                "organism_id": self.state["organism_id"],
                "continuity": continuity,
                "lineage_after_swap": lineage,
            })
            self.state["swaps"] = swaps[-MAX_SWAPS:]
            self.state["swap_count"] = int(self.state.get("swap_count") or 0) + 1

        self.state.update({
            "updated_at": observed_at.isoformat(),
            "current_cortex": cortex,
            "current_cortex_key": cortex_key,
            "last_request_id": request_id or None,
            "last_lineage": lineage,
            "latest_continuity": continuity,
        })
        _write_json(self.path, self.state)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        swaps = [dict(row) for row in self.state.get("swaps") or ()]
        return {
            "schema_version": SCHEMA,
            "organism_id": self.state.get("organism_id"),
            "updated_at": self.state.get("updated_at"),
            "current_cortex": self.state.get("current_cortex"),
            "swap_count": int(self.state.get("swap_count") or 0),
            "latest_continuity": self.state.get("latest_continuity"),
            "lineage": self.state.get("last_lineage"),
            "recent_swaps": swaps[-8:],
            "identity_owner": "PERSISTENT_NEURAL_MEMORY_RUNTIME",
            "cortex_role": "REPLACEABLE_HIGH_LEVEL_REASONING",
        }


__all__ = ["CortexContinuityMonitor", "SCHEMA"]
