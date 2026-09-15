import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore
from wechat_traffic_agent.channels.web import StaticWebsiteConfig
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop as GoalContentLoop
from wechat_traffic_agent.web_editorial import digest
from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
from wechat_traffic_agent.website_experiment import (
    WebsiteExperimentBinding,
    bind_website_experiment,
    experiment_compliance,
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


def test_publisher_recomputes_compliance_and_rejects_forged_pass(tmp_path):
    binding = bind_website_experiment(
        tmp_path / "binding.json", cycle="v2-790", decided_at=NOW
    )
    artifact = _artifact(binding, compliant=False)
    publisher = _publisher(tmp_path)
    before = (publisher.public / "current").resolve()
    forged = _experience(artifact, binding, compliance_status="PASS")
    with pytest.raises(ValueError, match="actual Website experiment compliance"):
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
    assert result["recorded"][0]["pageviews"] == 1
    rows = ChannelEvidenceStore(
        tmp_path / "state/content/channel_evidence"
    ).as_of(published_at + timedelta(hours=25))
    assert len(rows) == 1
    assert rows[0].strategy_version == binding.strategy_version
    assert f"website-experiment:{binding.experiment_id}" in rows[0].evidence_refs


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

    async def cognitive_probe(self, task, max_tokens):
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


def test_goal_loop_holds_noncompliant_treatment_without_site_activation(tmp_path):
    runtime, worker, publisher = _goal_worker(tmp_path, compliant=False)
    before = (publisher.public / "current").resolve()
    result = asyncio.run(worker.run_once(now=NOW))
    assert runtime.binding_seen_before_selection is True
    assert result["status"] == "HELD_BY_CHECKS"
    assert result["website_experiment_compliance"]["status"] == "FAIL"
    assert result["automated_checks_passed"] is False
    assert (publisher.public / "current").resolve() == before
    cycle = next((worker.root / "cycles").iterdir())
    assert not (cycle / "publication.json").exists()
