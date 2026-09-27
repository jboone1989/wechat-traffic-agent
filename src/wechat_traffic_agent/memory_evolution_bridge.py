from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from agent_runtime.evolution import (
    EvidenceClass,
    EvidenceMetric,
    ExperimentArm,
    ExperimentMetric,
    ExperimentResult,
    ExperimentStage,
    MetricDirection,
    MutationRequirement,
)

from .memory_evolution import (
    MemoryBenchmarkCase,
    MemoryEvolutionEngine,
    architecture_fingerprint,
)
from .memory_retrieval import RecallWeights
from .memory_procedural import ProcedurePolicy
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

RECALL_CAPABILITY_ID = "memory.recall.policy"
RECALL_MIN_PROSPECTIVE_SAMPLES = 2
RECALL_ROLLBACK_FAILURE_THRESHOLD = 2


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

    @staticmethod
    def _policy_ref(weights: Mapping[str, Any]) -> str:
        payload = json.dumps(dict(weights), sort_keys=True, separators=(",", ":"))
        import hashlib
        return "memory.recall.policy:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def _write_active_policy(
        self,
        *,
        weights: Mapping[str, Any],
        capability_version_id: str,
        candidate_id: str,
        experiment_id: str,
        status: str,
    ) -> None:
        path = self.state_root / "application" / "memory_system" / "active_recall_policy.json"
        row = {
            "policy_schema": 2,
            "authority": "agent-runtime-evolution",
            "status": status,
            "capability_version_id": capability_version_id,
            "candidate_id": candidate_id,
            "experiment_id": experiment_id,
            "architecture_fingerprint": architecture_fingerprint(),
            "weights": dict(weights),
            "activated_at": _aware(self.runtime.clock.now()).isoformat(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    def _prospective_memories(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        rows = self.memory_evolution.store.query_memories(
            memory_types=["SEMANTIC", "EPISODIC", "PROCEDURAL"],
            statuses=["ACTIVE"],
            access_scope="PRIVATE_OWNER",
            limit=5000,
        )
        selected = []
        for row in rows:
            raw = str(row.get("created_at") or "")
            if not raw:
                continue
            try:
                created = _aware(raw)
            except Exception:
                continue
            if start_at <= created < end_at:
                selected.append(row)
        selected.sort(key=lambda row: str(row.get("created_at") or ""))
        return selected[:24]

    def _memory_trial_metrics(
        self,
        row: Mapping[str, Any],
        *,
        weights: RecallWeights,
        now: datetime,
    ) -> dict[str, float]:
        memory_id = str(row.get("memory_id") or "")
        memory_type = str(row.get("memory_type") or "SEMANTIC")
        text = str(row.get("text") or "").strip()
        probe = self.memory_evolution._semantic_probe(row)
        if not memory_id or len(text) < 8:
            return {"memory_recall_quality": 0.0, "memory_exact_recall": 0.0}
        exact = MemoryBenchmarkCase(
            case_id="prospective-exact:" + memory_id,
            query=text[:320],
            expected_ids=(memory_id,),
            category="exact",
            limit=5,
            memory_types=(memory_type,),
            access_scope="PRIVATE_OWNER",
            now=now,
        )
        semantic = MemoryBenchmarkCase(
            case_id="prospective-semantic:" + memory_id,
            query=(probe or text[:320]),
            expected_ids=(memory_id,),
            category="semantic",
            limit=5,
            memory_types=(memory_type,),
            access_scope="PRIVATE_OWNER",
            now=now,
        )
        exact_result = self.memory_evolution.benchmark.run([exact], weights=weights)
        semantic_result = self.memory_evolution.benchmark.run([semantic], weights=weights)
        exact_case = exact_result["cases"][0]
        semantic_case = semantic_result["cases"][0]
        return {
            "memory_recall_quality": float(semantic_case.get("reciprocal_rank") or 0.0),
            "memory_exact_recall": float(exact_case.get("reciprocal_rank") or 0.0),
        }

    def _advance_recall_trial(
        self,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        if str(proposal.get("status") or "") in {
            "PROMOTED", "ROLLED_BACK", "REJECTED_REGRESSION", "REJECTED_NO_IMPROVEMENT",
        }:
            return proposal
        experiment_id = str(proposal.get("experiment_id") or "")
        candidate_id = str(proposal.get("candidate_id") or "")
        if not experiment_id or not candidate_id:
            return proposal
        experiment = self.runtime.evolution_experiment_manager.get(experiment_id)
        now = _aware(self.runtime.clock.now())
        if experiment.stage is ExperimentStage.SHADOW:
            if now < experiment.spec.prospective_start_at:
                proposal["stage"] = "SHADOW"
                proposal["status"] = "WAITING_PROSPECTIVE_START"
                return proposal
            experiment = self.runtime.begin_evolution_prospective_trial(experiment_id)
        proposal["stage"] = str(getattr(experiment.stage, "value", experiment.stage))
        if experiment.stage is not ExperimentStage.PROSPECTIVE_TRIAL:
            return proposal

        baseline = RecallWeights.from_mapping(dict(proposal.get("baseline_weights") or {}))
        challenger = RecallWeights.from_mapping(dict(proposal.get("challenger_weights") or {}))
        existing = {
            (str(sample.arm.value), str(sample.experience_id))
            for sample in experiment.samples
        }
        added = []
        memories = self._prospective_memories(
            experiment.spec.prospective_start_at,
            min(now, experiment.spec.prospective_end_at),
        )
        for row in memories:
            memory_id = str(row.get("memory_id") or "")
            event_at = _aware(str(row.get("created_at") or ""))
            if event_at >= now:
                continue
            for arm, weights in (
                (ExperimentArm.BASELINE, baseline),
                (ExperimentArm.CANDIDATE, challenger),
            ):
                domain_ref = f"memory-recall-prospective:{experiment_id}:{arm.value}:{memory_id}"
                experience = self.runtime.record_domain_evolution_experience(
                    domain_event_ref=domain_ref,
                    observed_at=event_at,
                    effective_at=event_at,
                    context_refs=["memory-system", "prospective-recall", arm.value],
                    confidence=1.0,
                )
                if (arm.value, experience.experience_id) in existing:
                    continue
                metrics = self._memory_trial_metrics(row, weights=weights, now=now)
                evidence = self.runtime.record_evolution_evidence(
                    experience.experience_id,
                    evidence_class=EvidenceClass.SYSTEM_MEASURED,
                    settled_at=now,
                    outcome_id=domain_ref + ":outcome",
                    outcome_success=metrics["memory_recall_quality"] > 0.0,
                    process_score=1.0,
                    quality_score=max(0.0, min(1.0, metrics["memory_recall_quality"])),
                    metrics=[
                        EvidenceMetric("memory_recall_quality", metrics["memory_recall_quality"], "score"),
                        EvidenceMetric("memory_exact_recall", metrics["memory_exact_recall"], "score"),
                    ],
                    confidence=1.0,
                    evidence_refs=[memory_id, domain_ref],
                )
                sample = self.runtime.add_evolution_trial_sample(
                    experiment_id,
                    arm=arm,
                    experience_id=experience.experience_id,
                    evidence_refs=[evidence.evidence_id],
                    known_at=now,
                    metric_values=metrics,
                )
                added.append(sample.sample_id)
                existing.add((arm.value, experience.experience_id))

        evaluation = self.runtime.evaluate_evolution_experiment(experiment_id)
        proposal["prospective"] = {
            "added_sample_ids": added,
            "baseline_samples": evaluation.baseline_samples,
            "candidate_samples": evaluation.candidate_samples,
            "result": str(getattr(evaluation.result, "value", evaluation.result)),
            "evaluation_id": evaluation.evaluation_id,
        }
        if evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED:
            active = None
            try:
                active = self.runtime.evolution_capability_registry.active(RECALL_CAPABILITY_ID)
            except KeyError:
                active = None
            if active is None or str(active.candidate_id) != candidate_id:
                capability = self.runtime.promote_evolution_candidate(
                    capability_id=RECALL_CAPABILITY_ID,
                    candidate_id=candidate_id,
                    experiment_id=experiment_id,
                    evaluation_id=evaluation.evaluation_id,
                    applicability_context=["canonical-memory-recall", "private-owner-memory"],
                    confidence=0.9,
                )
            else:
                capability = active
            self._write_active_policy(
                weights=proposal["challenger_weights"],
                capability_version_id=capability.version_id,
                candidate_id=candidate_id,
                experiment_id=experiment_id,
                status="PROMOTED",
            )
            proposal["status"] = "PROMOTED"
            proposal["capability_version_id"] = capability.version_id
            proposal["promoted_at"] = now.isoformat()
            proposal["known_failure_refs_at_promotion"] = sorted({
                str(row.get("source_ref") or "")
                for row in self._sources()
                if row.get("kind") in CANDIDATE_SPECS
                and CANDIDATE_SPECS[str(row["kind"])][0] == "RECALL_WEIGHT_CHANGE"
                and str(row.get("source_ref") or "")
            })
        elif evaluation.result is ExperimentResult.REGRESSION_OBSERVED:
            proposal["status"] = "REJECTED_REGRESSION"
        elif evaluation.result is ExperimentResult.NO_IMPROVEMENT:
            proposal["status"] = (
                "REJECTED_NO_IMPROVEMENT"
                if now >= experiment.spec.prospective_end_at
                else "PROSPECTIVE_TRIAL_NO_IMPROVEMENT_YET"
            )
        else:
            proposal["status"] = "PROSPECTIVE_TRIAL"
        return proposal

    def _maybe_rollback_recall_policy(self, proposal: dict[str, Any]) -> dict[str, Any]:
        if proposal.get("status") != "PROMOTED":
            return proposal
        promoted_at = _aware(proposal.get("promoted_at"))
        known_at_promotion = {
            str(ref)
            for ref in proposal.get("known_failure_refs_at_promotion") or ()
            if str(ref)
        }
        failures = []
        for row in self._sources():
            if CANDIDATE_SPECS[row["kind"]][0] != "RECALL_WEIGHT_CHANGE":
                continue
            if str(row.get("source_ref") or "") in known_at_promotion:
                continue
            try:
                observed = _aware(row.get("observed_at"))
            except Exception:
                continue
            if observed > promoted_at:
                failures.append(row)
        if len(failures) < RECALL_ROLLBACK_FAILURE_THRESHOLD:
            return proposal
        capability = self.runtime.rollback_evolution_capability(
            RECALL_CAPABILITY_ID,
            reason="repeated measured recall failures after promoted memory recall policy",
            evidence_refs=[row["source_ref"] for row in failures[-RECALL_ROLLBACK_FAILURE_THRESHOLD:]],
        )
        self._write_active_policy(
            weights=proposal["baseline_weights"],
            capability_version_id=capability.version_id,
            candidate_id=str(capability.candidate_id),
            experiment_id=str(capability.experiment_id),
            status="ROLLED_BACK",
        )
        proposal["status"] = "ROLLED_BACK"
        proposal["rolled_back_at"] = _aware(self.runtime.clock.now()).isoformat()
        proposal["rollback_failure_refs"] = [
            row["source_ref"] for row in failures[-RECALL_ROLLBACK_FAILURE_THRESHOLD:]
        ]
        return proposal

    def sync(self) -> dict[str, Any]:
        state = self._state()
        processed = set(str(item) for item in state.get("processed_refs") or [])
        candidate_state = dict(state.get("candidates") or {})
        existing_recall = candidate_state.get("RECALL_WEIGHT_CHANGE")
        if isinstance(existing_recall, dict):
            existing_recall = self._advance_recall_trial(dict(existing_recall))
            existing_recall = self._maybe_rollback_recall_policy(existing_recall)
            candidate_state["RECALL_WEIGHT_CHANGE"] = existing_recall
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

            # Procedure variants are evaluated prospectively against verified real executions,
            # not by replaying external tool calls.  ProceduralMemoryService/ProcedurePolicy
            # already owns bounded challenger exploration and only promotes a mature variant
            # after measured outcomes.  Surface that authority truthfully instead of implying
            # that skill learning is blocked on a replay evaluator.
            if candidate_type == "SKILL_VARIANT_PROMOTION":
                proposal = {
                    "candidate_type": candidate_type,
                    "status": "PROSPECTIVE_POLICY_OWNS_EVALUATION",
                    "evaluation_mode": "VERIFIED_PROSPECTIVE_EXECUTION",
                    "promotion_authority": "memory.procedure.policy",
                    "minimum_samples_per_variant": ProcedurePolicy.MIN_SAMPLES,
                    "replay_required": False,
                    "evidence_refs": known_evidence[-32:],
                }
                proposals.append(proposal)
                continue

            # Every mutation type must have a domain-specific replay evaluator before Runtime may
            # create a mutation candidate. Recall weights have one today; decay/index/salience
            # changes remain gated rather than being guessed into trials.
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

            baseline_weights = self.memory_evolution.active_weights().as_dict()
            challenger_weights = challenger.as_dict()
            baseline_ref = self._policy_ref(baseline_weights)
            challenger_ref = self._policy_ref(challenger_weights)
            self.runtime.register_evolution_capability_baseline(
                capability_id=RECALL_CAPABILITY_ID,
                implementation_ref=baseline_ref,
                evidence_refs=known_evidence[-32:],
                applicability_context=["canonical-memory-recall", "private-owner-memory"],
                confidence=1.0,
            )
            candidate = self.runtime.propose_evolution_candidate(
                requirement=requirement,
                target=challenger_ref,
                description=(
                    f"Evaluate {candidate_type} because measured memory-use failures recur; "
                    "keep canonical memory, provenance, authority and rollback invariants frozen."
                ),
                baseline_ref=baseline_ref,
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
                    ),
                    ExperimentMetric(
                        "memory_exact_recall",
                        MetricDirection.NON_REGRESSION,
                        tolerance=0.0,
                        primary=False,
                    ),
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
                "baseline_weights": baseline_weights,
                "challenger_weights": challenger_weights,
                "baseline_policy_ref": baseline_ref,
                "challenger_policy_ref": challenger_ref,
                "status": "WAITING_PROSPECTIVE_START",
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
