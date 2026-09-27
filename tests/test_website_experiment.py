import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.channels.web import StaticWebsiteConfig
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop as GoalContentLoop
from wechat_traffic_agent.web_editorial import digest
from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
from wechat_traffic_agent.website_experiment import (
    EXPERIMENT_VERSION, WebsiteExperimentBinding,
    bind_website_experiment, experiment_compliance,
)
import wechat_traffic_agent.website_experiment_evaluation as evaluation_module
from wechat_traffic_agent.website_experiment_evaluation import (
    WebsiteExperimentEvaluator, MIN_SAMPLES_PER_TREATMENT,
)
from test_content_loop import NOW, PARAGRAPH
from test_web_editorial import site


def _artifact(binding, *, compliant=True):
    if binding.treatment_value == "reader_question_first":
        first = "Reader question: What does this evidence actually support?"
    else:
        first = "Observation: The supplied evidence reports one bounded change."
    if not compliant:
        first = "This opening ignores the assigned treatment."
    return ContentArtifact(
        artifact_id="continuous-experiment",
        kind=ContentKind.ARTICLE,
        slug="continuous-experiment",
        title="A bounded Website experiment",
        summary="A bounded test of one opening mode.",
        sections=(ContentSection(heading="Evidence", paragraphs=(first,)),),
        tags=("evidence",),
        created_at=NOW,
        public_safe=False,
    )


def _publisher(tmp_path, *, clock=None):
    artifacts, web = site(tmp_path)
    public = tmp_path / "public"
    public.mkdir()
    (public / "current").symlink_to(web)
    config = StaticWebsiteConfig(
        site_name="Lab", description="Evidence",
        base_url="https://example.com", indexable=False,
    )
    return AutomaticWebsitePublisher(
        state_root=tmp_path / "state", public_root=public,
        starter_path=artifacts, config=config, clock=clock,
    )


def _experience(artifact, binding, *, compliance_status="PASS"):
    check = experiment_compliance(artifact, binding)
    check["status"] = compliance_status
    return {
        "lesson_id": "lesson-v1",
        "draft_sha256": digest(artifact.model_dump(mode="json")),
        "automated_checks_passed": True,
        "prospective_experiment": binding.model_dump(mode="json"),
        "website_experiment_compliance": check,
    }


def _combined(ts, route, *, ua="Mozilla/5.0", status=200):
    stamp = ts.strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'203.0.113.7 - - [{stamp}] "GET {route} HTTP/1.1" {status} 123 "-" "{ua}"\n'


def test_binding_is_restart_stable_and_cycle_derived(tmp_path):
    path = tmp_path / "binding.json"
    first = bind_website_experiment(path, cycle="v2-123", decided_at=NOW)
    restored = bind_website_experiment(
        path, cycle="v2-123", decided_at=NOW + timedelta(hours=1)
    )
    assert restored == first
    raw = json.loads(path.read_text())
    raw["treatment_value"] = (
        "evidence_first" if first.treatment_value == "reader_question_first"
        else "reader_question_first"
    )
    with pytest.raises(ValidationError):
        WebsiteExperimentBinding.model_validate(raw)


def test_compliance_checks_only_the_bound_opening_surface(tmp_path):
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-456", decided_at=NOW
    )
    assert experiment_compliance(_artifact(binding), binding)["status"] == "PASS"
    assert experiment_compliance(
        _artifact(binding, compliant=False), binding
    )["status"] == "FAIL"


def test_publisher_rejects_mismatched_or_failed_experiment(tmp_path):
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-789", decided_at=NOW
    )
    artifact = _artifact(binding)
    publisher = _publisher(tmp_path)
    before = (publisher.public / "current").resolve()
    experience = _experience(artifact, binding, compliance_status="FAIL")
    with pytest.raises(ValueError, match="experiment compliance"):
        publisher.publish(
            artifact=artifact, experience=experience,
            cycle_root=tmp_path / "state/content/continuous/cycles/v2-789",
        )
    assert (publisher.public / "current").resolve() == before


