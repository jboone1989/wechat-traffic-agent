"""Ferro domain adapter for Agent Runtime Evolution Engine v1.

The bridge does not own evolution state. It binds real content decisions and
world outcomes to Runtime records, proposes bounded strategy challengers from
observational evidence, and lets Runtime own experiments/promotion/rollback.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from agent_runtime.evolution import (
    CausalRelation,
    DiagnosisStatus,
    EvidenceClass,
    EvidenceMetric,
    ExperimentArm,
    ExperimentMetric,
    ExperimentResult,
    ExperimentStage,
    MetricDirection,
    MutationRequirement,
    VersionBinding,
    make_cause,
)

from .content_action_prediction import ActionPredictionOutcome, ContentActionPrediction
from .content_strategy import (
    ContentStrategyBinding,
    PROFILE_BY_NAME,
    collect_mature_strategy_outcomes,
)


STRATEGY_CAPABILITY_ID = "content.strategy.preference"
STRATEGY_BASELINE_IMPL = "content.strategy.adaptive:v1"
PROFILE_IMPL_PREFIX = "content.strategy.profile:"
BRIDGE_SCHEMA = "content-evolution-bridge.v1"
TRIAL_SCHEMA = "content-evolution-strategy-trial.v1"
BOOTSTRAP_VERSION = 2
EVOLUTION_MIN_HISTORICAL_PROFILE_SAMPLES = 3


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evolution bridge timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


class ContentEvolutionBridge:
    """Bind Ferro's real strategy loop to Runtime Evolution Engine."""

    def __init__(self, *, runtime: Any, state_root: Path, account: str) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.account = str(account).strip().rstrip("/")
        self.root = self.state_root / "application" / "evolution_bridge"
        self.decisions_root = self.root / "strategy_decisions"
        self.trial_path = self.root / "content_strategy_trial.json"
        self.bootstrap_path = self.root / "historical_bootstrap.json"

    def _decision_path(self, prediction_id: str) -> Path:
        digest = sha256(str(prediction_id).encode("utf-8")).hexdigest()[:24]
        return self.decisions_root / f"{digest}.json"

    def _mission_refs(self) -> list[str]:
        rows = []
        for mission in list(getattr(self.runtime, "external_missions", ()) or ()):
            status = _enum_value(getattr(mission, "status", ""))
            if status not in {"ACTIVE", "PROPOSED"}:
                continue
            mission_id = str(getattr(mission, "mission_id", "") or "")
            if mission_id:
                rows.append("external-mission:" + mission_id)
        return rows
    def _active_capability(self):
        registry = getattr(self.runtime, "evolution_capability_registry", None)
        if registry is None:
            return None
        try:
            return registry.active(STRATEGY_CAPABILITY_ID)
        except KeyError:
            return None

    def ensure_baseline(self, *, evidence_refs: Iterable[str] = ()):
        method = getattr(self.runtime, "register_evolution_capability_baseline", None)
        if not callable(method):
            return None
        active = self._active_capability()
        if active is not None:
            return active
        return method(
            capability_id=STRATEGY_CAPABILITY_ID,
            implementation_ref=STRATEGY_BASELINE_IMPL,
            evidence_refs=list(dict.fromkeys(str(x) for x in evidence_refs if str(x))),
            applicability_context=["website-content", "content-strategy"],
            confidence=0.8,
        )

    def preferred_profile(self, *, cycle: str, now: datetime) -> dict[str, Any]:
        active = self._active_capability()
        if active is not None and str(active.implementation_ref).startswith(PROFILE_IMPL_PREFIX):
            profile = str(active.implementation_ref)[len(PROFILE_IMPL_PREFIX):]
            if profile in PROFILE_BY_NAME:
                return {
                    "profile_name": profile,
                    "mode": "PROMOTED",
                    "arm": "CANDIDATE",
                    "capability_version_id": str(active.version_id),
                    "experiment_id": str(active.experiment_id),
                }

        state = _read_json(self.trial_path)
        experiment_id = str(state.get("experiment_id") or "")
        candidate_profile = str(state.get("candidate_profile") or "")
        if not experiment_id or candidate_profile not in PROFILE_BY_NAME:
            return {"profile_name": None, "mode": "BASELINE", "arm": None}

        try:
            experiment = self.runtime.evolution_experiment_manager.get(experiment_id)
        except (AttributeError, KeyError):
            return {"profile_name": None, "mode": "BASELINE", "arm": None}

        if experiment.stage is ExperimentStage.SHADOW and now >= experiment.spec.prospective_start_at:
            self.runtime.begin_evolution_prospective_trial(experiment_id)
            experiment = self.runtime.evolution_experiment_manager.get(experiment_id)

        if experiment.stage is not ExperimentStage.PROSPECTIVE_TRIAL:
            return {
                "profile_name": None,
                "mode": "SHADOW",
                "arm": None,
                "experiment_id": experiment_id,
            }

        digest = sha256(f"{experiment_id}|{cycle}".encode("utf-8")).hexdigest()
        candidate_arm = int(digest[:8], 16) % 2 == 1
        arm = "CANDIDATE" if candidate_arm else "BASELINE"
        profile = candidate_profile if candidate_arm else None
        assignments = dict(state.get("assignments") or {})
        assignments[str(cycle)] = {
            "arm": arm,
            "profile_name": profile,
            "assigned_at": now.astimezone(timezone.utc).isoformat(),
        }
        if len(assignments) > 512:
            assignments = dict(list(sorted(assignments.items()))[-512:])
        state["assignments"] = assignments
        _atomic_json(self.trial_path, state)
        return {
            "profile_name": profile,
            "mode": "PROSPECTIVE_TRIAL",
            "arm": arm,
            "experiment_id": experiment_id,
        }
    def record_strategy_decision(
        self,
        *,
        cycle: str,
        strategy: ContentStrategyBinding,
        prediction: ContentActionPrediction,
        trial_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = self._active_capability()
        trial = dict(trial_context or {})
        experiment_id = str(trial.get("experiment_id") or "")
        arm = str(trial.get("arm") or "")
        version_bindings = [
            VersionBinding(
                "strategy-profile",
                "content.profile",
                strategy.profile.name,
            )
        ]
        if active is not None:
            version_bindings.append(
                VersionBinding(
                    "capability",
                    STRATEGY_CAPABILITY_ID,
                    str(active.version_id),
                )
            )
        experience = self.runtime.record_domain_evolution_experience(
            domain_event_ref=prediction.prediction_id,
            observed_at=prediction.created_at,
            effective_at=prediction.created_at,
            goal_refs=self._mission_refs(),
            decision_ref=strategy.strategy_id,
            context_refs=[
                f"content-cycle:{cycle}",
                f"topic:{strategy.topic}",
                f"content-profile:{strategy.profile.name}",
                f"selection-mode:{strategy.selection_mode}",
                *([f"evolution-experiment:{experiment_id}", f"experiment-arm:{arm}"] if experiment_id and arm else []),
            ],
            belief_refs=[
                f"content-action-prediction:{prediction.prediction_id}",
                *list(strategy.mind_strategy_evidence_refs),
                *list(strategy.prediction_evidence_refs),
            ],
            version_bindings=version_bindings,
            confidence=1.0,
        )
        payload = {
            "schema_version": BRIDGE_SCHEMA,
            "prediction_id": prediction.prediction_id,
            "experience_id": experience.experience_id,
            "cycle": str(cycle),
            "topic": strategy.topic,
            "strategy_id": strategy.strategy_id,
            "profile_name": strategy.profile.name,
            "selection_mode": strategy.selection_mode,
            "created_at": prediction.created_at.isoformat(),
            "experiment_id": experiment_id or None,
            "arm": arm or None,
            "capability_version_id": None if active is None else str(active.version_id),
        }
        _atomic_json(self._decision_path(prediction.prediction_id), payload)
        return payload

    def _backfill_decision(self, prediction_id: str) -> dict[str, Any]:
        existing = _read_json(self._decision_path(prediction_id))
        if existing:
            return existing
        cycles_root = self.state_root / "content" / "continuous" / "cycles"
        for prediction_path in sorted(cycles_root.glob("*/action_prediction.json")):
            try:
                prediction = ContentActionPrediction.model_validate_json(
                    prediction_path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if prediction.prediction_id != prediction_id:
                continue
            strategy_path = prediction_path.parent / "content_strategy.json"
            if not strategy_path.exists():
                return {}
            strategy = ContentStrategyBinding.model_validate_json(
                strategy_path.read_text(encoding="utf-8")
            )
            return self.record_strategy_decision(
                cycle=prediction.cycle,
                strategy=strategy,
                prediction=prediction,
                trial_context=None,
            )
        return {}
    def record_action_outcomes(
        self,
        outcomes: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        outcome_rows = list(outcomes)
        recorded = []
        experiment_updates = []
        capability_updates = []
        for raw in outcome_rows:
            try:
                outcome = ActionPredictionOutcome.model_validate(raw)
            except Exception:
                continue
            decision = self._backfill_decision(outcome.prediction_id)
            experience_id = str(decision.get("experience_id") or "")
            if not experience_id:
                continue
            evidence = self.runtime.record_evolution_evidence(
                experience_id,
                evidence_class=EvidenceClass.WORLD_OBSERVED,
                settled_at=outcome.observed_at,
                outcome_id=f"{outcome.prediction_id}:{outcome.window}",
                outcome_success=outcome.actual_pageviews >= outcome.expected_pageviews,
                process_score=1.0,
                quality_score=max(0.0, 1.0 - float(outcome.prediction_error)),
                metrics=[
                    EvidenceMetric("actual_pageviews", float(outcome.actual_pageviews), "views"),
                    EvidenceMetric("expected_pageviews", float(outcome.expected_pageviews), "views"),
                    EvidenceMetric("prediction_error", float(outcome.prediction_error), "ratio"),
                    EvidenceMetric("reliability", float(outcome.reliability), "ratio"),
                ],
                confidence=float(outcome.reliability),
                evidence_refs=[
                    *list(outcome.evidence_refs),
                    outcome.prediction_id,
                    "website-publication:" + outcome.publication_id,
                    "content-profile:" + outcome.profile_name,
                ],
            )
            recorded.append({
                "evidence_id": evidence.evidence_id,
                "experience_id": experience_id,
                "prediction_id": outcome.prediction_id,
                "window": outcome.window,
                "profile_name": outcome.profile_name,
                "actual_pageviews": float(outcome.actual_pageviews),
            })

            if outcome.maturity != "MATURE":
                continue
            experiment_id = str(decision.get("experiment_id") or "")
            arm = str(decision.get("arm") or "")
            if experiment_id and arm in {"BASELINE", "CANDIDATE"}:
                try:
                    sample = self.runtime.add_evolution_trial_sample(
                        experiment_id,
                        arm=ExperimentArm(arm),
                        experience_id=experience_id,
                        evidence_refs=[evidence.evidence_id],
                        known_at=outcome.observed_at,
                        metric_values={
                            "pageviews_24h": float(outcome.actual_pageviews),
                        },
                    )
                    evaluation = self.runtime.evaluate_evolution_experiment(experiment_id)
                    experiment_updates.append({
                        "sample_id": sample.sample_id,
                        "evaluation_id": evaluation.evaluation_id,
                        "result": _enum_value(evaluation.result),
                        "baseline_samples": evaluation.baseline_samples,
                        "candidate_samples": evaluation.candidate_samples,
                    })
                    if evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED:
                        capability = self._promote_strategy_experiment(
                            experiment_id=experiment_id,
                            evaluation_id=evaluation.evaluation_id,
                        )
                        if capability is not None:
                            capability_updates.append({
                                "status": "PROMOTED",
                                "version_id": capability.version_id,
                                "implementation_ref": capability.implementation_ref,
                            })
                except ValueError as exc:
                    experiment_updates.append({
                        "status": "SAMPLE_REJECTED",
                        "error": str(exc)[:300],
                        "experiment_id": experiment_id,
                    })

            active = self._active_capability()
            decision_capability_version = str(
                decision.get("capability_version_id") or ""
            )
            if (
                active is not None
                and decision_capability_version == str(active.version_id)
                and str(active.implementation_ref).startswith(PROFILE_IMPL_PREFIX)
                and outcome.profile_name
                == str(active.implementation_ref)[len(PROFILE_IMPL_PREFIX):]
            ):
                used = self.runtime.record_evolution_capability_use(
                    STRATEGY_CAPABILITY_ID,
                    success=outcome.actual_pageviews >= outcome.expected_pageviews,
                )
                capability_updates.append({
                    "status": "USE_RECORDED",
                    "version_id": used.version_id,
                    "usage_count": int(used.usage_count),
                    "success_count": int(used.success_count),
                })
                self._maybe_rollback_after_regression(outcome, used, evidence.evidence_id)

        return {
            "status": "RECORDED" if recorded else "NO_NEW_EVOLUTION_EVIDENCE",
            "recorded": recorded,
            "experiment_updates": experiment_updates,
            "capability_updates": capability_updates,
        }
    def _promote_strategy_experiment(self, *, experiment_id: str, evaluation_id: str):
        state = _read_json(self.trial_path)
        if str(state.get("experiment_id") or "") != experiment_id:
            return None
        candidate_id = str(state.get("candidate_id") or "")
        candidate_profile = str(state.get("candidate_profile") or "")
        if not candidate_id or candidate_profile not in PROFILE_BY_NAME:
            return None
        active = self._active_capability()
        if active is not None and str(active.candidate_id) == candidate_id:
            return active
        return self.runtime.promote_evolution_candidate(
            capability_id=STRATEGY_CAPABILITY_ID,
            candidate_id=candidate_id,
            experiment_id=experiment_id,
            evaluation_id=evaluation_id,
            applicability_context=[
                "website-content",
                "content-strategy",
                "prospective-24h-pageviews",
            ],
            confidence=0.8,
        )

    def _maybe_rollback_after_regression(self, outcome, capability, evidence_id: str) -> None:
        if not getattr(capability, "rollback_target", None):
            return
        state = _read_json(self.trial_path)
        streak = int(state.get("post_promotion_regression_streak") or 0)
        if outcome.actual_pageviews < outcome.expected_pageviews:
            streak += 1
        else:
            streak = 0
        state["post_promotion_regression_streak"] = streak
        _atomic_json(self.trial_path, state)
        if streak < 3:
            return
        self.runtime.rollback_evolution_capability(
            STRATEGY_CAPABILITY_ID,
            reason="three consecutive mature real-world outcomes underperformed their prospective expectations",
            evidence_refs=[evidence_id],
        )
        state["post_promotion_regression_streak"] = 0
        state["status"] = "ROLLED_BACK_AFTER_REAL_WORLD_REGRESSION"
        _atomic_json(self.trial_path, state)

    def _historical_outcomes(self) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        cycles_root = self.state_root / "content" / "continuous" / "cycles"
        for path in sorted(cycles_root.glob("*/action_prediction_outcomes/*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                outcomes.append(row)
        return outcomes

    def _historical_mature_prediction_outcomes(self) -> list[dict[str, Any]]:
        return [
            row for row in self._historical_outcomes()
            if str(row.get("maturity") or "") == "MATURE"
            and str(row.get("profile_name") or "") in PROFILE_BY_NAME
        ]

    def _historical_mature_strategy_outcomes(
        self, *, as_of: datetime
    ) -> list[dict[str, Any]]:
        return collect_mature_strategy_outcomes(
            state_root=self.state_root,
            account=self.account,
            as_of=_aware(as_of),
        )

    def _record_mature_strategy_outcomes(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> dict[str, Any]:
        recorded: list[dict[str, Any]] = []
        for raw in rows:
            profile = str(raw.get("profile_name") or "")
            outcome_id = str(raw.get("outcome_id") or "")
            publication_id = str(raw.get("publication_id") or "")
            topic = str(raw.get("topic") or "")
            if profile not in PROFILE_BY_NAME or not outcome_id or not publication_id:
                continue
            observed_at = _aware(raw.get("observed_at"))
            refs = list(dict.fromkeys(
                str(x) for x in (raw.get("evidence_refs") or ()) if str(x)
            ))
            strategy_ref = next(
                (ref for ref in refs if ref.startswith("strategy:")), None
            )
            experience = self.runtime.record_domain_evolution_experience(
                domain_event_ref=outcome_id,
                observed_at=observed_at,
                effective_at=observed_at,
                goal_refs=self._mission_refs(),
                decision_ref=strategy_ref,
                context_refs=[
                    f"topic:{topic}",
                    f"content-profile:{profile}",
                    f"website-publication:{publication_id}",
                    "historical-mature-strategy-outcome",
                ],
                version_bindings=[
                    VersionBinding("strategy-profile", "content.profile", profile)
                ],
                confidence=1.0,
            )
            evidence = self.runtime.record_evolution_evidence(
                experience.experience_id,
                evidence_class=EvidenceClass.WORLD_OBSERVED,
                settled_at=observed_at,
                outcome_id=outcome_id,
                process_score=1.0,
                metrics=[
                    EvidenceMetric(
                        "actual_pageviews",
                        float(raw.get("pageviews_24h") or 0.0),
                        "views",
                    )
                ],
                confidence=1.0,
                evidence_refs=[
                    *refs,
                    f"website-publication:{publication_id}",
                    f"content-profile:{profile}",
                ],
            )
            recorded.append({
                "outcome_id": outcome_id,
                "experience_id": experience.experience_id,
                "evidence_id": evidence.evidence_id,
                "profile_name": profile,
                "pageviews_24h": float(raw.get("pageviews_24h") or 0.0),
            })
        return {
            "status": "RECORDED" if recorded else "NO_MATURE_STRATEGY_OUTCOMES",
            "recorded_count": len(recorded),
            "recorded": recorded,
        }

    def maybe_bootstrap_historical_learning(self, *, as_of: datetime) -> dict[str, Any]:
        previous = _read_json(self.bootstrap_path)
        if (
            previous.get("status") == "OK"
            and int(previous.get("bootstrap_version") or 0) >= BOOTSTRAP_VERSION
        ):
            return {"status": "ALREADY_BOOTSTRAPPED", **previous}
        result = self.bootstrap_historical_learning(as_of=as_of)
        if result.get("status") == "OK":
            _atomic_json(self.bootstrap_path, result)
        return result

    def bootstrap_historical_learning(self, *, as_of: datetime) -> dict[str, Any]:
        as_of = _aware(as_of)
        prediction_outcomes = self._historical_outcomes()
        prediction_binding = self.record_action_outcomes(prediction_outcomes)
        strategy_mature = self._historical_mature_strategy_outcomes(as_of=as_of)
        strategy_binding = self._record_mature_strategy_outcomes(strategy_mature)
        report = self.runtime.consolidate_evolution()

        mature: list[Mapping[str, Any]] = (
            strategy_mature
            if strategy_mature
            else self._historical_mature_prediction_outcomes()
        )
        candidate = self._ensure_strategy_candidate(mature=mature, as_of=as_of)
        return {
            "status": "OK",
            "bootstrap_version": BOOTSTRAP_VERSION,
            "historical_prediction_outcomes_scanned": len(prediction_outcomes),
            "mature_prediction_outcomes": len(self._historical_mature_prediction_outcomes()),
            "mature_strategy_outcomes": len(strategy_mature),
            "candidate_source": "MATURE_STRATEGY_OUTCOMES" if strategy_mature else "ACTION_PREDICTION_OUTCOMES",
            "evolution_evidence_count": len(getattr(self.runtime, "evolution_evidence", ()) or ()),
            "consolidation_report_id": report.report_id,
            "recurring_clusters": sum(1 for item in report.clusters if item.recurring),
            "conflicts": len(report.conflicts),
            "candidate": candidate,
            "prediction_outcome_binding": prediction_binding,
            "strategy_outcome_binding": strategy_binding,
        }

    @staticmethod
    def _mature_pageviews(row: Mapping[str, Any]) -> float:
        if row.get("pageviews_24h") is not None:
            return float(row["pageviews_24h"])
        return float(row["actual_pageviews"])

    def _trial_has_committed_action(self, state: Mapping[str, Any]) -> bool:
        for cycle in (state.get("assignments") or {}):
            folder = self.state_root / "content" / "continuous" / "cycles" / str(cycle)
            if (
                (folder / "action_prediction.json").exists()
                or (folder / "publication.json").exists()
            ):
                return True
        return False

    def _ensure_strategy_candidate(
        self,
        *,
        mature: list[Mapping[str, Any]],
        as_of: datetime,
    ) -> dict[str, Any]:
        active = self._active_capability()
        if active is not None and str(active.implementation_ref).startswith(PROFILE_IMPL_PREFIX):
            return {
                "status": "ALREADY_PROMOTED",
                "version_id": active.version_id,
                "implementation_ref": active.implementation_ref,
            }

        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in mature:
            profile = str(row.get("profile_name") or "")
            if profile in PROFILE_BY_NAME:
                grouped[profile].append(row)
        eligible = {
            profile: rows for profile, rows in grouped.items()
            if len(rows) >= EVOLUTION_MIN_HISTORICAL_PROFILE_SAMPLES
        }
        if not eligible:
            return {
                "status": "INSUFFICIENT_HISTORICAL_EVIDENCE",
                "minimum_samples_per_profile": EVOLUTION_MIN_HISTORICAL_PROFILE_SAMPLES,
                "samples_by_profile": {k: len(v) for k, v in sorted(grouped.items())},
            }

        means = {
            profile: mean(self._mature_pageviews(row) for row in rows)
            for profile, rows in eligible.items()
        }
        candidate_profile = max(
            sorted(means),
            key=lambda name: (means[name], len(eligible[name])),
        )
        all_values = [
            self._mature_pageviews(row)
            for rows in grouped.values()
            for row in rows
        ]
        baseline_mean = mean(all_values) if all_values else 0.0
        candidate_mean = means[candidate_profile]
        if candidate_mean <= baseline_mean:
            return {
                "status": "NO_HISTORICAL_CHALLENGER",
                "candidate_profile": candidate_profile,
                "candidate_mean": candidate_mean,
                "baseline_mean": baseline_mean,
            }

        state = _read_json(self.trial_path)
        experiment_id = str(state.get("experiment_id") or "")
        supersedes_experiment_id = None
        supersedes_candidate_id = None
        if experiment_id:
            try:
                existing_experiment = self.runtime.evolution_experiment_manager.get(experiment_id)
            except (AttributeError, KeyError):
                existing_experiment = None
            if existing_experiment is not None:
                existing_profile = str(state.get("candidate_profile") or "")
                if existing_profile == candidate_profile:
                    return {
                        "status": "EXISTING",
                        "experiment_id": experiment_id,
                        "stage": _enum_value(existing_experiment.stage),
                        "candidate_profile": existing_profile,
                        "candidate_id": state.get("candidate_id"),
                        "historical_samples": len(mature),
                    }
                if existing_experiment.samples or self._trial_has_committed_action(state):
                    return {
                        "status": "EXISTING_BROADER_EVIDENCE_CONFLICT",
                        "experiment_id": experiment_id,
                        "stage": _enum_value(existing_experiment.stage),
                        "candidate_profile": existing_profile,
                        "broader_evidence_candidate_profile": candidate_profile,
                        "reason": "existing prospective trial already has committed action or outcome evidence",
                    }
                supersedes_experiment_id = experiment_id
                supersedes_candidate_id = str(state.get("candidate_id") or "") or None

        mature_outcome_ids = {
            str(row.get("outcome_id") or "")
            for row in mature
            if str(row.get("outcome_id") or "")
        }
        mature_prediction_ids = {
            str(row.get("prediction_id") or "")
            for row in mature
            if str(row.get("prediction_id") or "")
        }
        evidence_rows = []
        for item in getattr(self.runtime, "evolution_evidence", ()) or ():
            if not item.learning_eligible:
                continue
            refs = set(str(x) for x in getattr(item, "evidence_refs", ()) or ())
            outcome_id = str(getattr(item, "outcome_id", "") or "")
            from_strategy_outcome = outcome_id in mature_outcome_ids
            from_prediction_outcome = (
                outcome_id.endswith(":24h")
                and any(prediction_id in refs for prediction_id in mature_prediction_ids)
            )
            if from_strategy_outcome or from_prediction_outcome:
                evidence_rows.append(item)
        evidence_refs = list(dict.fromkeys(item.evidence_id for item in evidence_rows))
        experience_refs = list(dict.fromkeys(item.experience_id for item in evidence_rows))
        if not evidence_refs or not experience_refs:
            return {"status": "NO_CANONICAL_EVIDENCE_AFTER_BACKFILL"}

        baseline = self.ensure_baseline(evidence_refs=evidence_refs[:64])
        causes = [
            make_cause(
                f"strategy profile {candidate_profile} may improve 24h pageviews",
                relation=CausalRelation.CORRELATION,
                supporting_evidence_refs=evidence_refs,
                confidence=0.45,
            ),
            make_cause(
                "topic mix, timing, source quality, or traffic variance may explain the observed association",
                relation=CausalRelation.UNKNOWN,
                supporting_evidence_refs=(),
                counter_evidence_refs=(),
                confidence=0.55,
            ),
        ]
        diagnosis = self.runtime.record_evolution_diagnosis(
            experience_refs=experience_refs,
            evidence_refs=evidence_refs,
            observed_pattern=(
                f"{candidate_profile} has associated mean 24h pageviews {candidate_mean:.3f} "
                f"versus overall adaptive baseline {baseline_mean:.3f}"
            ),
            causes=causes,
            unknowns=[
                "topic mix",
                "publication timing",
                "external traffic variance",
                "source quality",
            ],
            discriminating_experiment=(
                f"prospectively alternate current adaptive strategy with fixed {candidate_profile} "
                "and compare mature 24h pageviews under one frozen metric contract"
            ),
            applicability_scope="website-content-strategy",
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            confidence=0.45,
        )
        lift = (candidate_mean - baseline_mean) / max(baseline_mean, 1.0)
        fact = self.runtime.observe_evolution_fact(
            kind="OPPORTUNITY",
            subject="website-content-strategy",
            summary=(
                f"Real mature outcomes suggest testing {candidate_profile}; "
                f"observational relative lift={lift:.3f}, not yet causal."
            ),
            severity=min(0.9, 0.76 + max(0.0, lift) * 0.2),
            evidence_refs=evidence_refs[:64],
            recurrence_key="content-strategy:prospective-profile-challenger",
            need_hint="Run a prospective baseline-vs-profile strategy trial before changing durable behavior.",
            target_capability=STRATEGY_CAPABILITY_ID,
            metadata={
                "candidate_profile": candidate_profile,
                "candidate_mean_pageviews_24h": candidate_mean,
                "baseline_mean_pageviews_24h": baseline_mean,
                "historical_sample_count": len(mature),
                "causal_claim": False,
            },
        )
        need = next(
            (
                item for item in getattr(self.runtime, "evolution_needs", ()) or ()
                if str(getattr(item, "target_capability", "") or "")
                == STRATEGY_CAPABILITY_ID
                and _enum_value(getattr(item, "status", "")) == "OPEN"
            ),
            None,
        )
        hypothesis = self.runtime.propose_evolution_insight(
            statement=(
                f"If {candidate_profile} is genuinely better than the adaptive baseline, "
                "a prospective randomized trial should reproduce the 24h pageview lift."
            ),
            proposed_change=(
                f"Use {candidate_profile} only on candidate-arm cycles; keep existing adaptive "
                "strategy on baseline cycles until Runtime promotion."
            ),
            evidence_refs=evidence_refs[:64],
            need_id=None if need is None else need.need_id,
            expected_fitness={
                "pageviews_24h": max(0.0, candidate_mean - baseline_mean),
            },
            confidence=0.45,
        )
        candidate = self.runtime.propose_evolution_candidate(
            requirement=MutationRequirement.JUDGMENT_RULE,
            target=PROFILE_IMPL_PREFIX + candidate_profile,
            description=(
                f"Prefer {candidate_profile} for Website content if it beats the existing "
                "adaptive selector in a prospective mature-outcome trial."
            ),
            baseline_ref=str(getattr(baseline, "version_id", STRATEGY_BASELINE_IMPL)),
            rollback_plan=(
                "restore the registered adaptive content-strategy baseline capability version"
            ),
            evidence_refs=evidence_refs[:64],
            expected_fitness={"pageviews_24h": candidate_mean},
            protected_invariants=[
                "publication-admission",
                "evidence-grounding",
                "authorization-ceiling",
            ],
            need_id=None if need is None else need.need_id,
            diagnosis_id=diagnosis.diagnosis_id,
            hypothesis_id=hypothesis.hypothesis_id,
            risk_budget=0.15,
            cost_budget=0.0,
        )
        start = as_of + timedelta(minutes=1)
        experiment = self.runtime.create_evolution_experiment(
            candidate.candidate_id,
            prospective_start_at=start,
            prospective_end_at=start + timedelta(days=30),
            metrics=[
                ExperimentMetric(
                    "pageviews_24h",
                    MetricDirection.HIGHER_BETTER,
                    tolerance=max(0.25, baseline_mean * 0.05),
                    primary=True,
                ),
            ],
            regression_evidence_refs=evidence_refs[:64],
            stop_conditions=[
                "publication or evidence safety regression",
                "three mature post-promotion outcomes below prospective expectations",
            ],
        )
        self.runtime.validate_evolution_replay(
            experiment.spec.experiment_id,
            evidence_refs=[
                diagnosis.diagnosis_id,
                hypothesis.hypothesis_id,
                *evidence_refs[:16],
            ],
        )
        self.runtime.enter_evolution_shadow(
            experiment.spec.experiment_id,
            evidence_refs=[
                "content-profile-exists:" + candidate_profile,
                "publication-authority-unchanged",
                "strategy-override-candidate-arm-only",
            ],
        )
        payload = {
            "schema_version": TRIAL_SCHEMA,
            "status": "SHADOW",
            "candidate_id": candidate.candidate_id,
            "candidate_profile": candidate_profile,
            "baseline_impl": STRATEGY_BASELINE_IMPL,
            "baseline_version_id": None if baseline is None else baseline.version_id,
            "baseline_historical_mean_pageviews_24h": baseline_mean,
            "candidate_historical_mean_pageviews_24h": candidate_mean,
            "historical_sample_count": len(mature),
            "minimum_samples_per_profile": EVOLUTION_MIN_HISTORICAL_PROFILE_SAMPLES,
            "experiment_id": experiment.spec.experiment_id,
            "prospective_start_at": experiment.spec.prospective_start_at.isoformat(),
            "prospective_end_at": experiment.spec.prospective_end_at.isoformat(),
            "diagnosis_id": diagnosis.diagnosis_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "created_at": as_of.isoformat(),
            "assignments": {},
            "post_promotion_regression_streak": 0,
            "supersedes_experiment_id": supersedes_experiment_id,
            "supersedes_candidate_id": supersedes_candidate_id,
        }
        _atomic_json(self.trial_path, payload)
        return {
            "status": (
                "PROSPECTIVE_TRIAL_REFRESHED_FROM_BROADER_EVIDENCE"
                if supersedes_experiment_id
                else "PROSPECTIVE_TRIAL_CREATED"
            ),
            **{k: payload[k] for k in (
                "candidate_id", "candidate_profile", "experiment_id",
                "baseline_historical_mean_pageviews_24h",
                "candidate_historical_mean_pageviews_24h",
                "historical_sample_count",
                "supersedes_experiment_id",
            )},
        }


__all__ = [
    "ContentEvolutionBridge",
    "STRATEGY_BASELINE_IMPL",
    "STRATEGY_CAPABILITY_ID",
    "PROFILE_IMPL_PREFIX",
]
