"""Read-only production acceptance audit for Ferro Open-Ended Development.

This module deliberately separates two claims:

1. The engineering machinery for N26-N36 exists in code.
2. Ferro has actually produced the required persistent behavior in its live state.

Only state evidence can satisfy the second claim.  The auditor never creates or
repairs evidence and never advances a stage.
"""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Mapping


SCHEMA = "ferro-open-development-acceptance.v1"
STAGES = tuple(f"N{index}" for index in range(26, 37))


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _stage(
    *,
    accepted: bool,
    status: str,
    evidence: Mapping[str, Any] | None = None,
    missing: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "production_behavior_accepted": bool(accepted),
        "status": str(status),
        "evidence": dict(evidence or {}),
        "missing": list(missing if not accepted else ()),
    }


class OpenDevelopmentAcceptance:
    """Passive N26-N36 evidence reader."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.application = self.state_root / "application"

    def _agency(self) -> dict[str, Any]:
        return _read(self.application / "developmental_agency.json")

    def _development(self) -> dict[str, Any]:
        return _read(self.application / "synthetic_neural_development.json")

    def _n26(self, agency: Mapping[str, Any]) -> dict[str, Any]:
        needs = [
            row for row in (agency.get("needs") or {}).values()
            if isinstance(row, Mapping)
        ]
        evidenced = [
            row for row in needs
            if int(row.get("evidence_count") or len(row.get("evidence") or ())) > 0
            and row.get("llm_used_for_detection") is False
        ]
        accepted = bool(evidenced)
        return _stage(
            accepted=accepted,
            status="DEVELOPMENT_NEED_OBSERVED" if accepted else "WAITING_FOR_REAL_NEED",
            evidence={
                "need_count": len(needs),
                "evidenced_non_llm_need_count": len(evidenced),
                "need_ids": [str(row.get("need_id") or "") for row in evidenced[-8:]],
            },
            missing=("persistent real outcome -> non-LLM DevelopmentNeed",),
        )

    def _n27(self, agency: Mapping[str, Any]) -> dict[str, Any]:
        proposals = [
            row for row in (agency.get("proposals") or {}).values()
            if isinstance(row, Mapping)
        ]
        valid = [
            row for row in proposals
            if row.get("llm_used_for_proposal") is False
            and row.get("need_id")
            and row.get("experiment_contract")
        ]
        accepted = bool(valid)
        return _stage(
            accepted=accepted,
            status="BOUNDED_PROPOSAL_OBSERVED" if accepted else "WAITING_FOR_PROPOSAL",
            evidence={
                "proposal_count": len(proposals),
                "bounded_non_llm_proposal_count": len(valid),
                "proposal_ids": [str(row.get("proposal_id") or "") for row in valid[-8:]],
            },
            missing=("DevelopmentNeed -> bounded non-LLM proposal",),
        )

    def _n28(self, agency: Mapping[str, Any]) -> dict[str, Any]:
        worlds = [
            row for row in (agency.get("worlds") or {}).values()
            if isinstance(row, Mapping)
        ]
        ferro = []
        for row in worlds:
            safety = row.get("safety") if isinstance(row.get("safety"), Mapping) else {}
            if (
                str(row.get("world_id") or "").startswith("WORLD_")
                and row.get("seed")
                and row.get("sensor_channels")
                and row.get("actions")
                and safety.get("brain_lab_only") is True
                and safety.get("no_external_actions") is True
                and safety.get("no_llm_motor_loop") is True
            ):
                ferro.append(row)
        accepted = bool(ferro)
        return _stage(
            accepted=accepted,
            status="FERRO_BORN_WORLD_OBSERVED" if accepted else "WAITING_FOR_SELF_GENERATED_WORLD",
            evidence={
                "world_count": len(worlds),
                "bounded_ferro_born_world_count": len(ferro),
                "world_ids": [str(row.get("world_id") or "") for row in ferro[-8:]],
            },
            missing=("bounded reproducible Ferro-born training world",),
        )

    def _n29(self, agency: Mapping[str, Any]) -> dict[str, Any]:
        worlds = [
            row for row in (agency.get("worlds") or {}).values()
            if isinstance(row, Mapping)
        ]
        selected = []
        for row in worlds:
            evaluation = (
                row.get("transfer_evaluation")
                if isinstance(row.get("transfer_evaluation"), Mapping)
                else {}
            )
            arms = evaluation.get("arms") if isinstance(evaluation.get("arms"), Mapping) else {}
            treatment = arms.get("TREATMENT") if isinstance(arms.get("TREATMENT"), Mapping) else {}
            control = arms.get("CONTROL") if isinstance(arms.get("CONTROL"), Mapping) else {}
            if (
                bool(evaluation.get("causal_claim_ready"))
                and str(evaluation.get("decision") or "") in {"ADOPT", "REJECT", "REVISE"}
                and int(treatment.get("count") or 0) >= 4
                and int(control.get("count") or 0) >= 4
            ):
                selected.append(row)
        accepted = bool(selected)
        return _stage(
            accepted=accepted,
            status="REALITY_SELECTION_OBSERVED" if accepted else "WAITING_FOR_4X4_REALITY_TRIAL",
            evidence={
                "selected_world_count": len(selected),
                "decisions": [
                    {
                        "world_id": row.get("world_id"),
                        "decision": (row.get("transfer_evaluation") or {}).get("decision"),
                        "effect": (row.get("transfer_evaluation") or {}).get("estimated_real_world_effect"),
                    }
                    for row in selected[-8:]
                ],
            },
            missing=("prospective 4/4 real target-task treatment/control selection",),
        )

    def _n30(self) -> dict[str, Any]:
        dreams = _read(self.application / "neural_dreams.json")
        attractors = [
            row for row in (dreams.get("attractors") or {}).values()
            if isinstance(row, Mapping)
        ]
        stable = [
            row for row in attractors
            if str(row.get("status") or "") == "STABLE"
            and int(row.get("recurrence_count") or 0) >= 3
            and int(row.get("consolidation_epoch_count") or 0) >= 3
        ]
        accepted = bool(stable)
        return _stage(
            accepted=accepted,
            status="STABLE_DREAM_ATTRACTOR_OBSERVED" if accepted else "WAITING_FOR_MULTI_EPOCH_ATTRACTOR",
            evidence={
                "dream_count": int(dreams.get("dream_count") or 0),
                "stable_multi_epoch_attractor_count": len(stable),
                "attractor_ids": [str(row.get("attractor_id") or "") for row in stable[-8:]],
            },
            missing=("3+ dreams across 3+ consolidation epochs -> stable attractor",),
        )

    def _n31(self) -> dict[str, Any]:
        dreams = _read(self.application / "neural_dreams.json")
        attractors = [
            row for row in (dreams.get("attractors") or {}).values()
            if isinstance(row, Mapping) and str(row.get("status") or "") == "STABLE"
        ]
        candidates = [
            row.get("goal_candidate")
            for row in attractors
            if isinstance(row.get("goal_candidate"), Mapping)
            and (row.get("goal_candidate") or {}).get("llm_used_for_birth") is False
        ]
        accepted = bool(candidates)
        return _stage(
            accepted=accepted,
            status="DREAM_BORN_GOAL_OBSERVED" if accepted else "WAITING_FOR_DREAM_GOAL_BIRTH",
            evidence={
                "dream_born_goal_candidate_count": len(candidates),
                "candidate_ids": [str(row.get("goal_candidate_id") or row.get("candidate_id") or "") for row in candidates[-8:]],
            },
            missing=("stable attractor -> non-LLM endogenous goal candidate",),
        )

    def _n32(self) -> dict[str, Any]:
        extensions = _read(self.application / "neural_extensions.json")
        rows = [
            row for row in (extensions.get("extensions") or {}).values()
            if isinstance(row, Mapping)
        ]
        active_sensors = [
            row for row in rows
            if row.get("extension_type") == "SENSOR"
            and row.get("status") == "ACTIVE_EXPERIMENT"
            and int(row.get("sample_count") or 0) > 0
        ]
        motors = [
            row for row in rows
            if row.get("extension_type") == "MOTOR_CAPACITY"
            and row.get("status") == "ACTIVE_EXPERIMENT"
            and bool(row.get("born_motor_ids"))
        ]
        accepted = bool(active_sensors or motors)
        return _stage(
            accepted=accepted,
            status="SELF_EXTENSION_OBSERVED" if accepted else "WAITING_FOR_REAL_BODY_EXTENSION",
            evidence={
                "sampled_sensor_extension_count": len(active_sensors),
                "motor_capacity_extension_count": len(motors),
                "ferro_born_motor_unit_count": sum(len(row.get("born_motor_ids") or ()) for row in motors),
            },
            missing=("real sampled sensor extension or Ferro-born unsemantic motor capacity",),
        )

    def _n33(self) -> dict[str, Any]:
        search = _read(self.application / "developmental_architecture.json")
        trials = [
            row for row in search.get("trials") or ()
            if isinstance(row, Mapping)
            and row.get("status") == "SETTLED"
            and isinstance(row.get("target_outcome"), Mapping)
            and (row.get("target_outcome") or {}).get("utility") is not None
        ]
        arms = {
            name: sum(row.get("assignment") == name for row in trials)
            for name in ("ADAPTIVE_FAST", "CONSERVATIVE_STABLE")
        }
        selected = search.get("selected_variant")
        history = [
            row for row in search.get("selection_history") or ()
            if isinstance(row, Mapping)
            and row.get("selected_variant") == selected
            and row.get("causal_source") == "REAL_TARGET_OUTCOMES"
        ]
        accepted = bool(
            search.get("schema_version") == "ferro-developmental-architecture.v1"
            and selected in {"ADAPTIVE_FAST", "CONSERVATIVE_STABLE"}
            and arms["ADAPTIVE_FAST"] >= 4
            and arms["CONSERVATIVE_STABLE"] >= 4
            and history
        )
        return _stage(
            accepted=accepted,
            status="DEVELOPMENTAL_ARCHITECTURE_SELECTED" if accepted else "WAITING_FOR_ARCHITECTURE_SELECTION",
            evidence={
                "selected_variant": selected,
                "settled_counts": arms,
                "real_target_outcome_count": len(trials),
                "trial_count": len(search.get("trials") or ()),
                "selection_history_count": len(history),
                "selected_at": search.get("selected_at"),
            },
            missing=("4/4 prospective real target outcomes -> selected inherited developmental architecture",),
        )

    def _n34(self) -> dict[str, Any]:
        handoff = _read(self.application / "neural_organ_handoff.json")
        trials = [
            row for row in handoff.get("trials") or ()
            if isinstance(row, Mapping)
            and row.get("phase") == "EVALUATING"
            and row.get("status") == "SETTLED"
        ]
        neural_rows = [row for row in trials if row.get("arm") == "NEURAL_ORGAN"]
        python_rows = [row for row in trials if row.get("arm") == "PYTHON_BASELINE"]
        neural = len(neural_rows)
        python = len(python_rows)
        neural_values = [
            float((row.get("outcome") or {}).get("utility") or 0.0)
            for row in neural_rows
        ]
        python_values = [
            float((row.get("outcome") or {}).get("utility") or 0.0)
            for row in python_rows
        ]
        neural_mean = (
            None if not neural_values else sum(neural_values) / len(neural_values)
        )
        python_mean = (
            None if not python_values else sum(python_values) / len(python_values)
        )
        effect = (
            None if neural_mean is None or python_mean is None
            else neural_mean - python_mean
        )
        demonstrated = bool(
            handoff.get("handoff_at")
            and handoff.get("selected_organ_id")
            and neural >= 4
            and python >= 4
            and effect is not None
            and effect >= -0.08
        )
        return _stage(
            accepted=demonstrated,
            status=(
                "ORGAN_HANDOFF_DEMONSTRATED"
                if demonstrated
                else "WAITING_FOR_PROSPECTIVE_ORGAN_HANDOFF"
            ),
            evidence={
                "phase": handoff.get("phase"),
                "selected_organ_id": handoff.get("selected_organ_id"),
                "handoff_at": handoff.get("handoff_at"),
                "initial_neural_settled": neural,
                "initial_python_settled": python,
                "initial_mean_neural_utility": neural_mean,
                "initial_mean_python_utility": python_mean,
                "initial_effect_neural_minus_python": effect,
                "noninferior_margin": 0.08,
                "rollback_at": handoff.get("rollback_at"),
                "rollback_reason": handoff.get("rollback_reason"),
            },
            missing=("active emergent organ + prospective 4/4 non-inferiority -> handoff",),
        )

    def _n35(self) -> dict[str, Any]:
        from .longitudinal_individuality import LongitudinalIndividuality

        longitudinal = LongitudinalIndividuality(self.state_root).projection()
        month = (
            longitudinal.get("month_comparison")
            if isinstance(longitudinal.get("month_comparison"), Mapping)
            else {}
        )
        raw_longitudinal = _read(
            self.application / "longitudinal_individuality.json"
        )
        preserved_swaps = [
            row for row in raw_longitudinal.get("cortex_transitions") or ()
            if isinstance(row, Mapping) and row.get("status") == "PRESERVED"
        ]
        accepted = bool(
            month.get("ready")
            and month.get("same_organism")
            and month.get("individuality_changed")
            and preserved_swaps
        )
        return _stage(
            accepted=accepted,
            status="LONGITUDINAL_INDIVIDUALITY_OBSERVED" if accepted else "ACCUMULATING_LONGITUDINAL_EVIDENCE",
            evidence={
                "snapshot_count": longitudinal.get("snapshot_count"),
                "month_status": month.get("status"),
                "history_days": month.get("history_days"),
                "month_change_score": month.get("change_score"),
                "preserved_cortex_transition_count": len(preserved_swaps),
            },
            missing=(
                "28+ day same-organism individuality change",
                "at least one preserved real cortex transition",
            ),
        )

    def _n36(self) -> dict[str, Any]:
        evolution = _read(self.application / "open_ended_evolution.json")
        rows = [
            row for row in (evolution.get("cycles") or {}).values()
            if isinstance(row, Mapping)
        ]
        evolved = [
            row for row in rows
            if (
                row.get("status") == "EVOLVED"
                and row.get("ferro_born_world_verified") is True
                and row.get("causal_claim_ready") is True
                and row.get("completion_kind")
                in {
                    "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT",
                    "WORLD_PROVENANCE_EMERGENT_ORGAN",
                }
            )
        ]
        accepted = bool(evolved)
        return _stage(
            accepted=accepted,
            status="OPEN_ENDED_EVOLUTION_OBSERVED" if accepted else "WAITING_FOR_REAL_N36_EVOLUTION",
            evidence={
                "cycle_count": len(rows),
                "evolved_cycle_count": len(evolved),
                "latest_completion_kind": evolved[-1].get("completion_kind") if evolved else None,
                "latest_world_id": evolved[-1].get("world_id") if evolved else None,
            },
            missing=("ADOPTed Ferro-born world -> exact world-linked stable circuit or organ",),
        )

    def projection(self) -> dict[str, Any]:
        agency = self._agency()
        development = self._development()
        stages = {
            "N26": self._n26(agency),
            "N27": self._n27(agency),
            "N28": self._n28(agency),
            "N29": self._n29(agency),
            "N30": self._n30(),
            "N31": self._n31(),
            "N32": self._n32(),
            "N33": self._n33(),
            "N34": self._n34(),
            "N35": self._n35(),
            "N36": self._n36(),
        }
        accepted = [stage for stage in STAGES if stages[stage]["production_behavior_accepted"]]
        missing = [stage for stage in STAGES if not stages[stage]["production_behavior_accepted"]]
        contiguous = None
        for stage in STAGES:
            if not stages[stage]["production_behavior_accepted"]:
                break
            contiguous = stage
        return {
            "schema_version": SCHEMA,
            "scope": "N26-N36",
            "engineering_frontier": "N36",
            "production_behavior_accepted_count": len(accepted),
            "production_behavior_missing_count": len(missing),
            "accepted_stages": accepted,
            "missing_stages": missing,
            "production_accepted_contiguously_through": contiguous,
            "stages": stages,
            "audit_policy": (
                "read-only state evidence; engineering implementation is not "
                "treated as production behavioral acceptance"
            ),
        }


__all__ = ["OpenDevelopmentAcceptance", "SCHEMA", "STAGES"]