def test_publisher_publishes_genuine_experiment_miss_without_enrolling_sample(tmp_path):
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-789b", decided_at=NOW
    )
    artifact = _artifact(binding, compliant=False)
    publisher = _publisher(tmp_path)
    before = (publisher.public / "current").resolve()
    receipt = publisher.publish(
        artifact=artifact,
        experience=_experience(artifact, binding, compliance_status="FAIL"),
        cycle_root=tmp_path / "state/content/continuous/cycles/v2-789b",
    )
    assert (publisher.public / "current").resolve() != before
    assert receipt["outcome_attribution"]["schema_version"] == "website-publication-attribution.v1"
    assert "experiment" not in receipt["outcome_attribution"]


def test_publisher_recomputes_compliance_and_rejects_forged_pass(tmp_path):
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-790", decided_at=NOW
    )
    artifact = _artifact(binding, compliant=False)
    publisher = _publisher(tmp_path)
    before = (publisher.public / "current").resolve()
    forged = _experience(artifact, binding, compliance_status="PASS")
    with pytest.raises(ValueError, match="persisted Website experiment compliance"):
        publisher.publish(
            artifact=artifact, experience=forged,
            cycle_root=tmp_path / "state/content/continuous/cycles/v2-790",
        )
    assert (publisher.public / "current").resolve() == before


def test_v2_publication_and_outcome_keep_stable_treatment_identity(tmp_path):
    published_at = NOW + timedelta(minutes=5)
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-999", decided_at=NOW
    )
    artifact = _artifact(binding)
    publisher = _publisher(tmp_path, clock=lambda: published_at)
    cycle_root = tmp_path / "state/content/continuous/cycles/v2-999"
    receipt = publisher.publish(
        artifact=artifact,
        experience=_experience(artifact, binding),
        cycle_root=cycle_root,
    )
    attr = receipt["outcome_attribution"]
    assert attr["schema_version"] == "website-publication-attribution.v2"
    assert attr["strategy_version"] == binding.strategy_version
    assert attr["practice_version"] == "lesson:lesson-v1"
    assert attr["experiment"] == binding.model_dump(mode="json")

    route = attr["public_route"]
    log = tmp_path / "access.log"
    log.write_text("".join([
        _combined(published_at - timedelta(minutes=1), "/"),
        _combined(published_at + timedelta(hours=1), route),
        _combined(published_at + timedelta(hours=25), "/"),
    ]))
    result = WebsiteOutcomeCollector(
        state_root=tmp_path / "state", access_log=log,
        account="https://example.com",
    ).collect(now=published_at + timedelta(hours=25))
    assert result["status"] == "RECORDED"
    recorded = {row["window"]: row["pageviews"] for row in result["recorded"]}
    assert recorded["24h"] == 1
    rows = ChannelEvidenceStore(
        tmp_path / "state/content/channel_evidence"
    ).as_of(published_at + timedelta(hours=25))
    assert len(rows) == 3
    assert all(row.strategy_version == binding.strategy_version for row in rows)
    assert all(f"website-experiment:{binding.experiment_id}" in row.evidence_refs for row in rows)


class _GoalExplorer:
    async def collect(self, *, now, active):
        return {
            "items": [{
                "evidence_id": "web:test",
                "title": "Fresh bounded evidence",
                "summary": "The publisher reports one bounded observation.",
                "source": "example",
                "source_kind": "web-search",
                "source_revision": "rev-1",
                "url": "https://example.org/evidence",
                "observed_at": now.isoformat(),
                "evidence_refs": ["source:test"],
            }],
            "failures": [],
        }

    async def filter_for_goal(self, *, runtime, goal_topic, topic_context, candidates):
        return {
            "items": list(candidates),
            "matches": ["web:test"],
            "candidate_count": len(candidates),
        }


