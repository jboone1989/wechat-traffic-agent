from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from agent_runtime.evolution import (
    EvidenceClass,
    EvidenceMetric,
    ExperimentMetric,
    MetricDirection,
    MutationRequirement,
)

from .memory_evolution import MemoryEvolutionEngine, architecture_fingerprint
from .memory_observation import MemoryLifeObservatory


CANDIDATE_SPECS = {
    "RECALL_FAILURE": ("RECALL_WEIGHT_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.recall.policy"),
    "MISSED_RECALL": ("RECALL_WEIGHT_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.recall.policy"),
    "WRONG_RECALL": ("RECALL_WEIGHT_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.recall.policy"),
    "NO_USEFUL_MEMORY": ("RECALL_WEIGHT_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.recall.policy"),
    "STALE_MEMORY": ("DECAY_PARAMETER_CHANGE", MutationRequirement.JUDGMENT_RULE, "memory.decay.policy"),
    "STALE_RECALL": ("DECAY_PARAMETER_CHANGE", MutationRequirement.JUDGMENT_RULE, "memory.decay.policy"),
    "CONTRADICTION": ("INDEX_CONFIGURATION_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.index.configuration"),
    "CONFLICT": ("INDEX_CONFIGURATION_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.index.configuration"),
    "PROCEDURE_FAILURE": ("SKILL_VARIANT_PROMOTION", MutationRequirement.JUDGMENT_RULE, "memory.procedure.policy"),
    "INDEX_COST": ("INDEX_CONFIGURATION_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.index.configuration"),
    "CONTEXT_WASTE": ("INDEX_CONFIGURATION_CHANGE", MutationRequirement.RETRIEVAL_FAILURE, "memory.index.configuration"),
}
ALLOWED_CANDIDATE_TYPES = frozenset({
    "RECALL_WEIGHT_CHANGE",
    "DECAY_PARAMETER_CHANGE",
    "SALIENCE_WEIGHT_CHANGE",
    "SKILL_VARIANT_PROMOTION",
    "INDEX_CONFIGURATION_CHANGE",
})


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MemoryRuntimeEvolutionBridge:
    """Project measured memory failures into Runtime Evolution candidates.

    Candidate scope is intentionally narrow. This bridge never mutates schemas, code, authority,
    secrets, or governance. Runtime owns candidate/experiment state.
    """

    MIN_FAILURES_FOR_CANDIDATE = 2

    def __init__(self, *, runtime: Any, state_root: Path) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.observatory = MemoryLifeObservatory(self.state_root)
        self.memory_evolution = MemoryEvolutionEngine(self.state_root)
        self.root = self.state_root / "application" / "memory_system" / "runtime_evolution_bridge"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"

    def _state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"processed_refs": [], "candidates": {}}
        return value if isinstance(value, dict) else {"processed_refs": [], "candidates": {}}

    def _write_state(self, row: Mapping[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def _sources(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.observatory.rows(limit=5000):
            if row.get("event_type") != "FAILURE" or not row.get("failure_kind"):
                continue
            rows.append({
                "source_ref": str(row.get("memory_use_id") or ""),
                "kind": str(row.get("failure_kind") or ""),
                "detail": str(row.get("detail") or ""),
                "observed_at": str(row.get("observed_at") or ""),
                "evidence_refs": list(row.get("evidence_refs") or []),
            })
        for row in self.memory_evolution.recent_signals(limit=1000):
            rows.append({
                "source_ref": str(row.get("signal_id") or ""),
                "kind": str(row.get("kind") or ""),
                "detail": str(row.get("detail") or ""),
                "observed_at": str(row.get("observed_at") or ""),
                "evidence_refs": [str(row.get("evidence_ref") or "")],
            })
        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["source_ref"] and row["kind"] in CANDIDATE_SPECS:
                dedup[row["source_ref"]] = row
        return list(dedup.values())

    def sync(self) -> dict[str, Any]:
        state = self._state()
        processed = set(str(item) for item in state.get("processed_refs") or [])
        candidate_state = dict(state.get("candidates") or {})
        new = [row for row in self._sources() if row["source_ref"] not in processed]
        evidence_by_type: dict[str, list[str]] = defaultdict(list)
        projected = []

        for row in new:
            candidate_type, requirement, target = CANDIDATE_SPECS[row["kind"]]
            if candidate_type not in ALLOWED_CANDIDATE_TYPES:
                continue
            observed_at = _aware(row["observed_at"] or self.runtime.clock.now())
            experience = self.runtime.record_domain_evolution_experience(
                domain_event_ref="memory-failure:" + row["source_ref"],
                observed_at=observed_at,
                effective_at=observed_at,
                context_refs=["memory-system", candidate_type],
                confidence=0.95,
            )
            evidence = self.runtime.record_evolution_evidence(
                experience.experience_id,
                evidence_class=EvidenceClass.SYSTEM_MEASURED,
                settled_at=max(observed_at, _aware(self.runtime.clock.now())),
                outcome_id=row["source_ref"],
                outcome_success=False,
                process_score=0.0,
                quality_score=0.0,
                metrics=[EvidenceMetric("memory_failure", 1.0, "event")],
                confidence=0.95,
                evidence_refs=list(dict.fromkeys(
                    [row["source_ref"], *[str(x) for x in row["evidence_refs"] if str(x)]]
                )),
            )
            evidence_by_type[candidate_type].append(evidence.evidence_id)
            self.runtime.observe_evolution_fact(
                kind="FAILURE",
                subject=target,
                summary=f"{row['kind']}: {row['detail'][:500]}",
                severity=0.75,
                evidence_refs=[evidence.evidence_id],
                recurrence_key="memory:" + candidate_type,
                need_hint=f"Evaluate bounded {candidate_type} without changing memory authority.",
                target_capability=target,
                metadata={
                    "candidate_type": candidate_type,
                    "source_ref": row["source_ref"],
                    "memory_architecture_fingerprint": architecture_fingerprint(),
                },
            )
            projected.append({
                "source_ref": row["source_ref"],
                "candidate_type": candidate_type,
                "evidence_id": evidence.evidence_id,
                "experience_id": experience.experience_id,
            })
            processed.add(row["source_ref"])

        # Include existing bridge evidence so recurrence can reach threshold across runs.
        for item in getattr(self.runtime, "evolution_evidence", ()) or ():
            refs = set(str(x) for x in getattr(item, "evidence_refs", ()) or ())
            for candidate_type in ALLOWED_CANDIDATE_TYPES:
                if any("memory-failure:" + candidate_type in ref for ref in refs):
                    evidence_by_type[candidate_type].append(str(item.evidence_id))

        proposals = []
        sources = self._sources()
        grouped_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in sources:
            candidate_type = CANDIDATE_SPECS[row["kind"]][0]
            grouped_sources[candidate_type].append(row)

        for candidate_type, source_rows in grouped_sources.items():
            if len(source_rows) < self.MIN_FAILURES_FOR_CANDIDATE:
                continue
            if candidate_type in candidate_state:
                proposals.append(dict(candidate_state[candidate_type]))
                continue
            _, requirement, target = CANDIDATE_SPECS[source_rows[-1]["kind"]]
            known_evidence = []
            source_refs = {row["source_ref"] for row in source_rows}
            for item in getattr(self.runtime, "evolution_evidence", ()) or ():
                refs = set(str(x) for x in getattr(item, "evidence_refs", ()) or ())
                if refs & source_refs:
                    known_evidence.append(str(item.evidence_id))
            known_evidence = list(dict.fromkeys(known_evidence))
            if len(known_evidence) < self.MIN_FAILURES_FOR_CANDIDATE:
                continue

            # Every mutation type must have a domain-specific replay evaluator before Runtime may
            # create a mutation candidate. Recall weights have one today; other allowed types are
            # observed and routed but remain gated rather than being guessed into trials.
            if candidate_type != "RECALL_WEIGHT_CHANGE":
                proposal = {
                    "candidate_type": candidate_type,
                    "status": "AWAITING_REPLAY_EVALUATOR",
                    "evidence_refs": known_evidence[-32:],
                }
                proposals.append(proposal)
                continue
            cases = self.memory_evolution.build_self_supervised_benchmark(max_cases=24)
            if len(cases) < 4:
                continue
            challenger = self.memory_evolution.propose_from_signals(limit=100)
            experiment = self.memory_evolution.evaluate_candidate(
                challenger,
                cases,
                evidence_refs=[row["source_ref"] for row in source_rows[-16:]],
                rationale="Runtime bridge replay for measured recall failures",
            )
            baseline_score = float((experiment.get("baseline_result") or {}).get("score") or 0.0)
            challenger_score = float((experiment.get("challenger_result") or {}).get("score") or 0.0)
            replay = {
                "status": "REPLAYED",
                "experiment_id": experiment["experiment_id"],
                "baseline_score": baseline_score,
                "challenger_score": challenger_score,
            }
            if challenger_score <= baseline_score + 1e-12:
                continue

            candidate = self.runtime.propose_evolution_candidate(
                requirement=requirement,
                target=target + ":" + candidate_type.lower(),
                description=(
                    f"Evaluate {candidate_type} because measured memory-use failures recur; "
                    "keep canonical memory, provenance, authority and rollback invariants frozen."
                ),
                baseline_ref="memory-architecture:" + architecture_fingerprint(),
                rollback_plan="restore prior persisted memory policy/configuration",
                evidence_refs=known_evidence[-32:],
                expected_fitness={"memory_recall_quality": 1.0},
                protected_invariants=[
                    "memory-authority-boundary",
                    "append-only-history",
                    "provenance-preservation",
                    "derived-index-rebuildability",
                ],
                risk_budget=0.05,
                cost_budget=0.0,
            )
            now = _aware(self.runtime.clock.now())
            runtime_experiment = self.runtime.create_evolution_experiment(
                candidate.candidate_id,
                prospective_start_at=now + timedelta(minutes=1),
                prospective_end_at=now + timedelta(days=7),
                metrics=[
                    ExperimentMetric(
                        "memory_recall_quality",
                        MetricDirection.HIGHER_BETTER,
                        tolerance=0.0,
                        primary=True,
                    )
                ],
                min_samples_per_arm=2,
                regression_evidence_refs=known_evidence[-32:],
                stop_conditions=[
                    "protected memory invariant regression",
                    "recall quality regression",
                ],
            )
            experiment_id = str(runtime_experiment.spec.experiment_id)
            self.runtime.validate_evolution_replay(
                experiment_id,
                evidence_refs=known_evidence[-32:],
            )
            shadow = self.runtime.enter_evolution_shadow(
                experiment_id,
                evidence_refs=known_evidence[-32:],
            )
            proposal = {
                "candidate_type": candidate_type,
                "candidate_id": candidate.candidate_id,
                "experiment_id": experiment_id,
                "stage": str(getattr(shadow.stage, "value", shadow.stage)),
                "evidence_refs": known_evidence[-32:],
                "replay": replay,
            }
            candidate_state[candidate_type] = proposal
            proposals.append(proposal)

        state = {
            "processed_refs": sorted(processed)[-10000:],
            "candidates": candidate_state,
            "last_sync_at": _aware(self.runtime.clock.now()).isoformat(),
        }
        self._write_state(state)
        return {
            "status": "UPDATED" if projected or proposals else "NO_CHANGE",
            "projected_failures": projected,
            "candidates": proposals,
            "allowed_candidate_types": sorted(ALLOWED_CANDIDATE_TYPES),
        }


__all__ = ["ALLOWED_CANDIDATE_TYPES", "MemoryRuntimeEvolutionBridge"]
