"""N36 open-ended evolution closure for Ferro.

This module closes the existing endogenous development chain:

    Need -> Proposal -> Ferro-born World -> Training -> Reality Trial
         -> Adopt/Reject/Revise -> Structural Emergence

A successful reality trial is necessary but not sufficient.  An N36 cycle reaches
EVOLVED only when the adopted, self-generated world has exact provenance into a
persistent neural structure that can support a capability: an emergent organ or
a STABLE circuit whose member neurons were born from that world's Brain Lab
events.  A single world-linked neuron is visible structural development, but is
not by itself called a new capability.

If adoption is real but no qualifying structure exists yet, bounded
consolidation sessions may continue the adopted world.  No synthetic success is
invented when that budget is exhausted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "ferro-open-ended-evolution.v1"
MAX_CYCLES = 64
MAX_CONSOLIDATION_SESSIONS = 8
DEFAULT_CONSOLIDATION_STEPS = 24


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
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _id(*parts: str) -> str:
    raw = "\0".join(str(value) for value in parts).encode()
    return sha256(raw).hexdigest()[:20]


class OpenEndedEvolution:
    """Evidence-bound closure of Ferro's developmental loop."""

    def __init__(self, state_root: Path, *, brain_lab: Any, agency: Any) -> None:
        self.state_root = Path(state_root)
        self.brain_lab = brain_lab
        self.agency = agency
        self.path = self.state_root / "application" / "open_ended_evolution.json"
        self.development_path = (
            self.state_root / "application" / "synthetic_neural_development.json"
        )
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "cycles": {},
        }

    @staticmethod
    def _world_prefix(world_id: str) -> str:
        return f"brain-lab:{str(world_id).lower()}:"

    @staticmethod
    def _world_evaluation(world: Mapping[str, Any]) -> dict[str, Any] | None:
        raw = world.get("transfer_evaluation")
        return dict(raw) if isinstance(raw, Mapping) else None

    def _structural_evidence(self, world_id: str) -> dict[str, Any]:
        development = _read(self.development_path)
        neurons = (
            development.get("neurons")
            if isinstance(development.get("neurons"), Mapping)
            else {}
        )
        prefix = self._world_prefix(world_id)
        linked_neurons: dict[str, dict[str, Any]] = {}
        for neuron_id, raw in neurons.items():
            if not isinstance(raw, Mapping) or str(raw.get("state") or "") == "DEAD":
                continue
            trigger = (
                raw.get("developmental_trigger")
                if isinstance(raw.get("developmental_trigger"), Mapping)
                else {}
            )
            refs = [
                str(value)
                for value in trigger.get("recent_source_events") or ()
                if value
            ]
            exact_refs = [value for value in refs if value.startswith(prefix)]
            if not exact_refs:
                continue
            linked_neurons[str(neuron_id)] = {
                "neuron_id": str(neuron_id),
                "ordinal": int(raw.get("ordinal") or 0),
                "generation": int(raw.get("generation") or 0),
                "state": str(raw.get("state") or ""),
                "born_at": raw.get("born_at"),
                "parent_channels": list(raw.get("parent_channels") or ()),
                "source_event_refs": exact_refs[-8:],
            }

        linked_ids = set(linked_neurons)
        circuits = (
            development.get("circuits")
            if isinstance(development.get("circuits"), Mapping)
            else {}
        )
        linked_circuits: dict[str, dict[str, Any]] = {}
        for circuit_id, raw in circuits.items():
            if not isinstance(raw, Mapping) or str(raw.get("state") or "") == "DISSOLVED":
                continue
            members = {str(value) for value in raw.get("members") or ()}
            world_members = sorted(members & linked_ids)
            if len(world_members) < 2:
                continue
            linked_circuits[str(circuit_id)] = {
                "circuit_id": str(circuit_id),
                "state": str(raw.get("state") or ""),
                "member_count": len(members),
                "world_linked_member_count": len(world_members),
                "world_linked_members": world_members[:32],
                "stability_events": int(raw.get("stability_events") or 0),
                "active_events": int(raw.get("active_events") or 0),
                "human_function_label": raw.get("human_function_label"),
            }

        organs = (
            development.get("organs")
            if isinstance(development.get("organs"), Mapping)
            else {}
        )
        linked_organs = []
        for organ_id, raw in organs.items():
            if not isinstance(raw, Mapping):
                continue
            circuit_id = str(raw.get("origin_circuit_id") or "")
            if (
                str(raw.get("state") or "") == "EMERGENT_ORGAN"
                and circuit_id in linked_circuits
            ):
                linked_organs.append({
                    "organ_id": str(organ_id),
                    "origin_circuit_id": circuit_id,
                    "state": str(raw.get("state") or ""),
                    "member_count": int(raw.get("member_count") or 0),
                    "function_label": raw.get("function_label"),
                    "function_assignment": raw.get("function_assignment"),
                })
        linked_organs.sort(key=lambda row: row["organ_id"])
        stable_circuits = [
            row
            for row in linked_circuits.values()
            if row["state"] == "STABLE"
        ]
        stable_circuits.sort(key=lambda row: row["circuit_id"])

        if linked_organs:
            completion_kind = "WORLD_PROVENANCE_EMERGENT_ORGAN"
        elif stable_circuits:
            completion_kind = "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT"
        else:
            completion_kind = None

        return {
            "development_state_present": bool(development),
            "world_event_prefix": prefix,
            "world_linked_neuron_count": len(linked_neurons),
            "world_linked_neurons": sorted(
                linked_neurons.values(),
                key=lambda row: (row["ordinal"], row["neuron_id"]),
            )[:32],
            "world_linked_circuit_count": len(linked_circuits),
            "world_linked_circuits": sorted(
                linked_circuits.values(),
                key=lambda row: row["circuit_id"],
            )[:16],
            "stable_world_linked_circuit_count": len(stable_circuits),
            "world_linked_organ_count": len(linked_organs),
            "world_linked_organs": linked_organs[:8],
            "completion_kind": completion_kind,
            "semantic_capability_claim": False,
        }

    @staticmethod
    def _cycle_status_for_decision(decision: str) -> str:
        return {
            "REJECT": "REJECTED_BY_REALITY",
            "REVISE": "REVISION_REQUIRED",
            "ADOPT": "ADOPTED_AWAITING_EMERGENCE",
        }.get(str(decision), "AWAITING_REALITY_DECISION")

    def _ensure_cycle(
        self,
        *,
        world: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        cycles = dict(self.state.get("cycles") or {})
        world_id = str(world.get("world_id") or "")
        cycle_id = "evolution-cycle:" + _id(
            str(world.get("need_id") or ""),
            str(world.get("proposal_id") or ""),
            world_id,
        )
        cycle = dict(cycles.get(cycle_id) or {})
        if not cycle:
            cycle = {
                "cycle_id": cycle_id,
                "created_at": now.isoformat(),
                "need_id": world.get("need_id"),
                "proposal_id": world.get("proposal_id"),
                "world_id": world_id,
                "target_task": world.get("target_task"),
                "world_origin": "FERRO_BORN_DEVELOPMENTAL_WORLD",
                "world_seed": world.get("seed"),
                "world_sensor_channels": list(world.get("sensor_channels") or ()),
                "world_actions": list(world.get("actions") or ()),
                "consolidation_sessions": [],
                "consolidation_session_count": 0,
                "structural_evidence": {},
                "status": "AWAITING_REALITY_DECISION",
                "completion_kind": None,
                "completed_at": None,
                "llm_used_for_cycle_selection": False,
            }
        decision = str(evaluation.get("decision") or "CONTINUE_EXPERIMENT")
        ferro_born_world = bool(
            world_id.startswith("WORLD_")
            and world.get("seed")
            and world.get("sensor_channels")
            and world.get("actions")
        )
        cycle["ferro_born_world_verified"] = ferro_born_world
        cycle["reality_evaluation"] = dict(evaluation)
        cycle["reality_decision"] = decision
        cycle["real_world_effect"] = evaluation.get("estimated_real_world_effect")
        cycle["causal_claim_ready"] = bool(evaluation.get("causal_claim_ready"))
        cycle["provenance_chain"] = {
            "need_id": world.get("need_id"),
            "proposal_id": world.get("proposal_id"),
            "world_id": world_id,
            "world_seed": world.get("seed"),
            "training_trial_count": int(world.get("trial_count") or 0),
            "training_steps": int(world.get("training_steps") or 0),
            "changed_synapses": int(world.get("changed_synapses") or 0),
            "reality_decision": decision,
            "real_world_effect": evaluation.get("estimated_real_world_effect"),
            "causal_claim_ready": bool(evaluation.get("causal_claim_ready")),
        }
        cycle["updated_at"] = now.isoformat()

        evidence = self._structural_evidence(world_id)
        cycle["structural_evidence"] = evidence
        cycle["provenance_chain"]["structural_completion_kind"] = evidence.get("completion_kind")
        cycle["provenance_chain"]["world_linked_neuron_count"] = int(
            evidence.get("world_linked_neuron_count") or 0
        )
        cycle["provenance_chain"]["stable_world_linked_circuit_count"] = int(
            evidence.get("stable_world_linked_circuit_count") or 0
        )
        cycle["provenance_chain"]["world_linked_organ_count"] = int(
            evidence.get("world_linked_organ_count") or 0
        )
        completion_kind = evidence.get("completion_kind")
        if not ferro_born_world:
            cycle["status"] = "INELIGIBLE_NON_FERRO_WORLD"
            cycle["completion_kind"] = None
            cycle["completed_at"] = None
            cycle["emergence_claim"] = None
        elif (
            decision == "ADOPT"
            and bool(evaluation.get("causal_claim_ready"))
            and completion_kind
        ):
            cycle["status"] = "EVOLVED"
            cycle["completion_kind"] = completion_kind
            cycle["completed_at"] = cycle.get("completed_at") or now.isoformat()
            cycle["emergence_claim"] = (
                "ADOPTED_REAL_WORLD_TRANSFER_WITH_EXACT_WORLD_NEURAL_PROVENANCE"
            )
        elif decision == "ADOPT":
            if int(cycle.get("consolidation_session_count") or 0) >= MAX_CONSOLIDATION_SESSIONS:
                cycle["status"] = "CONSOLIDATION_BUDGET_EXHAUSTED"
            else:
                cycle["status"] = "ADOPTED_AWAITING_EMERGENCE"
            cycle["completion_kind"] = None
            cycle["completed_at"] = None
            cycle["emergence_claim"] = None
        else:
            cycle["status"] = self._cycle_status_for_decision(decision)
            cycle["completion_kind"] = None
            cycle["completed_at"] = None
            cycle["emergence_claim"] = None

        cycles[cycle_id] = cycle
        if len(cycles) > MAX_CYCLES:
            keep = sorted(
                cycles.values(),
                key=lambda row: (
                    row.get("status") != "EVOLVED",
                    str(row.get("updated_at") or ""),
                ),
                reverse=True,
            )[:MAX_CYCLES]
            cycles = {str(row["cycle_id"]): row for row in keep}
        self.state["cycles"] = cycles
        return cycle

    def refresh(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        worlds = (
            self.agency.state.get("worlds")
            if isinstance(self.agency.state.get("worlds"), Mapping)
            else {}
        )
        for raw in worlds.values():
            if not isinstance(raw, Mapping):
                continue
            evaluation = self._world_evaluation(raw)
            if evaluation is None:
                continue
            decision = str(evaluation.get("decision") or "")
            if decision not in {"ADOPT", "REJECT", "REVISE"}:
                continue
            self._ensure_cycle(world=raw, evaluation=evaluation, now=now)
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return self.projection()

    def next_consolidation(self) -> dict[str, Any] | None:
        rows = [
            dict(row)
            for row in (self.state.get("cycles") or {}).values()
            if isinstance(row, Mapping)
            and row.get("status") == "ADOPTED_AWAITING_EMERGENCE"
            and int(row.get("consolidation_session_count") or 0)
            < MAX_CONSOLIDATION_SESSIONS
        ]
        if not rows:
            return None
        rows.sort(
            key=lambda row: (
                int(row.get("consolidation_session_count") or 0),
                str(row.get("created_at") or ""),
                str(row.get("cycle_id") or ""),
            )
        )
        return rows[0]

    def run_consolidation(
        self,
        *,
        now: datetime | None = None,
        steps: int = DEFAULT_CONSOLIDATION_STEPS,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cycle = self.next_consolidation()
        if cycle is None:
            return {"status": "NO_EVOLUTION_CONSOLIDATION_DUE"}
        cycle_id = str(cycle["cycle_id"])
        world_id = str(cycle["world_id"])
        result = self.brain_lab.run_session(
            environment=world_id,
            steps=max(1, min(64, int(steps))),
            now=now,
        )

        cycles = dict(self.state.get("cycles") or {})
        current = dict(cycles[cycle_id])
        sessions = [
            dict(row)
            for row in current.get("consolidation_sessions") or ()
            if isinstance(row, Mapping)
        ]
        session_seq = int(current.get("consolidation_session_count") or 0) + 1
        sessions.append({
            "session_seq": session_seq,
            "observed_at": now.isoformat(),
            "world_id": world_id,
            "steps": int(result.get("steps") or 0),
            "reward": float(result.get("reward") or 0.0),
            "changed_synapses": int(result.get("changed_synapses") or 0),
            "status": str(result.get("status") or "UNKNOWN"),
        })
        current["consolidation_sessions"] = sessions[-MAX_CONSOLIDATION_SESSIONS:]
        current["consolidation_session_count"] = session_seq
        current["last_consolidation_at"] = now.isoformat()
        cycles[cycle_id] = current
        self.state["cycles"] = cycles
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)

        # The session ran on the same production nervous system. Re-read the
        # developmental state only after training so newly born structures can
        # satisfy the emergence gate.
        projection = self.refresh(now=now)
        updated = dict((self.state.get("cycles") or {}).get(cycle_id) or {})
        return {
            "status": (
                "EVOLVED"
                if updated.get("status") == "EVOLVED"
                else str(updated.get("status") or "UNKNOWN")
            ),
            "cycle_id": cycle_id,
            "world_id": world_id,
            "training": result,
            "cycle": updated,
            "projection": projection,
        }

    def projection(self) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in (self.state.get("cycles") or {}).values()
            if isinstance(row, Mapping)
        ]
        rows.sort(
            key=lambda row: (
                str(row.get("updated_at") or ""),
                str(row.get("cycle_id") or ""),
            ),
            reverse=True,
        )
        evolved = [row for row in rows if row.get("status") == "EVOLVED"]
        awaiting = [
            row
            for row in rows
            if row.get("status") == "ADOPTED_AWAITING_EMERGENCE"
        ]
        exhausted = [
            row
            for row in rows
            if row.get("status") == "CONSOLIDATION_BUDGET_EXHAUSTED"
        ]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "cycle_count": len(rows),
            "evolved_cycle_count": len(evolved),
            "awaiting_emergence_count": len(awaiting),
            "exhausted_cycle_count": len(exhausted),
            "latest_evolved_cycle": evolved[0] if evolved else None,
            "recent_cycles": rows[:16],
            "next_consolidation": self.next_consolidation(),
            "max_consolidation_sessions": MAX_CONSOLIDATION_SESSIONS,
            "completion_requires_real_world_adoption": True,
            "completion_requires_ferro_born_world": True,
            "completion_requires_world_linked_stable_circuit_or_organ": True,
            "single_neuron_is_not_called_a_new_capability": True,
            "llm_used_for_cycle_selection": False,
            "semantic_capability_claim": False,
        }


__all__ = [
    "DEFAULT_CONSOLIDATION_STEPS",
    "MAX_CONSOLIDATION_SESSIONS",
    "OpenEndedEvolution",
    "SCHEMA",
]