class _GoalRuntime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")
    def __init__(self, state_root, *, compliant=True):
        self.state_root = state_root
        self.compliant = compliant
        self.calls = []
        self.binding_seen_before_selection = False

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        if task.startswith("LEARN_WRITING:"):
            return json.dumps({
                "techniques": [{
                    "name": "Separate observations",
                    "instruction": "Separate facts from interpretation.",
                    "before": "This always works.",
                    "after": "The evidence reports one observation.",
                    "check": "Locate the distinction.",
                }],
                "adaptation": "Keep the explanation concrete.",
            })
        if task.startswith("SELECT_EVIDENCE:"):
            bindings = list((self.state_root / "content/continuous/cycles").glob(
                "*/website_experiment.json"
            ))
            self.binding_seen_before_selection = bool(bindings)
            return json.dumps({
                "title": "Experiment evidence question",
                "evidence_id": "web:test",
                "topic": "rare",
                "related_ids": ["a0"],
                "reader_question": "What does this evidence actually support?",
                "gap": "Connect the observation to existing evidence interpretation.",
            })
        if task.startswith("CHECK_ARTICLE:"):
            return json.dumps({
                "grounded": True,
                "useful": True,
                "no_private_data": True,
                "no_investment_advice": True,
                "applications": [{
                    "technique_index": 0,
                    "article_quote": "Explanation of supplied evidence",
                    "explanation": "The article separates observation and interpretation.",
                }],
                "support": [{
                    "section_index": 0,
                    "source_id": "a0",
                    "source_quote": "Existing bounded evidence.",
                }],
                "next_practice": "Make the opening constraint more explicit next time.",
                "rationale": "The article remains source-grounded.",
            })
        prefix = ""
        if self.compliant:
            prefix = (
                "Reader question: What does this evidence actually support? "
                if '"treatment_value": "reader_question_first"' in task
                else "Observation: The supplied evidence reports one bounded change. "
            )
        return json.dumps({
            "title": "Experiment evidence question",
            "summary": "A bounded interpretation of fresh evidence.",
            "sections": [{
                "heading": "Explanation",
                "paragraphs": [prefix + PARAGRAPH],
            }],
            "topic": "Experiment evidence question",
            "source_urls": [],
            "disclosure": None,
        })

    def persist(self):
        return None


def _goal_worker(tmp_path, *, compliant=True):
    artifacts, web = site(tmp_path)
    public = tmp_path / "goal-public"
    public.mkdir()
    (public / "current").symlink_to(web)
    state = tmp_path / "goal-state"
    curriculum = tmp_path / "goal-practice.md"
    curriculum.write_text("Practice separating observations from interpretation.")
    config = StaticWebsiteConfig(
        site_name="Lab", description="Evidence",
        base_url="https://example.com", indexable=False,
    )
    publisher = AutomaticWebsitePublisher(
        state_root=state, public_root=public,
        starter_path=artifacts, config=config,
    )
    runtime = _GoalRuntime(state, compliant=compliant)
    worker = GoalContentLoop(
        runtime=runtime,
        state_root=state,
        artifacts_path=artifacts,
        site_root=public / "current",
        base_url="https://example.com",
        curriculum_path=curriculum,
        feeds=["https://example.org/feed"],
        publisher=publisher,
        explorer=_GoalExplorer(),
        website_experiments=True,
    )
    return runtime, worker, publisher


def test_goal_loop_binds_before_selection_and_publishes_compliant_treatment(tmp_path):
    runtime, worker, publisher = _goal_worker(tmp_path)
    result = asyncio.run(worker.run_once(now=NOW))
    assert runtime.binding_seen_before_selection is True
    assert result["status"] == "PUBLISHED"
    assert result["website_experiment_compliance"]["status"] == "PASS"
    assert result["publication"]["outcome_attribution"]["schema_version"] == "website-publication-attribution.v2"


def test_goal_loop_publishes_noncompliant_treatment_without_enrolling_experiment(tmp_path):
    runtime, worker, publisher = _goal_worker(tmp_path, compliant=False)
    before = (publisher.public / "current").resolve()
    result = asyncio.run(worker.run_once(now=NOW))
    assert runtime.binding_seen_before_selection is True
    assert result["status"] == "PUBLISHED"
    assert result["website_experiment_compliance"]["status"] == "FAIL"
    assert result["website_experiment_eligible"] is False
    assert result["automated_checks_passed"] is True
    assert result["publication"]["outcome_attribution"]["schema_version"] == "website-publication-attribution.v1"
    assert "experiment" not in result["publication"]["outcome_attribution"]
    assert (publisher.public / "current").resolve() != before
    cycle = next((worker.root / "cycles").iterdir())
    assert (cycle / "publication.json").exists()


