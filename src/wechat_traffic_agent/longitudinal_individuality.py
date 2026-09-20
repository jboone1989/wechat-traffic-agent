"""N35 longitudinal individuality ledger for Ferro.

N16 formed traits and N25 checked cortex continuity. N35 asks a different
question: after living for weeks or months, is Ferro measurably shaped by its own
history while remaining the same organism?

This ledger records bounded daily individuality snapshots from persistent neural
and autobiographical state. The replaceable LLM cortex is deliberately excluded
from the individuality fingerprint. Cortex changes are stored only as context and
are checked for continuity rather than treated as identity changes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "ferro-longitudinal-individuality.v1"
SNAPSHOT_INTERVAL_HOURS = 24.0
MONTH_LOOKBACK_DAYS = 28
MAX_SNAPSHOTS = 400
MAX_CORTEX_TRANSITIONS = 128


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + sha256(raw).hexdigest()


def _mean_abs_delta(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    keys = sorted(set(left) | set(right))
    if not keys:
        return 0.0
    values = []
    for key in keys:
        a = float(left.get(key, 0.5) or 0.0)
        b = float(right.get(key, 0.5) or 0.0)
        values.append(abs(a - b))
    return sum(values) / len(values)


def _jaccard_distance(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(str(value) for value in left), set(str(value) for value in right)
    if not a and not b:
        return 0.0
    union = a | b
    return 1.0 - (len(a & b) / max(1, len(union)))


def _cortex_key(cortex: Mapping[str, Any]) -> str | None:
    current = cortex.get("current_cortex")
    if not isinstance(current, Mapping):
        return None
    material = {
        key: current.get(key)
        for key in (
            "class",
            "provider_name",
            "model",
            "base_url",
            "selected_resource_id",
        )
    }
    return _digest(material)


class LongitudinalIndividuality:
    """Persistent evidence that one organism changes through its own history."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "longitudinal_individuality.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "organism_id": None,
            "snapshots": [],
            "cortex_transitions": [],
        }

    @staticmethod
    def _relationship_summary(social_state: Mapping[str, Any]) -> dict[str, Any]:
        relationships = social_state.get("relationships")
        relationships = relationships if isinstance(relationships, Mapping) else {}
        rows = []
        for actor, raw in relationships.items():
            if not isinstance(raw, Mapping):
                continue
            rows.append({
                "actor": str(actor),
                "interactions": int(raw.get("interactions") or 0),
                "familiarity": round(float(raw.get("familiarity") or 0.0), 6),
                "valence": round(float(raw.get("valence") or 0.0), 6),
                "approach_bias": round(float(raw.get("approach_bias") or 0.0), 6),
                "first_seen_at": raw.get("first_seen_at"),
                "last_seen_at": raw.get("last_seen_at"),
            })
        rows.sort(
            key=lambda row: (
                -int(row["interactions"]),
                -float(row["familiarity"]),
                row["actor"],
            )
        )
        top = rows[:32]
        signature_material = [
            {
                "actor": row["actor"],
                "interactions": row["interactions"],
                "familiarity": row["familiarity"],
                "valence": row["valence"],
                "approach_bias": row["approach_bias"],
            }
            for row in top
        ]
        return {
            "count": len(rows),
            "actors": [row["actor"] for row in rows],
            "top": top,
            "signature": _digest(signature_material),
            "total_interactions": sum(int(row["interactions"]) for row in rows),
        }

    @staticmethod
    def _habit_summary(motor_state: Mapping[str, Any]) -> dict[str, Any]:
        units = motor_state.get("units")
        units = units if isinstance(units, Mapping) else {}
        habits = []
        for motor_id, raw in units.items():
            if not isinstance(raw, Mapping) or str(raw.get("state") or "") != "HABIT":
                continue
            meaning = str(raw.get("meaning") or "")
            associations = raw.get("associations")
            associations = associations if isinstance(associations, Mapping) else {}
            assoc = associations.get(meaning)
            assoc = assoc if isinstance(assoc, Mapping) else {}
            habits.append({
                "motor_id": str(motor_id),
                "meaning": meaning,
                "trials": int(assoc.get("trials") or 0),
                "mean_reward": round(float(assoc.get("mean_reward") or 0.0), 6),
                "generation": raw.get("generation") or "ANCESTRAL_MOTOR_BANK",
            })
        habits.sort(key=lambda row: (row["meaning"], row["motor_id"]))
        return {
            "count": len(habits),
            "keys": [f"{row['motor_id']}|{row['meaning']}" for row in habits],
            "habits": habits[:32],
            "signature": _digest(habits),
        }

    @staticmethod
    def _dream_summary(dream_state: Mapping[str, Any]) -> dict[str, Any]:
        attractors = dream_state.get("attractors")
        attractors = attractors if isinstance(attractors, Mapping) else {}
        rows = []
        for attractor_id, raw in attractors.items():
            if not isinstance(raw, Mapping):
                continue
            rows.append({
                "attractor_id": str(attractor_id),
                "signature": str(raw.get("signature") or ""),
                "status": str(raw.get("status") or ""),
                "recurrence_count": int(raw.get("recurrence_count") or 0),
                "stability_score": round(float(raw.get("stability_score") or 0.0), 6),
                "channels": sorted(str(value) for value in raw.get("channels") or ()),
            })
        rows.sort(
            key=lambda row: (
                row["status"] != "STABLE",
                -float(row["stability_score"]),
                -int(row["recurrence_count"]),
                row["attractor_id"],
            )
        )
        stable = [row for row in rows if row["status"] == "STABLE"]
        return {
            "dream_count": int(dream_state.get("dream_count") or 0),
            "attractor_count": len(rows),
            "stable_attractor_count": len(stable),
            "stable_signatures": [row["signature"] for row in stable],
            "top_attractors": rows[:16],
            "signature": _digest(rows[:16]),
        }

    @staticmethod
    def _lineage_summary(neural_graph: Mapping[str, Any]) -> dict[str, Any]:
        substrate = neural_graph.get("substrate")
        substrate = substrate if isinstance(substrate, Mapping) else {}
        development = substrate.get("developmental_neurogenesis")
        development = development if isinstance(development, Mapping) else {}
        genome = development.get("developmental_genome")
        genome = genome if isinstance(genome, Mapping) else {}
        organs = [
            str(row.get("organ_id") or "")
            for row in development.get("organs") or ()
            if isinstance(row, Mapping) and row.get("organ_id")
        ]
        circuits = [
            str(row.get("circuit_id") or "")
            for row in development.get("circuits") or ()
            if isinstance(row, Mapping)
            and row.get("circuit_id")
            and str(row.get("state") or "") == "STABLE"
        ]
        return {
            "ancestral_source_fingerprint": substrate.get("source_fingerprint"),
            "neuron_count": int(substrate.get("neuron_count") or 0),
            "synthetic_neuron_count": int(development.get("synthetic_neuron_count") or 0),
            "birth_seq": int(development.get("birth_seq") or 0),
            "event_seq": int(development.get("event_seq") or 0),
            "genome_fingerprint": genome.get("fingerprint"),
            "genome_mutation_count": int(genome.get("mutation_count") or 0),
            "species_stage": development.get("species_stage"),
            "stable_circuit_ids": sorted(circuits),
            "organ_ids": sorted(organs),
        }

    @classmethod
    def build_snapshot(
        cls,
        *,
        now: datetime,
        organism_id: str,
        personality: Mapping[str, Any],
        social_state: Mapping[str, Any],
        motor_state: Mapping[str, Any],
        dream_state: Mapping[str, Any],
        cortex: Mapping[str, Any],
        neural_graph: Mapping[str, Any],
    ) -> dict[str, Any]:
        traits = (
            {
                str(key): round(float(value), 6)
                for key, value in personality.get("traits", {}).items()
                if isinstance(value, (int, float))
            }
            if isinstance(personality.get("traits"), Mapping)
            else {}
        )
        relationships = cls._relationship_summary(social_state)
        habits = cls._habit_summary(motor_state)
        dreams = cls._dream_summary(dream_state)
        lineage = cls._lineage_summary(neural_graph)
        individuality = {
            "personality_traits": traits,
            "relationships": {
                "count": relationships["count"],
                "actors": relationships["actors"],
                "signature": relationships["signature"],
                "total_interactions": relationships["total_interactions"],
            },
            "habits": {
                "count": habits["count"],
                "keys": habits["keys"],
                "signature": habits["signature"],
            },
            "dreams": {
                "dream_count": dreams["dream_count"],
                "stable_attractor_count": dreams["stable_attractor_count"],
                "stable_signatures": dreams["stable_signatures"],
                "signature": dreams["signature"],
            },
            "lineage": lineage,
        }
        continuity = {
            "organism_id": organism_id,
            "ancestral_source_fingerprint": lineage.get("ancestral_source_fingerprint"),
        }
        return {
            "observed_at": now.isoformat(),
            "organism_id": organism_id,
            "individuality_fingerprint": _digest(individuality),
            "continuity_fingerprint": _digest(continuity),
            "cortex_key": _cortex_key(cortex),
            "cortex_context": dict(cortex.get("current_cortex") or {})
            if isinstance(cortex.get("current_cortex"), Mapping)
            else None,
            "personality_traits": traits,
            "relationships": relationships,
            "habits": habits,
            "dreams": dreams,
            "lineage": lineage,
        }

    @staticmethod
    def compare_snapshots(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        left_traits = left.get("personality_traits") if isinstance(left.get("personality_traits"), Mapping) else {}
        right_traits = right.get("personality_traits") if isinstance(right.get("personality_traits"), Mapping) else {}
        trait_delta = _mean_abs_delta(left_traits, right_traits)

        left_rel = left.get("relationships") if isinstance(left.get("relationships"), Mapping) else {}
        right_rel = right.get("relationships") if isinstance(right.get("relationships"), Mapping) else {}
        relationship_distance = _jaccard_distance(
            left_rel.get("actors") or (),
            right_rel.get("actors") or (),
        )
        interaction_growth = abs(
            int(right_rel.get("total_interactions") or 0)
            - int(left_rel.get("total_interactions") or 0)
        )
        interaction_component = min(1.0, interaction_growth / 50.0)

        left_habits = left.get("habits") if isinstance(left.get("habits"), Mapping) else {}
        right_habits = right.get("habits") if isinstance(right.get("habits"), Mapping) else {}
        habit_distance = _jaccard_distance(
            left_habits.get("keys") or (),
            right_habits.get("keys") or (),
        )

        left_dreams = left.get("dreams") if isinstance(left.get("dreams"), Mapping) else {}
        right_dreams = right.get("dreams") if isinstance(right.get("dreams"), Mapping) else {}
        dream_distance = _jaccard_distance(
            left_dreams.get("stable_signatures") or (),
            right_dreams.get("stable_signatures") or (),
        )
        dream_growth = min(
            1.0,
            abs(
                int(right_dreams.get("dream_count") or 0)
                - int(left_dreams.get("dream_count") or 0)
            ) / 20.0,
        )

        left_lineage = left.get("lineage") if isinstance(left.get("lineage"), Mapping) else {}
        right_lineage = right.get("lineage") if isinstance(right.get("lineage"), Mapping) else {}
        lineage_delta = min(
            1.0,
            (
                abs(int(right_lineage.get("birth_seq") or 0) - int(left_lineage.get("birth_seq") or 0))
                + abs(
                    int(right_lineage.get("genome_mutation_count") or 0)
                    - int(left_lineage.get("genome_mutation_count") or 0)
                )
                + len(
                    set(right_lineage.get("organ_ids") or ())
                    ^ set(left_lineage.get("organ_ids") or ())
                )
            ) / 8.0,
        )

        score = (
            0.30 * trait_delta
            + 0.15 * relationship_distance
            + 0.10 * interaction_component
            + 0.15 * habit_distance
            + 0.10 * dream_distance
            + 0.05 * dream_growth
            + 0.15 * lineage_delta
        )
        return {
            "same_organism": (
                str(left.get("organism_id") or "")
                == str(right.get("organism_id") or "")
                and bool(left.get("organism_id"))
            ),
            "individuality_changed": (
                left.get("individuality_fingerprint")
                != right.get("individuality_fingerprint")
            ),
            "change_score": round(max(0.0, min(1.0, score)), 6),
            "trait_mean_abs_delta": round(trait_delta, 6),
            "relationship_distance": round(relationship_distance, 6),
            "interaction_growth": interaction_growth,
            "habit_distance": round(habit_distance, 6),
            "dream_distance": round(dream_distance, 6),
            "dream_count_growth": (
                int(right_dreams.get("dream_count") or 0)
                - int(left_dreams.get("dream_count") or 0)
            ),
            "lineage_change": round(lineage_delta, 6),
            "cortex_changed": left.get("cortex_key") != right.get("cortex_key"),
        }

    @staticmethod
    def _cortex_transition(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        comparison = LongitudinalIndividuality.compare_snapshots(left, right)
        left_rel = left.get("relationships") if isinstance(left.get("relationships"), Mapping) else {}
        right_rel = right.get("relationships") if isinstance(right.get("relationships"), Mapping) else {}
        left_habits = left.get("habits") if isinstance(left.get("habits"), Mapping) else {}
        right_habits = right.get("habits") if isinstance(right.get("habits"), Mapping) else {}
        left_dreams = left.get("dreams") if isinstance(left.get("dreams"), Mapping) else {}
        right_dreams = right.get("dreams") if isinstance(right.get("dreams"), Mapping) else {}
        left_lineage = left.get("lineage") if isinstance(left.get("lineage"), Mapping) else {}
        right_lineage = right.get("lineage") if isinstance(right.get("lineage"), Mapping) else {}
        checks = {
            "same_organism_id": comparison["same_organism"],
            "relationships_not_reset": set(left_rel.get("actors") or ()).issubset(
                set(right_rel.get("actors") or ())
            ),
            "habits_not_reset": set(left_habits.get("keys") or ()).issubset(
                set(right_habits.get("keys") or ())
            ),
            "dream_history_monotonic": int(right_dreams.get("dream_count") or 0)
            >= int(left_dreams.get("dream_count") or 0),
            "lineage_births_monotonic": int(right_lineage.get("birth_seq") or 0)
            >= int(left_lineage.get("birth_seq") or 0),
            "lineage_events_monotonic": int(right_lineage.get("event_seq") or 0)
            >= int(left_lineage.get("event_seq") or 0),
            "traits_not_factory_reset": comparison["trait_mean_abs_delta"] <= 0.25,
        }
        return {
            "observed_at": right.get("observed_at"),
            "from_cortex_key": left.get("cortex_key"),
            "to_cortex_key": right.get("cortex_key"),
            "from_cortex": left.get("cortex_context"),
            "to_cortex": right.get("cortex_context"),
            "status": "PRESERVED" if all(checks.values()) else "DISCONTINUITY_DETECTED",
            "checks": checks,
            "individuality_change_during_transition": comparison,
        }

    def observe(
        self,
        *,
        now: datetime | None,
        personality: Mapping[str, Any],
        social_state: Mapping[str, Any],
        motor_state: Mapping[str, Any],
        dream_state: Mapping[str, Any],
        cortex: Mapping[str, Any],
        neural_graph: Mapping[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        organism_id = str(cortex.get("organism_id") or self.state.get("organism_id") or "")
        if not organism_id:
            return {
                "status": "WAITING_FOR_ORGANISM_ID",
                **self.projection(),
            }
        if self.state.get("organism_id") not in {None, "", organism_id}:
            return {
                "status": "ORGANISM_ID_MISMATCH",
                "expected": self.state.get("organism_id"),
                "observed": organism_id,
                **self.projection(),
            }
        self.state["organism_id"] = organism_id

        snapshots = list(self.state.get("snapshots") or ())
        last = snapshots[-1] if snapshots else None
        last_at = _parse_time(last.get("observed_at")) if isinstance(last, Mapping) else None
        if (
            not force
            and last_at is not None
            and (now - last_at).total_seconds() < SNAPSHOT_INTERVAL_HOURS * 3600.0
        ):
            return {
                "status": "SNAPSHOT_NOT_DUE",
                **self.projection(),
            }

        snapshot = self.build_snapshot(
            now=now,
            organism_id=organism_id,
            personality=personality,
            social_state=social_state,
            motor_state=motor_state,
            dream_state=dream_state,
            cortex=cortex,
            neural_graph=neural_graph,
        )
        transitions = list(self.state.get("cortex_transitions") or ())
        if (
            isinstance(last, Mapping)
            and last.get("cortex_key")
            and snapshot.get("cortex_key")
            and last.get("cortex_key") != snapshot.get("cortex_key")
        ):
            transitions.append(self._cortex_transition(last, snapshot))

        snapshots.append(snapshot)
        self.state.update({
            "updated_at": now.isoformat(),
            "snapshots": snapshots[-MAX_SNAPSHOTS:],
            "cortex_transitions": transitions[-MAX_CORTEX_TRANSITIONS:],
        })
        _write(self.path, self.state)
        return {
            "status": "SNAPSHOT_RECORDED",
            "snapshot": snapshot,
            **self.projection(),
        }

    def _month_comparison(self) -> dict[str, Any]:
        snapshots = [
            row for row in self.state.get("snapshots") or ()
            if isinstance(row, Mapping) and _parse_time(row.get("observed_at")) is not None
        ]
        if not snapshots:
            return {
                "status": "NO_HISTORY",
                "history_days": 0.0,
                "ready": False,
            }
        latest = snapshots[-1]
        latest_at = _parse_time(latest.get("observed_at"))
        first_at = _parse_time(snapshots[0].get("observed_at"))
        history_days = (
            0.0 if latest_at is None or first_at is None
            else max(0.0, (latest_at - first_at).total_seconds() / 86400.0)
        )
        target = latest_at - timedelta(days=MONTH_LOOKBACK_DAYS) if latest_at else None
        baseline = None
        if target is not None:
            eligible = [
                row for row in snapshots
                if (_parse_time(row.get("observed_at")) or latest_at) <= target
            ]
            if eligible:
                baseline = eligible[-1]
        if baseline is None:
            return {
                "status": "ACCUMULATING_HISTORY",
                "history_days": round(history_days, 3),
                "required_days": MONTH_LOOKBACK_DAYS,
                "ready": False,
                "latest_individuality_fingerprint": latest.get("individuality_fingerprint"),
            }
        comparison = self.compare_snapshots(baseline, latest)
        baseline_at = _parse_time(baseline.get("observed_at"))
        elapsed_days = (
            0.0 if latest_at is None or baseline_at is None
            else (latest_at - baseline_at).total_seconds() / 86400.0
        )
        return {
            "status": "LONGITUDINAL_COMPARISON_READY",
            "history_days": round(history_days, 3),
            "elapsed_days": round(elapsed_days, 3),
            "required_days": MONTH_LOOKBACK_DAYS,
            "ready": True,
            "baseline_at": baseline.get("observed_at"),
            "latest_at": latest.get("observed_at"),
            **comparison,
        }

    @classmethod
    def compare_histories(
        cls,
        left_snapshots: Sequence[Mapping[str, Any]],
        right_snapshots: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not left_snapshots or not right_snapshots:
            return {
                "status": "INSUFFICIENT_HISTORY",
                "divergence_score": None,
            }
        left = left_snapshots[-1]
        right = right_snapshots[-1]
        comparison = cls.compare_snapshots(left, right)
        return {
            "status": "COMPARED",
            "same_organism": comparison["same_organism"],
            "divergence_score": comparison["change_score"],
            "clearly_diverged": comparison["change_score"] >= 0.20,
            "details": comparison,
        }

    def projection(self) -> dict[str, Any]:
        snapshots = [dict(row) for row in self.state.get("snapshots") or () if isinstance(row, Mapping)]
        latest = snapshots[-1] if snapshots else None
        transitions = [
            dict(row)
            for row in self.state.get("cortex_transitions") or ()
            if isinstance(row, Mapping)
        ]
        public_latest = None
        if isinstance(latest, Mapping):
            relationships = latest.get("relationships") if isinstance(latest.get("relationships"), Mapping) else {}
            habits = latest.get("habits") if isinstance(latest.get("habits"), Mapping) else {}
            dreams = latest.get("dreams") if isinstance(latest.get("dreams"), Mapping) else {}
            lineage = latest.get("lineage") if isinstance(latest.get("lineage"), Mapping) else {}
            public_latest = {
                "observed_at": latest.get("observed_at"),
                "individuality_fingerprint": latest.get("individuality_fingerprint"),
                "continuity_fingerprint": latest.get("continuity_fingerprint"),
                "personality_traits": dict(latest.get("personality_traits") or {}),
                "relationship_count": int(relationships.get("count") or 0),
                "relationship_total_interactions": int(relationships.get("total_interactions") or 0),
                "relationship_signature": relationships.get("signature"),
                "habit_count": int(habits.get("count") or 0),
                "habit_meanings": [
                    row.get("meaning")
                    for row in habits.get("habits") or ()
                    if isinstance(row, Mapping) and row.get("meaning")
                ],
                "dream_count": int(dreams.get("dream_count") or 0),
                "stable_dream_attractor_count": int(dreams.get("stable_attractor_count") or 0),
                "stable_dream_signatures": list(dreams.get("stable_signatures") or ()),
                "lineage": {
                    "synthetic_neuron_count": int(lineage.get("synthetic_neuron_count") or 0),
                    "birth_seq": int(lineage.get("birth_seq") or 0),
                    "genome_mutation_count": int(lineage.get("genome_mutation_count") or 0),
                    "species_stage": lineage.get("species_stage"),
                    "stable_circuit_count": len(lineage.get("stable_circuit_ids") or ()),
                    "organ_count": len(lineage.get("organ_ids") or ()),
                },
                "cortex_key": latest.get("cortex_key"),
            }
        return {
            "schema_version": SCHEMA,
            "organism_id": self.state.get("organism_id"),
            "updated_at": self.state.get("updated_at"),
            "snapshot_count": len(snapshots),
            "latest": public_latest,
            "month_comparison": self._month_comparison(),
            "cortex_transition_count": len(transitions),
            "recent_cortex_transitions": transitions[-8:],
            "individuality_owner": "PERSISTENT_NEURAL_MEMORY_HISTORY",
            "cortex_in_individuality_fingerprint": False,
            "raw_relationship_ids_public": False,
        }


__all__ = [
    "LongitudinalIndividuality",
    "MAX_SNAPSHOTS",
    "MONTH_LOOKBACK_DAYS",
    "SCHEMA",
    "SNAPSHOT_INTERVAL_HOURS",
]
