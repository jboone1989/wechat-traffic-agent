"""Domain evaluators/executors for Runtime-owned memory evolution experiments.

Paired prospective shadow retrieval measures recall on independently settled future
uses. It is not a claim that either arm caused a better external-world outcome.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any

from agent_runtime.evolution import (
    EvidenceClass, EvidenceMetric, ExperimentArm, ExperimentMetric, ExperimentResult,
    ExperimentStage, MetricDirection, MutationRequirement,
)

from .memory_domain_policy import DOMAIN_SPECS, challenger_policies, load_policies, policy_ref
from .memory_evolution import architecture_fingerprint
from .memory_observation import MemoryLifeObservatory
from .memory_retrieval import MemoryRouter, RecallWeights
from .memory_system import CanonicalMemoryStore


MIN_REPLAY = 4
MIN_PROSPECTIVE = 4
MIN_GAIN = 0.01
TERMINAL = {"ROLLED_BACK", "REJECTED_REGRESSION", "REJECTED_NO_IMPROVEMENT",
            "EXPIRED_INSUFFICIENT_EVIDENCE", "INVALIDATED_POLICY_DRIFT"}
INVARIANTS = ["canonical-memory-and-event-ledger-unchanged", "scope-and-status-boundaries",
              "exact-and-historical-recall-non-regression", "derived-index-rebuildability",
              "no-authority-or-confidence-mutation"]


def aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("domain evidence requires an aware timestamp")
    return parsed.astimezone(timezone.utc)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def sandbox(store: CanonicalMemoryStore):
    # SQLite backup pins both arms to one consistent snapshot; no live index rebuild.
    with TemporaryDirectory(prefix="memory-domain-eval-") as directory:
        root = Path(directory)
        target = root / "application" / "memory_system" / "memory.db"
        target.parent.mkdir(parents=True)
        with store._connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        yield root, CanonicalMemoryStore(root)


class DomainEvaluator:
    """Separate domain objectives, sharing protected retrieval/ledger checks."""

    def __init__(self, domain: str):
        self.domain = domain
        self.primary = {"decay": "decay_useful_recall", "index": "ranking_work",
                        "salience": "salience_useful_recall"}[domain]
        self.lower_better = domain == "index"

    def eligible(self, case: dict, store: CanonicalMemoryStore) -> bool:
        expected = [store.get_memory(item) for item in case["expected_ids"]]
        if not expected or any(row is None or row["status"] != "ACTIVE"
                               or row["access_scope"] != "PRIVATE_OWNER"
                               or row["memory_type"] not in {"SEMANTIC", "EPISODIC", "PROCEDURAL"}
                               or not row.get("source_ref")
                               or aware(row["created_at"]) > aware(case["event_at"]) for row in expected):
            return False
        if self.domain == "decay":
            return any((aware(case["event_at"]) - aware(row["created_at"])).total_seconds() >= 7 * 86400
                       and float(row["importance"]) < 0.95 for row in expected)
        if self.domain == "salience":
            return any(abs(float((row.get("payload") or {}).get("attributes", {}).get("recall_boost", 1.0)) - 1.0) > 0.01
                       for row in expected)
        return True

    def metrics(self) -> list[ExperimentMetric]:
        return [
            ExperimentMetric(self.primary, MetricDirection.LOWER_BETTER if self.lower_better
                             else MetricDirection.HIGHER_BETTER, tolerance=MIN_GAIN, primary=True),
            ExperimentMetric("useful_recall", MetricDirection.NON_REGRESSION, tolerance=0.0, primary=False),
            ExperimentMetric("protected_recall", MetricDirection.NON_REGRESSION, tolerance=0.0, primary=False),
            ExperimentMetric("invariants", MetricDirection.NON_REGRESSION, tolerance=0.0, primary=False),
        ]

    @staticmethod
    def rank(ids: list[str], expected: list[str]) -> float:
        return sum(1.0 / (ids.index(item) + 1) if item in ids else 0.0 for item in expected) / len(expected)

    def measure(self, router: MemoryRouter, case: dict) -> dict:
        now = aware(case["event_at"])
        rows = router.recall(case["query"], limit=5, now=now, access_scope="PRIVATE_OWNER")
        ids = [row["memory_id"] for row in rows]
        cost = router.last_recall_metrics["candidate_cost"]
        useful = self.rank(ids, case["expected_ids"])
        protected = []
        safe = all(row["access_scope"] == "PRIVATE_OWNER" and
                   (router.plan(case["query"], now=now).historical or row["status"] == "ACTIVE") for row in rows)
        # Preserve exact recall and the explicit historical escape hatch, even for old/cold records.
        for memory_id in dict.fromkeys([*case["expected_ids"], *case.get("guard_ids", [])]):
            memory = router.store.get_memory(memory_id)
            queries = ["historical " + memory["text"][:320]]
            if memory["status"] == "ACTIVE":
                queries.extend([memory["text"][:320], memory["created_at"][:10] + " " + memory["text"][:320]])
            for query in queries:
                recalled = router.recall(query, limit=5, now=now, access_scope="PRIVATE_OWNER")
                protected.append(self.rank([row["memory_id"] for row in recalled], [memory_id]))
            public = router.recall(memory["text"][:320], now=now, access_scope="PUBLIC")
            safe = safe and all(row["access_scope"] == "PUBLIC" for row in public)
        return {self.primary: cost if self.lower_better else useful,
                "useful_recall": useful, "protected_recall": min(protected),
                "invariants": float(safe), "returned_ids": ids, "protected_scores": protected}

    def compare(self, store: CanonicalMemoryStore, cases: list[dict], weights: dict,
                policies: dict, challenger: dict) -> dict:
        with sandbox(store) as (root, snapshot):
            before = digest([snapshot.list_memories(limit=None), snapshot.events()])
            guards = [row["memory_id"] for row in snapshot.list_memories(limit=None)
                      if row["access_scope"] == "PRIVATE_OWNER" and (row["memory_type"] == "CORE"
                      or float(row["importance"]) >= 0.95 or row["status"] != "ACTIVE")][:8]
            cases = [{**case, "guard_ids": guards} for case in cases]
            baseline_router = MemoryRouter(root, store=snapshot, weights=RecallWeights.from_mapping(weights),
                                           domain_policies=policies)
            candidate_policies = {**policies, self.domain: challenger}
            candidate_router = MemoryRouter(root, store=snapshot, weights=RecallWeights.from_mapping(weights),
                                            domain_policies=candidate_policies)
            pairs = []
            for case in cases:
                base = self.measure(baseline_router, case)
                cand = self.measure(candidate_router, case)
                pairs.append({"case_id": case["case_id"], "baseline": base, "candidate": cand})
            # Destruction/rebuild happens only in the evaluation copy, from canonical records.
            candidate_router.fts.rebuild()
            candidate_router.vector.rebuild()
            rebuilt = [self.measure(candidate_router, case) for case in cases]
            unchanged = before == digest([snapshot.list_memories(limit=None), snapshot.events()])
            invariant_ok = unchanged and all(pair["candidate"] == rebuilt[index]
                                             for index, pair in enumerate(pairs))
            names = [metric.name for metric in self.metrics()]
            means = {arm: {key: sum(pair[arm][key] for pair in pairs) / len(pairs)
                           for key in names} for arm in ("baseline", "candidate")}
            regressions = [pair["case_id"] for pair in pairs
                           if any(pair["candidate"][key] + 1e-12 < pair["baseline"][key]
                                  for key in ("useful_recall", "protected_recall", "invariants"))
                           or any(right + 1e-12 < left for left, right in zip(
                               pair["baseline"]["protected_scores"], pair["candidate"]["protected_scores"]))
                           or pair["candidate"]["invariants"] != 1.0]
            delta = means["candidate"][self.primary] - means["baseline"][self.primary]
            gain = -delta if self.lower_better else delta
            return {"pairs": pairs, **means, "gain": gain, "protected_regressions": regressions,
                    "invariants_passed": invariant_ok, "snapshot_hash": before,
                    "case_fingerprint": digest(cases), "case_count": len(cases),
                    "passed": invariant_ok and not regressions and gain > MIN_GAIN}


class MemoryDomainEvolution:
    def __init__(self, *, runtime: Any, state_root: Path):
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.store = CanonicalMemoryStore(self.state_root)
        self.root = self.store.root / "domain_evolution"
        self.root.mkdir(parents=True, exist_ok=True)
        self.observatory = MemoryLifeObservatory(self.state_root)

    def cases(self, now: datetime) -> list[dict]:
        rows = self.observatory.rows(limit=10000)
        uses = {row["memory_use_id"]: row for row in rows if row.get("query")}
        cases = {}
        seen_uses = set()
        contradicted_uses, contradicted_outcomes = self._counterevidence(rows, now)
        for settlement in rows:
            if (settlement.get("event_type") != "SETTLEMENT"
                    or settlement.get("outcome_success") is not True
                    or (settlement.get("metadata") or {}).get("memory_helped") is not True
                    or not settlement.get("evidence_refs") or not settlement.get("outcome_ref")):
                continue
            use = uses.get(settlement.get("parent_use_id"))
            if (not use or use["memory_use_id"] in seen_uses
                    or use["memory_use_id"] in contradicted_uses
                    or settlement["outcome_ref"] in contradicted_outcomes):
                continue
            # Used IDs are actual decision evidence. A bare positive retrieval response is
            # ambiguous about which returned memories helped and is not a ranking label.
            expected = list(dict.fromkeys(use.get("used_memory_ids") or []))
            if not expected or len(expected) > 5:
                continue
            try:
                event_at, settled_at = aware(use["observed_at"]), aware(settlement["observed_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if not event_at < settled_at <= now:
                continue
            outcome = str(settlement["outcome_ref"])
            seen_uses.add(use["memory_use_id"])
            cases.setdefault(outcome, {
                "case_id": digest([outcome, use["memory_use_id"]]), "query": use["query"],
                "expected_ids": expected, "event_at": event_at.isoformat(),
                "settled_at": settled_at.isoformat(), "outcome_ref": outcome,
                "use_id": use["memory_use_id"], "evidence_refs": settlement["evidence_refs"],
            })
        return sorted(cases.values(), key=lambda row: (row["event_at"], row["case_id"]))[-128:]

    @staticmethod
    def _counterevidence(rows: list[dict], now: datetime) -> tuple[set, set]:
        uses, outcomes = set(), set()
        for row in rows:
            if (row.get("event_type") != "SETTLEMENT" or not row.get("evidence_refs")
                    or (row.get("metadata") or {}).get("memory_helped") is not False):
                continue
            try:
                if aware(row["observed_at"]) > now:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            if row.get("parent_use_id"):
                uses.add(row["parent_use_id"])
            if row.get("outcome_ref"):
                outcomes.add(row["outcome_ref"])
        return uses, outcomes

    def _persist(self, domain: str, row: dict) -> None:
        atomic_json(self.root / f"{domain}.json", row)
        if row.get("experiment_id") and row.get("status") in TERMINAL | {"PROMOTED"}:
            experiment = self.runtime.evolution_experiment_manager.get(row["experiment_id"])
            if experiment.stage is ExperimentStage.PROSPECTIVE_TRIAL:
                self.runtime.evolution_experiment_manager.close(
                    row["experiment_id"], occurred_at=aware(self.runtime.clock.now()),
                    evidence_refs=[row["replay_evidence_id"]])
                self.runtime.persist()

    def _load(self, domain: str) -> dict:
        try:
            row = json.loads((self.root / f"{domain}.json").read_text(encoding="utf-8"))
            return row if isinstance(row, dict) else {}
        except (OSError, ValueError):
            return {}

    def _materialize(self, domain: str, parameters: dict, capability: Any, status: str) -> None:
        if capability.implementation_ref != policy_ref(domain, parameters):
            raise ValueError("Runtime capability does not authorize materialized parameters")
        atomic_json(self.store.root / f"active_{domain}_policy.json", {
            "schema": "memory-domain-policy.v1", "authority": "agent-runtime-evolution",
            "capability_version_id": capability.version_id, "candidate_id": capability.candidate_id,
            "experiment_id": capability.experiment_id, "parameters": parameters, "status": status,
            "architecture_fingerprint": architecture_fingerprint(),
        })

    def _evidence(self, ref: str, effective_at: datetime, metrics: dict, refs: list[str]):
        now = aware(self.runtime.clock.now())
        experience = self.runtime.record_domain_evolution_experience(
            domain_event_ref=ref, observed_at=effective_at, effective_at=effective_at,
            context_refs=["memory-domain-evaluator", "paired-shadow-retrieval"], confidence=1.0)
        evidence = self.runtime.record_evolution_evidence(
            experience.experience_id, evidence_class=EvidenceClass.SYSTEM_MEASURED,
            settled_at=now, outcome_id=ref, outcome_success=metrics.get("invariants", 1.0) == 1.0,
            process_score=1.0, quality_score=metrics.get("useful_recall", 0.0), confidence=1.0,
            metrics=[EvidenceMetric(key, float(value), "score") for key, value in metrics.items()],
            evidence_refs=refs)
        return experience, evidence

    def _start(self, kind: str, evaluator: DomainEvaluator, row: dict, cases: list[dict],
               weights: dict, policies: dict, failure_refs: list[str]) -> dict:
        domain, capability_id = DOMAIN_SPECS[kind]
        baseline = policies[domain]
        reports = [(candidate, evaluator.compare(self.store, cases, weights, policies, candidate))
                   for candidate in challenger_policies(domain, baseline)]
        if not reports:
            row["status"] = "NO_BOUNDED_CHALLENGER"
            return row
        challenger, replay = max(reports, key=lambda item: (item[1]["passed"], item[1]["gain"]))
        row.update(replay=replay, status="REPLAY_NO_IMPROVEMENT")
        atomic_json(self.root / "replays" / (domain + "-" + row["input_fingerprint"] + ".json"),
                    {"candidate_type": kind, "cases": cases, "reports": reports})
        if not replay["passed"]:
            if replay["protected_regressions"] or not replay["invariants_passed"]:
                row["status"] = "REPLAY_REJECTED_INVARIANT"
            return row
        now = aware(self.runtime.clock.now())
        _, evidence = self._evidence("memory-domain-replay:" + domain + ":" + row["input_fingerprint"], now,
                                     {"useful_recall": replay["candidate"]["useful_recall"], "invariants": 1.0},
                                     failure_refs + [case["outcome_ref"] for case in cases])
        baseline_ref, challenger_ref = policy_ref(domain, baseline), policy_ref(domain, challenger)
        self.runtime.register_evolution_capability_baseline(
            capability_id=capability_id, implementation_ref=baseline_ref,
            evidence_refs=[evidence.evidence_id], applicability_context=["memory-router"], confidence=1.0)
        candidate = self.runtime.propose_evolution_candidate(
            requirement=MutationRequirement.RETRIEVAL_FAILURE if domain == "index" else MutationRequirement.JUDGMENT_RULE,
            target=challenger_ref, baseline_ref=baseline_ref,
            description=f"Bounded {kind}; paired future settled-use retrieval with protected memory checks",
            rollback_plan="Restore prior Runtime capability and exact prior domain parameters atomically",
            evidence_refs=[evidence.evidence_id], expected_fitness={evaluator.primary: replay["candidate"][evaluator.primary]},
            protected_invariants=INVARIANTS, risk_budget=0.05, cost_budget=0.0)
        experiment = self.runtime.create_evolution_experiment(
            candidate.candidate_id, prospective_start_at=now + timedelta(minutes=1),
            prospective_end_at=now + timedelta(days=7), metrics=evaluator.metrics(),
            min_samples_per_arm=MIN_PROSPECTIVE, regression_evidence_refs=[evidence.evidence_id],
            stop_conditions=INVARIANTS + ["baseline context drift", "any per-case protected regression"])
        experiment_id = experiment.spec.experiment_id
        # Save recovery intent before transitions; retries reconcile Runtime's durable state.
        row.update(status="WAITING_PROSPECTIVE_START", candidate_id=candidate.candidate_id,
                   experiment_id=experiment_id, baseline=baseline, challenger=challenger,
                   weights=weights, policies=policies, replay_case_ids=[case["case_id"] for case in cases],
                   replay_evidence_id=evidence.evidence_id, architecture_fingerprint=architecture_fingerprint())
        self._persist(domain, row)
        self.runtime.validate_evolution_replay(experiment_id, evidence_refs=[evidence.evidence_id])
        self.runtime.enter_evolution_shadow(experiment_id, evidence_refs=[evidence.evidence_id])
        return row

    def _advance(self, kind: str, evaluator: DomainEvaluator, row: dict, cases: list[dict],
                 weights: dict, policies: dict) -> dict:
        domain, capability_id = DOMAIN_SPECS[kind]
        now = aware(self.runtime.clock.now())
        active = self.runtime.evolution_capability_registry.active(capability_id)
        # Recover crashes between Runtime promotion/rollback and domain-policy materialization.
        if active.candidate_id == row["candidate_id"]:
            self._materialize(domain, row["challenger"], active, "PROMOTED")
            row.update(status="PROMOTED", promoted_at=active.born_at.isoformat(), capability_version_id=active.version_id)
        elif row.get("status") == "PROMOTED":
            if active.implementation_ref == policy_ref(domain, row["baseline"]):
                self._materialize(domain, row["baseline"], active, "ROLLED_BACK")
                row["status"] = "ROLLED_BACK"
            else:
                row["status"] = "INVALIDATED_POLICY_DRIFT"
            return row
        if row.get("status") in TERMINAL:
            return row
        if row.get("status") == "PROMOTED":
            return self._monitor(kind, evaluator, row, cases)
        if (weights != row["weights"] or policies != row["policies"]
                or row["architecture_fingerprint"] != architecture_fingerprint()
                or active.implementation_ref != policy_ref(domain, row["baseline"])):
            row["status"] = "INVALIDATED_POLICY_DRIFT"
            return row
        experiment = self.runtime.evolution_experiment_manager.get(row["experiment_id"])
        negative_uses, negative_outcomes = self._counterevidence(self.observatory.rows(limit=10000), now)
        sampled_evidence = {ref for sample in experiment.samples for ref in sample.evidence_refs}
        if any(set(evidence.evidence_refs) & (negative_uses | negative_outcomes)
               for evidence in self.runtime.evolution_evidence if evidence.evidence_id in sampled_evidence):
            row["status"] = "REJECTED_REGRESSION"
            row["reason"] = "Counterevidence invalidated a previously sampled positive use"
            return row
        if experiment.stage is ExperimentStage.PROPOSED:
            experiment = self.runtime.validate_evolution_replay(row["experiment_id"], evidence_refs=[row["replay_evidence_id"]])
        if experiment.stage is ExperimentStage.REPLAY_VALIDATED:
            experiment = self.runtime.enter_evolution_shadow(row["experiment_id"], evidence_refs=[row["replay_evidence_id"]])
        if now < experiment.spec.prospective_start_at:
            return row
        if experiment.stage is ExperimentStage.SHADOW:
            experiment = self.runtime.begin_evolution_prospective_trial(row["experiment_id"])
        future = [case for case in cases if case["case_id"] not in row["replay_case_ids"]
                  and experiment.spec.prospective_start_at <= aware(case["event_at"]) < experiment.spec.prospective_end_at
                  and aware(case["settled_at"]) <= experiment.spec.prospective_end_at]
        existing = {sample.experience_id for sample in experiment.samples}
        for case in future:
            result_path = self.root / "prospective" / (digest([row["experiment_id"], case["case_id"]]) + ".json")
            if result_path.exists():
                report = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                report = evaluator.compare(self.store, [case], row["weights"], row["policies"], row["challenger"])
                atomic_json(result_path, report)
            if report["protected_regressions"] or not report["invariants_passed"]:
                row.update(status="REJECTED_REGRESSION", rejected_case=case["case_id"])
                return row
            for arm, name in ((ExperimentArm.BASELINE, "baseline"), (ExperimentArm.CANDIDATE, "candidate")):
                ref = "memory-domain-prospective:" + digest([row["experiment_id"], case["case_id"], arm.value])
                experience = self.runtime.record_domain_evolution_experience(
                    domain_event_ref=ref, observed_at=aware(case["event_at"]), effective_at=aware(case["event_at"]),
                    context_refs=["memory-domain-evaluator", "paired-shadow-retrieval"], confidence=1.0)
                if experience.experience_id in existing:
                    continue
                _, evidence = self._evidence(ref, aware(case["event_at"]), report[name],
                                             [case["outcome_ref"], case["use_id"], report["snapshot_hash"]])
                self.runtime.add_evolution_trial_sample(row["experiment_id"], arm=arm,
                    experience_id=experience.experience_id, evidence_refs=[evidence.evidence_id],
                    known_at=now, metric_values=report[name])
                existing.add(experience.experience_id)
        evaluation = self.runtime.evaluate_evolution_experiment(row["experiment_id"])
        row["prospective"] = {"result": evaluation.result.value, "evaluation_id": evaluation.evaluation_id,
                              "baseline_samples": evaluation.baseline_samples, "candidate_samples": evaluation.candidate_samples}
        if evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED:
            capability = self.runtime.promote_evolution_candidate(
                capability_id=capability_id, candidate_id=row["candidate_id"], experiment_id=row["experiment_id"],
                evaluation_id=evaluation.evaluation_id, applicability_context=["memory-router"], confidence=0.9)
            self._materialize(domain, row["challenger"], capability, "PROMOTED")
            row.update(status="PROMOTED", promoted_at=now.isoformat(), capability_version_id=capability.version_id)
        elif evaluation.result is ExperimentResult.REGRESSION_OBSERVED:
            row["status"] = "REJECTED_REGRESSION"
        elif now >= experiment.spec.prospective_end_at:
            row["status"] = "EXPIRED_INSUFFICIENT_EVIDENCE" if evaluation.result is ExperimentResult.INSUFFICIENT_EVIDENCE else "REJECTED_NO_IMPROVEMENT"
        else:
            row["status"] = "PROSPECTIVE_TRIAL"
        return row

    def _monitor(self, kind: str, evaluator: DomainEvaluator, row: dict, cases: list[dict]) -> dict:
        domain, capability_id = DOMAIN_SPECS[kind]
        checked = set(row.get("monitor_checked", []))
        failures = list(row.get("monitor_failures", []))
        # Independently settled harmful uses must not disappear through the positive-label
        # filter. Only failures of uses actually bound to this active version count.
        observations = self.observatory.rows(limit=10000)
        uses = {item["memory_use_id"]: item for item in observations}
        negative_refs = set()
        for item in observations:
            use = uses.get(item.get("parent_use_id"), {})
            versions = (use.get("metadata") or {}).get("domain_policy_refs") or {}
            if (item.get("event_type") != "SETTLEMENT" or not item.get("evidence_refs")
                    or (item.get("metadata") or {}).get("memory_helped") is not False
                    or versions.get(domain) != policy_ref(domain, row["challenger"]) or not item.get("outcome_ref")):
                continue
            try:
                if not aware(row["promoted_at"]) < aware(use["observed_at"]) < aware(item["observed_at"]) <= aware(self.runtime.clock.now()):
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            negative_refs.add(str(item["outcome_ref"]))
        if len(negative_refs) >= 2:
            capability = self.runtime.rollback_evolution_capability(
                capability_id, reason="Two independently settled harmful memory uses bound to active domain version",
                evidence_refs=sorted(negative_refs))
            self._materialize(domain, row["baseline"], capability, "ROLLED_BACK")
            row.update(status="ROLLED_BACK", rollback_failure_refs=sorted(negative_refs),
                       rolled_back_at=aware(self.runtime.clock.now()).isoformat())
            return row
        for case in cases:
            if case["case_id"] in checked or aware(case["event_at"]) <= aware(row["promoted_at"]):
                continue
            # Freeze other live policies for both arms, so another domain is not blamed.
            policies = load_policies(self.state_root)
            policies[domain] = row["baseline"]
            weights = MemoryRouter(self.state_root).weights.as_dict()
            report = evaluator.compare(self.store, [case], weights, policies, row["challenger"])
            checked.add(case["case_id"])
            invariant_failure = not report["invariants_passed"] or bool(report["protected_regressions"])
            if invariant_failure or report["gain"] < -MIN_GAIN:
                failures.append(case["case_id"])
                atomic_json(self.root / "monitor" / (domain + "-" + case["case_id"] + ".json"), report)
            if invariant_failure or len(set(failures)) >= 2:
                capability = self.runtime.rollback_evolution_capability(
                    capability_id, reason="Measured post-promotion domain/protected recall regression",
                    evidence_refs=[case["outcome_ref"], case["use_id"], report["snapshot_hash"]])
                self._materialize(domain, row["baseline"], capability, "ROLLED_BACK")
                row.update(status="ROLLED_BACK", rolled_back_at=aware(self.runtime.clock.now()).isoformat())
                break
        row.update(monitor_checked=sorted(checked), monitor_failures=sorted(set(failures)))
        return row

    def sync(self, source_groups: dict[str, list[dict]]) -> list[dict]:
        now = aware(self.runtime.clock.now())
        all_cases = self.cases(now)
        corpus_fingerprint = digest(self.store.list_memories(limit=None))
        summaries = []
        for kind, (domain, _) in DOMAIN_SPECS.items():
            evaluator = DomainEvaluator(domain)
            row = self._load(domain)
            old_status = row.get("status")
            policies = load_policies(self.state_root)
            weights = MemoryRouter(self.state_root).weights.as_dict()
            cases = [case for case in all_cases if evaluator.eligible(case, self.store)]
            source_refs = sorted({str(ref) for source in source_groups.get(kind, [])
                                  for ref in source.get("evidence_refs", []) if ref})
            fingerprint = digest([cases, policies, weights, source_refs, corpus_fingerprint])
            if row.get("status") in TERMINAL and (
                (row["status"] == "INVALIDATED_POLICY_DRIFT" and fingerprint != row.get("input_fingerprint"))
                or len(set(source_refs) - set(row.get("source_refs", []))) >= 2
            ):
                atomic_json(self.root / "history" / (domain + "-" + row["experiment_id"] + ".json"), row)
                row = {}
            if row.get("experiment_id"):
                row = self._advance(kind, evaluator, row, cases, weights, policies)
            else:
                if fingerprint != row.get("input_fingerprint"):
                    row = {"candidate_type": kind, "evaluator": domain + "-settled-use-v1",
                           "evaluation_mode": "PAIRED_PROSPECTIVE_SHADOW_RETRIEVAL",
                           "causal_world_outcome_claim": False, "input_fingerprint": fingerprint,
                           "source_refs": source_refs,
                           "replay_case_count": len(cases), "unique_failure_evidence": len(source_refs),
                           "minimum_replay_cases": MIN_REPLAY, "minimum_samples_per_arm": MIN_PROSPECTIVE,
                           "status": "INSUFFICIENT_DOMAIN_EVIDENCE"}
                    if len(cases) >= MIN_REPLAY and len(source_refs) >= 2:
                        row = self._start(kind, evaluator, row, cases[-24:], weights, policies, source_refs[-32:])
            self._persist(domain, row)
            summary = {key: row[key] for key in ("candidate_type", "evaluator", "status", "experiment_id",
                       "replay_case_count", "unique_failure_evidence", "prospective") if key in row}
            summaries.append(summary)
            if old_status != row.get("status"):
                print(json.dumps({"memory_domain_evaluator_routed": summary}, sort_keys=True), flush=True)
        return summaries