def _record_evaluation_sample(root, *, treatment, index, value, window_hours=24,
                              candidate_matches=True, write_publication=True,
                              observed_delay_hours=0, extra_refs=()):
    published_at = NOW + timedelta(hours=index)
    binding = None
    for salt in range(32):
        cycle = f"eval-{treatment}-{index}-{salt}"
        candidate = bind_website_experiment(
            root / "eval-bindings" / f"{cycle}.json",
            cycle=cycle, decided_at=published_at - timedelta(minutes=5),
        )
        if candidate.treatment_value == treatment:
            binding = candidate
            break
    assert binding is not None
    publication_id = f"pub-{treatment}-{index}-{window_hours}"
    content_hash = ("a" if candidate_matches else "b") * 64
    if write_publication:
        cycle_root = root / "content/continuous/cycles" / binding.cycle
        cycle_root.mkdir(parents=True, exist_ok=True)
        receipt = {
            "revision": publication_id,
            "artifact_id": f"artifact-{treatment}-{index}",
            "outcome_attribution": {
                "schema_version": "website-publication-attribution.v2",
                "published_at": published_at.isoformat(),
                "public_route": f"/articles/{publication_id}/",
                "published_content_sha256": "a" * 64,
                "agent_candidate_sha256": content_hash,
                "topic": "evidence", "title_version": "title-v1",
                "structure_version": "structure-v1",
                "strategy_version": binding.strategy_version,
                "practice_version": "lesson:test",
                "experiment": binding.model_dump(mode="json"),
            },
        }
        (cycle_root / "publication.json").write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
    row = ChannelEvidence(
        channel="website", account="https://example.com", publication_id=publication_id,
        artifact_id=f"artifact-{treatment}-{index}", published_content_sha256="a" * 64,
        agent_candidate_sha256=content_hash, topic="evidence", title_version="title-v1",
        structure_version="structure-v1", strategy_version=binding.strategy_version,
        objective="website_pageviews", distribution_cohort="public-web-http-requests:v1",
        metric="http_pageviews", metric_definition="bounded pageviews", unit="count",
        value=float(value), published_at=published_at, window_start=published_at,
        window_end=published_at + timedelta(hours=window_hours),
        observed_at=published_at + timedelta(hours=window_hours + observed_delay_hours), revision=1,
        source="nginx-access-log:v1",
        evidence_refs=(f"website-experiment:{binding.experiment_id}", *tuple(extra_refs)),
    )
    ChannelEvidenceStore(root / "content/channel_evidence").record(row)
    return row


def test_experiment_evaluator_observes_until_both_arms_reach_minimum(tmp_path):
    evaluator = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com",
    )
    empty = evaluator.evaluate(as_of=NOW + timedelta(days=10))
    assert empty["status"] == "NO_EVIDENCE"
    assert empty["strategy_control"] is False
    for index in range(MIN_SAMPLES_PER_TREATMENT - 1):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=index, value=10,
        )
        _record_evaluation_sample(
            tmp_path, treatment="evidence_first", index=100 + index, value=1,
        )
    observing = evaluator.evaluate(as_of=NOW + timedelta(days=20))
    assert observing["status"] == "OBSERVING"
    assert [row["samples"] for row in observing["treatments"]] == [
        MIN_SAMPLES_PER_TREATMENT - 1, MIN_SAMPLES_PER_TREATMENT - 1,
    ]
    assert observing["favored_treatment"] is None


def test_experiment_evaluator_can_flag_clear_treatment_evidence(tmp_path):
    for index in range(MIN_SAMPLES_PER_TREATMENT):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=index, value=12,
        )
        _record_evaluation_sample(
            tmp_path, treatment="evidence_first", index=100 + index, value=1,
        )
    evaluator = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com",
    )
    first = evaluator.evaluate(as_of=NOW + timedelta(days=20))
    # Later contradictory samples cannot create repeated-peeking decisions in v1;
    # the first 12 mature observations per arm are the fixed primary cohort.
    for index in range(3):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=1000 + index, value=0,
        )
        _record_evaluation_sample(
            tmp_path, treatment="evidence_first", index=1100 + index, value=100,
        )
    second = evaluator.evaluate(as_of=NOW + timedelta(days=100))
    assert first["status"] == "EVIDENCE_FAVORS_TREATMENT"
    assert first["favored_treatment"] == "reader_question_first"
    assert first["permutation_p_value"] <= 0.05
    assert first["relative_lift_of_higher"] >= 0.10
    assert first["evaluation_ref"] == second["evaluation_ref"]
    assert second["favored_treatment"] == "reader_question_first"
    assert [row["samples"] for row in second["treatments"]] == [12, 12]
    assert first["learning_authority"] is False
    assert first["strategy_control"] is False
    assert first["causal_claim"] is False


