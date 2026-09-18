"""Read-only statistical evaluation of prospective Website opening experiments.

Only mature 24h pageview evidence produced from validated v2 publication
attribution is eligible. The evaluator can summarize evidence; it cannot change
future treatment assignment, publish content, or claim causal improvement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audience_reward import audience_reward, collect_mature_audience_observations
from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .content_admission import _write_once
from .web_editorial import digest
from .website_experiment import EXPERIMENT_VERSION, PROMOTED_EXPERIMENT_VERSION, TREATMENTS
from .website_experiment_governance import active_conclusion, record_conclusion, record_revocation
SCHEMA = "website-experiment-evaluation.v1"
PRIMARY_METRIC = "http_pageviews"
PRIMARY_OBJECTIVE = "website_pageviews"
PRIMARY_SOURCE = "nginx-access-log:v1"
PRIMARY_COHORT = "public-web-http-requests:v1"
PRIMARY_WINDOW = timedelta(hours=24)
MIN_SAMPLES_PER_TREATMENT = 12
MIN_RELATIVE_LIFT = 0.10
ALPHA = 0.05
MAX_EXACT_ASSIGNMENTS = 50_000
MONTE_CARLO_ASSIGNMENTS = 20_000
MAX_SAMPLES_PER_TREATMENT = 500


class TreatmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    treatment: Literal["reader_question_first", "evidence_first"]
    samples: int = Field(ge=0)
    mean_pageviews: float | None = Field(default=None, ge=0)
    median_pageviews: float | None = Field(default=None, ge=0)
    publication_ids: tuple[str, ...] = Field(max_length=MAX_SAMPLES_PER_TREATMENT)


class WebsiteExperimentEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["website-experiment-evaluation.v1"] = SCHEMA
    experiment_version: Literal["website-opening-mode:v1"] = EXPERIMENT_VERSION
    account: str = Field(min_length=1, max_length=200)
    evidence_cutoff: datetime | None = None
    status: Literal[
        "NO_EVIDENCE", "OBSERVING", "NO_CLEAR_DIFFERENCE",
        "EVIDENCE_FAVORS_TREATMENT", "INPUT_BUDGET_EXCEEDED",
    ]
    primary_metric: Literal["http_pageviews"] = PRIMARY_METRIC
    primary_window: Literal["24h"] = "24h"
    min_samples_per_treatment: int = MIN_SAMPLES_PER_TREATMENT
    treatments: tuple[TreatmentSummary, TreatmentSummary]
    mean_difference_reader_minus_evidence: float | None = None
    relative_lift_of_higher: float | None = Field(default=None, ge=0)
    permutation_p_value: float | None = Field(default=None, ge=0, le=1)
    favored_treatment: Literal["reader_question_first", "evidence_first"] | None = None
    evidence_refs: tuple[str, ...] = Field(max_length=1000)
    evidence_ref_count: int = Field(ge=0)
    evidence_refs_truncated: bool = False
    learning_authority: Literal[False] = False
    strategy_control: Literal[False] = False
    causal_claim: Literal[False] = False

    @field_validator("evidence_cutoff")
    @classmethod
    def aware_cutoff(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("evidence cutoff must be timezone-aware")
        return value


@dataclass(frozen=True)
class _Sample:
    treatment: str
    value: float
    publication_id: str
    published_at: datetime
    observed_at: datetime
    evidence_refs: tuple[str, ...]


class WebsiteExperimentEvaluator:
    """Evaluate one pre-registered 24h opening-mode experiment without control."""

    def __init__(self, *, state_root: Path, account: str,
                 min_samples_per_treatment: int = MIN_SAMPLES_PER_TREATMENT) -> None:
        self.state_root = Path(state_root)
        self.account = str(account).strip().rstrip("/")
        if not self.account:
            raise ValueError("Website experiment account must be non-empty")
        if not 2 <= min_samples_per_treatment <= MAX_SAMPLES_PER_TREATMENT:
            raise ValueError("min_samples_per_treatment out of bounds")
        self.min_samples = int(min_samples_per_treatment)
        self.store = ChannelEvidenceStore(self.state_root / "content" / "channel_evidence")
        self.output_root = self.state_root / "content" / "website_experiment_evaluations"

    def evaluate(self, *, as_of: datetime) -> dict:
        if as_of.utcoffset() is None:
            raise ValueError("evaluation cutoff must be timezone-aware")
        as_of = as_of.astimezone(timezone.utc)
        samples = self._samples(as_of, experiment_version=EXPERIMENT_VERSION)
        grouped = {name: [] for name in TREATMENTS}
        for sample in samples:
            grouped[sample.treatment].append(sample)
        if any(len(grouped[name]) > MAX_SAMPLES_PER_TREATMENT for name in TREATMENTS):
            evaluation = self._budget_evaluation(grouped)
            return self._with_governance(self._persist(evaluation), as_of)

        if not samples:
            evaluation = self._build(
                grouped, status="NO_EVIDENCE", favored=None,
                difference=None, relative_lift=None, p_value=None,
            )
            return self._with_governance(evaluation.model_dump(mode="json"), as_of)

        counts = [len(grouped[name]) for name in TREATMENTS]
        if min(counts) < self.min_samples:
            evaluation = self._build(
                grouped, status="OBSERVING", favored=None,
                difference=None, relative_lift=None, p_value=None,
            )
            return self._with_governance(self._persist(evaluation), as_of)

        # Fixed-horizon v1: freeze the first min_samples mature observations per arm.
        # Later evidence cannot repeatedly re-test the same hypothesis until it crosses alpha.
        decision_grouped = {
            name: grouped[name][: self.min_samples] for name in TREATMENTS
        }
        reader = [sample.value for sample in decision_grouped["reader_question_first"]]
        evidence = [sample.value for sample in decision_grouped["evidence_first"]]
        difference = mean(reader) - mean(evidence)
        high, low = (reader, evidence) if difference >= 0 else (evidence, reader)
        relative_lift = (mean(high) - mean(low)) / max(mean(low), 1.0)
        p_value = _permutation_p_value(
            reader, evidence,
            seed_material="|".join(sorted(
                sample.publication_id
                for name in TREATMENTS for sample in decision_grouped[name]
            )),
        )
        favored = None
        status = "NO_CLEAR_DIFFERENCE"
        if p_value <= ALPHA and relative_lift >= MIN_RELATIVE_LIFT and difference != 0:
            favored = "reader_question_first" if difference > 0 else "evidence_first"
            status = "EVIDENCE_FAVORS_TREATMENT"
        evaluation = self._build(
            decision_grouped, status=status, favored=favored,
            difference=difference, relative_lift=relative_lift, p_value=p_value,
        )
        return self._with_governance(self._persist(evaluation), as_of)

    def _samples(self, as_of: datetime, *, experiment_version: str) -> list[_Sample]:
        expected = {f"{experiment_version}:{name}": name for name in TREATMENTS}
        publications = self._validated_publications()
        rows = []
        for row in self.store.as_of(as_of):
            treatment = expected.get(row.strategy_version)
            publication = publications.get(row.publication_id)
            if treatment is None or publication is None or not _eligible(row, account=self.account):
                continue
            attr = publication["outcome_attribution"]
            experiment = attr["experiment"]
            try:
                publication_time = datetime.fromisoformat(attr["published_at"]).astimezone(timezone.utc)
            except (KeyError, ValueError):
                continue
            if (
                raw_artifact_id(publication) != row.artifact_id
                or publication_time != row.published_at.astimezone(timezone.utc)
                or attr.get("topic") != row.topic
                or attr.get("title_version") != row.title_version
                or attr.get("structure_version") != row.structure_version
                or attr.get("strategy_version") != row.strategy_version
                or attr.get("published_content_sha256") != row.published_content_sha256
                or attr.get("agent_candidate_sha256") != row.agent_candidate_sha256
                or experiment.get("strategy_version") != row.strategy_version
            ):
                continue
            experiment_ref = f"website-experiment:{experiment['experiment_id']}"
            if row.value is None or tuple(ref for ref in row.evidence_refs if ref.startswith("website-experiment:")) != (experiment_ref,):
                continue
            rows.append(_Sample(
                treatment=treatment, value=float(row.value),
                publication_id=row.publication_id,
                published_at=row.published_at.astimezone(timezone.utc),
                observed_at=row.observed_at.astimezone(timezone.utc),
                evidence_refs=tuple(row.evidence_refs),
            ))
        rows.sort(key=lambda row: (row.published_at, row.publication_id))
        return rows

    def _validated_publications(self) -> dict[str, dict]:
        from .website_experiment import WebsiteExperimentBinding

        rows = {}
        root = self.state_root / "content" / "continuous" / "cycles"
        for path in sorted(root.glob("*/publication.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
            if not isinstance(attr, dict) or attr.get("schema_version") != "website-publication-attribution.v2":
                continue
            binding = WebsiteExperimentBinding.model_validate(attr.get("experiment"))
            if attr.get("strategy_version") != binding.strategy_version:
                raise ValueError("Website experiment publication strategy does not match binding")
            if raw.get("revision") in rows:
                raise ValueError("duplicate Website experiment publication revision")
            rows[raw["revision"]] = raw
        return rows

    def _budget_evaluation(self, grouped) -> WebsiteExperimentEvaluation:
        summaries = []
        cutoff = None
        for name in TREATMENTS:
            bucket = grouped[name]
            summaries.append(TreatmentSummary(
                treatment=name, samples=len(bucket), mean_pageviews=None, median_pageviews=None,
                publication_ids=tuple(
                    sample.publication_id for sample in bucket[:MAX_SAMPLES_PER_TREATMENT]
                ),
            ))
            for sample in bucket:
                cutoff = sample.observed_at if cutoff is None or sample.observed_at > cutoff else cutoff
        all_refs = sorted({
            ref for name in TREATMENTS for sample in grouped[name] for ref in sample.evidence_refs
        })
        return WebsiteExperimentEvaluation(
            account=self.account, evidence_cutoff=cutoff, status="INPUT_BUDGET_EXCEEDED",
            min_samples_per_treatment=self.min_samples, treatments=tuple(summaries),
            evidence_refs=tuple(all_refs[:1000]), evidence_ref_count=len(all_refs),
            evidence_refs_truncated=len(all_refs) > 1000,
        )

    def _build(self, grouped, *, status, favored, difference, relative_lift, p_value):
        summaries = []
        refs = []
        cutoff = None
        for name in TREATMENTS:
            bucket = grouped[name]
            values = [sample.value for sample in bucket]
            summaries.append(TreatmentSummary(
                treatment=name,
                samples=len(values),
                mean_pageviews=mean(values) if values else None,
                median_pageviews=median(values) if values else None,
                publication_ids=tuple(sample.publication_id for sample in bucket),
            ))
            for sample in bucket:
                refs.extend(sample.evidence_refs)
                cutoff = sample.observed_at if cutoff is None or sample.observed_at > cutoff else cutoff
        all_refs = sorted(set(refs))
        return WebsiteExperimentEvaluation(
            account=self.account,
            evidence_cutoff=cutoff,
            status=status,
            min_samples_per_treatment=self.min_samples,
            treatments=tuple(summaries),
            mean_difference_reader_minus_evidence=difference,
            relative_lift_of_higher=relative_lift,
            permutation_p_value=p_value,
            favored_treatment=favored,
            evidence_refs=tuple(all_refs[:1000]),
            evidence_ref_count=len(all_refs),
            evidence_refs_truncated=len(all_refs) > 1000,
        )

    def _audience_reward_samples(self, as_of: datetime, *, experiment_version: str) -> list[_Sample]:
        """Build fixed-horizon samples from reach + 30s engagement, never missing-as-zero."""
        expected = {f"{experiment_version}:{name}": name for name in TREATMENTS}
        publications = self._validated_publications()
        observations = collect_mature_audience_observations(
            state_root=self.state_root, account=self.account, as_of=as_of
        )
        ordered = sorted(
            observations.values(),
            key=lambda row: (row.published_at, row.publication_id),
        )
        prior_pageviews: list[float] = []
        rows: list[_Sample] = []
        for observation in ordered:
            baseline = (
                median(prior_pageviews[-10:]) if prior_pageviews else None
            )
            reward = audience_reward(
                pageviews=observation.pageviews,
                baseline_pageviews=baseline,
                engagement_rate_value=observation.engagement_rate,
            )
            prior_pageviews.append(observation.pageviews)
            if reward is None:
                continue
            publication = publications.get(observation.publication_id)
            if publication is None:
                continue
            attr = publication.get("outcome_attribution") or {}
            experiment = attr.get("experiment") or {}
            treatment = expected.get(str(experiment.get("strategy_version") or ""))
            if treatment is None:
                continue
            refs = tuple(dict.fromkeys([
                *observation.evidence_refs,
                f"website-experiment:{experiment.get('experiment_id')}",
            ]))
            rows.append(_Sample(
                treatment=treatment,
                value=float(reward),
                publication_id=observation.publication_id,
                published_at=observation.published_at,
                observed_at=observation.observed_at,
                evidence_refs=refs,
            ))
        return rows

    def _audience_reward_evaluation(self, *, as_of: datetime, experiment_version: str) -> dict:
        samples = self._audience_reward_samples(as_of, experiment_version=experiment_version)
        grouped = {name: [] for name in TREATMENTS}
        for sample in samples:
            grouped[sample.treatment].append(sample)
        counts = {name: len(grouped[name]) for name in TREATMENTS}
        base = {
            "schema_version": "website-audience-reward-evaluation.v1",
            "experiment_version": experiment_version,
            "primary_metric": "audience_reward:v1",
            "reward_definition": "harmonic(reach_vs_trailing_pageview_baseline, engaged_readers_30s/tracked_pageviews)",
            "samples": counts,
            "min_samples_per_treatment": self.min_samples,
            "causal_claim": False,
        }
        if not samples:
            payload = {**base, "status": "NO_EVIDENCE", "favored_treatment": None}
        elif min(counts.values()) < self.min_samples:
            payload = {**base, "status": "OBSERVING", "favored_treatment": None}
        else:
            decision_grouped = {
                name: grouped[name][: self.min_samples] for name in TREATMENTS
            }
            reader = [sample.value for sample in decision_grouped["reader_question_first"]]
            evidence = [sample.value for sample in decision_grouped["evidence_first"]]
            difference = mean(reader) - mean(evidence)
            high, low = (reader, evidence) if difference >= 0 else (evidence, reader)
            low_mean = mean(low)
            relative_lift = (mean(high) - low_mean) / max(low_mean, 1e-6)
            p_value = _permutation_p_value(
                reader, evidence,
                seed_material="audience|" + "|".join(sorted(
                    sample.publication_id
                    for name in TREATMENTS for sample in decision_grouped[name]
                )),
            )
            favored = None
            status = "NO_CLEAR_DIFFERENCE"
            if p_value <= ALPHA and relative_lift >= MIN_RELATIVE_LIFT and difference != 0:
                favored = "reader_question_first" if difference > 0 else "evidence_first"
                status = "EVIDENCE_FAVORS_TREATMENT"
            refs = tuple(sorted({
                ref
                for name in TREATMENTS
                for sample in decision_grouped[name]
                for ref in sample.evidence_refs
            }))
            payload = {
                **base,
                # Fixed-horizon decision artifact: once both arms reach the gate,
                # later observations cannot alter its identity or mint a new
                # conclusion from the same hypothesis.
                "samples": {name: len(decision_grouped[name]) for name in TREATMENTS},
                "status": status,
                "favored_treatment": favored,
                "mean_difference_reader_minus_evidence": difference,
                "relative_lift_of_higher": relative_lift,
                "permutation_p_value": p_value,
                "evidence_cutoff": max(
                    sample.observed_at
                    for name in TREATMENTS for sample in decision_grouped[name]
                ).isoformat(),
                "publication_ids": {
                    name: [sample.publication_id for sample in decision_grouped[name]]
                    for name in TREATMENTS
                },
                "evidence_refs": list(refs[:1000]),
            }
        evaluation_id = digest(payload)
        output_root = self.state_root / "content" / "website_experiment_audience_evaluations"
        _write_once(
            output_root / f"{evaluation_id}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        return {
            **payload,
            "evaluation_ref": f"website-audience-reward-evaluation:sha256:{evaluation_id}",
        }

    def _persist(self, evaluation: WebsiteExperimentEvaluation) -> dict:
        payload = evaluation.model_dump(mode="json")
        evaluation_id = digest(payload)
        _write_once(
            self.output_root / f"{evaluation_id}.json",
            evaluation.model_dump_json(indent=2) + "\n",
        )
        return {**payload, "evaluation_ref": f"website-experiment-evaluation:sha256:{evaluation_id}"}


    def _with_governance(self, result: dict, as_of: datetime) -> dict:
        audience_evaluation = self._audience_reward_evaluation(
            as_of=as_of, experiment_version=EXPERIMENT_VERSION
        )
        if audience_evaluation.get("status") == "EVIDENCE_FAVORS_TREATMENT":
            record_conclusion(
                state_root=self.state_root,
                evaluation=audience_evaluation,
                concluded_at=as_of,
            )
        conclusion = active_conclusion(self.state_root)
        governed = dict(result)
        governed["audience_reward_evaluation"] = audience_evaluation
        if conclusion is None:
            return governed
        governed["experiment_conclusion"] = conclusion.model_dump(mode="json")
        governed["promotion_monitor"] = self._monitor_promoted_holdout(
            as_of=as_of, conclusion=conclusion
        )
        return governed

    def _monitor_promoted_holdout(self, *, as_of: datetime, conclusion) -> dict:
        evaluation_payload = self._audience_reward_evaluation(
            as_of=as_of, experiment_version=PROMOTED_EXPERIMENT_VERSION
        )
        base = {
            **evaluation_payload,
            "schema_version": "website-promotion-monitor.v2",
            "conclusion_id": conclusion.conclusion_id,
            "champion_treatment": conclusion.favored_treatment,
        }
        favored = evaluation_payload.get("favored_treatment")
        if (
            evaluation_payload.get("status") == "EVIDENCE_FAVORS_TREATMENT"
            and favored is not None
            and favored != conclusion.favored_treatment
        ):
            refs = tuple(str(ref) for ref in evaluation_payload.get("evidence_refs") or ())
            revocation = record_revocation(
                state_root=self.state_root,
                conclusion=conclusion,
                favored_treatment=str(favored),
                evaluation_payload=base,
                revoked_at=as_of,
                evidence_refs=refs,
            )
            base["status"] = "EVIDENCE_FAVORS_HOLDOUT"
            base["revocation"] = revocation.model_dump(mode="json")
        elif (
            evaluation_payload.get("status") == "EVIDENCE_FAVORS_TREATMENT"
            and favored == conclusion.favored_treatment
        ):
            base["status"] = "CHAMPION_CONFIRMED"
        return base


def raw_artifact_id(publication: dict) -> str | None:
    value = publication.get("artifact_id")
    return value if isinstance(value, str) and value else None


def _eligible(row: ChannelEvidence, *, account: str) -> bool:
    if not (
        row.channel == "website"
        and row.account.rstrip("/") == account
        and row.metric == PRIMARY_METRIC
        and row.objective == PRIMARY_OBJECTIVE
        and row.source == PRIMARY_SOURCE
        and row.distribution_cohort == PRIMARY_COHORT
        and row.unit == "count"
        and row.agent_candidate_sha256 == row.published_content_sha256
    ):
        return False
    if row.window_start != row.published_at:
        return False
    return row.window_end - row.window_start == PRIMARY_WINDOW


def _permutation_p_value(a: list[float], b: list[float], *, seed_material: str) -> float:
    if not a or not b:
        raise ValueError("both treatment groups are required")
    values = tuple(a + b)
    n_a = len(a)
    n_b = len(b)
    observed = abs(mean(a) - mean(b))
    if observed == 0:
        return 1.0
    total_sum = sum(values)
    assignments = math.comb(len(values), n_a)
    tolerance = 1e-12

    def difference(indexes) -> float:
        sum_a = sum(values[index] for index in indexes)
        return abs(sum_a / n_a - (total_sum - sum_a) / n_b)

    if assignments <= MAX_EXACT_ASSIGNMENTS:
        exceed = sum(
            1 for indexes in combinations(range(len(values)), n_a)
            if difference(indexes) + tolerance >= observed
        )
        return exceed / assignments

    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    exceed = 0
    population = range(len(values))
    for _ in range(MONTE_CARLO_ASSIGNMENTS):
        indexes = rng.sample(population, n_a)
        if difference(indexes) + tolerance >= observed:
            exceed += 1
    return (exceed + 1) / (MONTE_CARLO_ASSIGNMENTS + 1)


__all__ = [
    "WebsiteExperimentEvaluator", "WebsiteExperimentEvaluation", "TreatmentSummary",
    "MIN_SAMPLES_PER_TREATMENT", "PRIMARY_WINDOW",
]