def test_experiment_evaluator_reports_no_clear_difference_for_equal_outcomes(tmp_path):
    for index in range(MIN_SAMPLES_PER_TREATMENT):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=index, value=5,
        )
        _record_evaluation_sample(
            tmp_path, treatment="evidence_first", index=100 + index, value=5,
        )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com",
    ).evaluate(as_of=NOW + timedelta(days=20))
    assert result["status"] == "NO_CLEAR_DIFFERENCE"
    assert result["permutation_p_value"] == 1.0
    assert result["favored_treatment"] is None


def test_experiment_evaluator_excludes_wrong_window_and_editor_modified_samples(tmp_path):
    _record_evaluation_sample(
        tmp_path, treatment="reader_question_first", index=1, value=99, window_hours=48,
    )
    _record_evaluation_sample(
        tmp_path, treatment="evidence_first", index=2, value=99, candidate_matches=False,
    )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com",
    ).evaluate(as_of=NOW + timedelta(days=20))
    assert result["status"] == "NO_EVIDENCE"
    assert all(row["samples"] == 0 for row in result["treatments"])


def test_experiment_evaluator_rejects_evidence_without_v2_publication_receipt(tmp_path):
    _record_evaluation_sample(
        tmp_path, treatment="reader_question_first", index=1, value=99,
        write_publication=False,
    )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com", min_samples_per_treatment=2,
    ).evaluate(as_of=NOW + timedelta(days=10))
    assert result["status"] == "NO_EVIDENCE"
    assert all(row["samples"] == 0 for row in result["treatments"])


def test_fixed_horizon_uses_publication_order_not_delayed_collection_order(tmp_path):
    _record_evaluation_sample(
        tmp_path, treatment="reader_question_first", index=0, value=100,
        observed_delay_hours=200,
    )
    _record_evaluation_sample(
        tmp_path, treatment="reader_question_first", index=1, value=100,
        observed_delay_hours=100,
    )
    _record_evaluation_sample(
        tmp_path, treatment="reader_question_first", index=2, value=0,
    )
    _record_evaluation_sample(
        tmp_path, treatment="evidence_first", index=100, value=1,
    )
    _record_evaluation_sample(
        tmp_path, treatment="evidence_first", index=101, value=1,
    )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com", min_samples_per_treatment=2,
    ).evaluate(as_of=NOW + timedelta(days=20))
    reader = next(
        row for row in result["treatments"] if row["treatment"] == "reader_question_first"
    )
    assert reader["publication_ids"] == [
        "pub-reader_question_first-0-24",
        "pub-reader_question_first-1-24",
    ]
    assert reader["mean_pageviews"] == 100.0
    assert result["favored_treatment"] is None


def test_experiment_evaluator_budget_exceeded_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation_module, "MAX_SAMPLES_PER_TREATMENT", 2)
    for index in range(3):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=index, value=10,
        )
    _record_evaluation_sample(
        tmp_path, treatment="evidence_first", index=100, value=1,
    )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com", min_samples_per_treatment=2,
    ).evaluate(as_of=NOW + timedelta(days=20))
    assert result["status"] == "INPUT_BUDGET_EXCEEDED"
    reader = next(
        row for row in result["treatments"] if row["treatment"] == "reader_question_first"
    )
    assert reader["samples"] == 3
    assert len(reader["publication_ids"]) == 2
    assert reader["mean_pageviews"] is None
    assert result["strategy_control"] is False


def test_observing_evidence_refs_are_bounded_and_report_truncation(tmp_path):
    for index in range(21):
        _record_evaluation_sample(
            tmp_path, treatment="reader_question_first", index=index, value=10,
            extra_refs=tuple(f"support:{index}:{part}" for part in range(49)),
        )
    result = WebsiteExperimentEvaluator(
        state_root=tmp_path, account="https://example.com", min_samples_per_treatment=22,
    ).evaluate(as_of=NOW + timedelta(days=20))
    assert result["status"] == "OBSERVING"
    assert result["evidence_ref_count"] > 1000
    assert len(result["evidence_refs"]) == 1000
    assert result["evidence_refs_truncated"] is True
    assert result["strategy_control"] is False
