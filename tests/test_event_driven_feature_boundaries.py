"""Independently executable research and content-event contracts."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.content_cycle_completion import ContentCycleCompletion
from wechat_traffic_agent.content_lifecycle_events import (
    ContentEventRouter, DraftChecked, ContentCycleSettled,
)
from wechat_traffic_agent.research_evidence_service import (
    EvidencePolicies, EvidenceRequest, ResearchEvidenceService,
)
from wechat_traffic_agent.web_api.operator_resources import OperatorResourceHandlers


class FakeSearch:
    def __init__(self):
        self.candidates = []

    async def collect(self, *, now, active):
        raise AssertionError("owner evidence should avoid a new search")

    async def filter_for_goal(self, *, runtime, goal_topic, topic_context, candidates):
        self.candidates = candidates
        return {"items": candidates, "candidate_count": len(candidates), "matches": []}


class FakeTraffic:
    def build(self, *, website, as_of, trend_candidates):
        return SimpleNamespace(
            topics=(),
            model_dump=lambda **kwargs: {"topics": []},
        )


def test_research_evidence_service_uses_owner_evidence_without_host_or_browser(tmp_path):
    explorer = FakeSearch()
    events = []
    research = ResearchEvidenceService(
        explorer=explorer, inbox=object(), traffic_learning=FakeTraffic(),
        runtime=object(), mind=None, state_root=tmp_path,
        publication_surface="WEBSITE",
        activity=lambda *args, **kwargs: events.append((args, kwargs)),
        policies=EvidencePolicies(
            candidate_available=lambda inbox, row: True,
            focus_matches=lambda row, topic: True,
            focus_query=lambda goal, topic: "",
            focus_anchors=lambda topic: (),
            allow_output_fallback=lambda **kwargs: False,
        ),
    )
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    evidence = {"evidence_id": "e-1", "title": "A new source", "summary": "Evidence"}
    result = asyncio.run(research.run(EvidenceRequest(
        folder=tmp_path, cycle="cycle-1", now=now, cycle_now=now,
        snapshot={"topics": {}, "articles": []}, required_topic=None,
        public_goal={}, mind_plan={}, owner_goal=None,
        owner_seed_items=[evidence], prefetched_evidence_pool=None,
        owner_focus_fast_path=False,
    )))
    assert explorer.candidates == [evidence]
    assert result.evidence_pool["seed_source"] == "OWNER_AGENT_EPISODE_SEARCH_WORLD"
    assert result.exploration["items"] == [evidence]
    assert result.effective_topic is None
    assert not result.goal_active
    assert (tmp_path / "exploration.json").exists()


def test_lifecycle_event_router_is_typed_and_rejects_duplicate_subscribers(tmp_path):
    router = ContentEventRouter()
    observed = []
    router.subscribe(DraftChecked, "learning", lambda event: observed.append(event.cycle) or "ok")
    event = DraftChecked("cycle-1", "topic-1", True, (), "sha", 1, tmp_path)
    assert router.emit(event) == {"learning": "ok"}
    assert observed == ["cycle-1"]
    with pytest.raises(ValueError, match="duplicate"):
        router.subscribe(DraftChecked, "learning", lambda event: None)
    outcome = ContentCycleSettled("cycle-1", "topic-1", True, "PUBLISHED",
                                  (), (), "", 1, datetime.now(timezone.utc), True)
    assert router.emit(outcome) == {}


def test_cycle_completion_events_apply_learning_then_publish_then_process_memory(tmp_path):
    calls = []

    class Mind:
        def observe_process_experience(self, **kwargs):
            calls.append(("mind", kwargs))
            return {"status": "RECORDED"}

    class ProcessMemory:
        def observe_process_outcome(self, **kwargs):
            calls.append(("memory", kwargs))
            return {"status": "STORED"}

    class Publisher:
        def publish(self, **kwargs):
            calls.append(("publisher", kwargs))
            return {"event_id": "publication-1"}

    event = ContentCycleCompletion(
        publisher=Publisher(), mind=Mind(), memory_life=ProcessMemory(),
        activity=lambda *args, **kwargs: None,
    )
    now = datetime.now(timezone.utc)
    result = event.complete(
        {"status": "DRAFT_CREATED"}, artifact=SimpleNamespace(title="Test"),
        experience={"draft_sha256": "abcdef"}, folder=tmp_path,
        cycle="cycle-1", topic="finance", passed=True, evidence_id="e-1",
        evidence_refs=["e-2", "e-1"], runtime_calls=3,
        process_steps=["research", "publish"], failure_mode="", observed_at=now,
    )
    assert [kind for kind, _ in calls] == ["mind", "publisher", "memory"]
    assert calls[0][1]["evidence_refs"] == [
        "cycle:cycle-1", "evidence:e-1", "e-2", "e-1", "draft-sha256:abcdef",
    ]
    assert result["status"] == "PUBLISHED"
    assert result["publication"]["event_id"] == "publication-1"
    assert result["mind_experience"]["status"] == "RECORDED"
    assert result["memory_life"]["status"] == "STORED"
    assert json.loads((tmp_path / "mind_experience.json").read_text())["status"] == "RECORDED"


def test_held_draft_notifies_feedback_without_publish(tmp_path):
    calls = []
    class Memory:
        def observe_process_outcome(self, **kwargs):
            calls.append(kwargs)
            return {"status": "STORED"}
    class Publisher:
        def publish(self, **kwargs):
            raise AssertionError("held draft cannot publish")
    completion = ContentCycleCompletion(
        publisher=Publisher(), mind=None, memory_life=Memory(),
        activity=lambda *args, **kwargs: None,
    )
    outcome = completion.complete(
        {"status": "HELD_BY_CHECKS"},
        artifact=SimpleNamespace(title="Held"),
        experience={"draft_sha256": "hash"}, folder=tmp_path,
        cycle="cycle-2", topic="ai", passed=False, evidence_id="e-2",
        evidence_refs=(), runtime_calls=2,
        process_steps=[], failure_mode="NO_SOURCE", observed_at=datetime.now(timezone.utc),
    )
    assert outcome["status"] == "HELD_BY_CHECKS"
    assert outcome["memory_life"]["status"] == "STORED"
    assert calls[0]["outcome"] == "HELD_BY_CHECKS"
    assert calls[0]["failure_mode"] == "NO_SOURCE"


def test_operator_resource_endpoints_have_injected_auth_and_no_legacy_app():
    endpoints = OperatorResourceHandlers(
        console=None, wakeful=None,
        authorized=lambda request: False,
        write_allowed=lambda request: False,
    )
    response = asyncio.run(endpoints.operator_resource_decision(SimpleNamespace(headers={})))
    assert response.status_code == 401
    assert json.loads(response.body) == {"error": "authentication_required"}
    handlers = endpoints.routes()
    assert "operator_store_secret" in handlers
    assert "operator_logout" in handlers


def test_feature_modules_never_import_the_two_giant_entrypoint_modules():
    import ast
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    feature_files = [
        "research_evidence_service.py", "content_lifecycle_events.py",
        "content_feedback_subscribers.py", "content_cycle_completion.py",
        "web_api/operator_resources.py", "web_api/operator_browser.py",
        "web_api/operator_login.py",
    ]
    for filename in feature_files:
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "content_goal_loop" not in str(node.module), filename
                assert "live_agent" not in str(node.module), filename
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    filename, node.name, node.end_lineno - node.lineno + 1,
                )


def test_research_admission_falls_back_to_real_candidates_without_claiming_goal_success(tmp_path):
    class EmptyMatchSearch(FakeSearch):
        async def filter_for_goal(self, *, runtime, goal_topic, topic_context, candidates):
            return {"items": [], "candidate_count": len(candidates), "matches": []}

    research = ResearchEvidenceService(
        explorer=EmptyMatchSearch(), inbox=object(), traffic_learning=FakeTraffic(),
        runtime=object(), mind=None, state_root=tmp_path, publication_surface="WEBSITE",
        activity=lambda *args, **kwargs: None,
        policies=EvidencePolicies(
            candidate_available=lambda inbox, row: True,
            focus_matches=lambda row, topic: True,
            focus_query=lambda goal, topic: "",
            focus_anchors=lambda topic: (),
            allow_output_fallback=lambda **kwargs: True,
        ),
    )
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    request = EvidenceRequest(
        folder=tmp_path, cycle="cycle-2", now=now, cycle_now=now,
        snapshot={"topics": {}, "articles": []}, required_topic="ai",
        public_goal={"origin": "INTEREST"}, mind_plan={}, owner_goal=None,
        owner_seed_items=[{"evidence_id": "real-1", "title": "AI evaluation",
                           "summary": "source evidence"}],
        prefetched_evidence_pool=None, owner_focus_fast_path=False,
    )
    observations = asyncio.run(research.run(request))
    decision = asyncio.run(research.admit(request, observations, goal_origin="INTEREST"))
    assert decision.early_result is None
    assert [row["evidence_id"] for row in decision.evidence] == ["real-1"]
    assert decision.exploration["fallback"] == "output-first-unconstrained"
    assert decision.traffic_payload["selection_authority"] == "OUTPUT_FALLBACK"
    assert decision.effective_topic is None
    assert not decision.goal_active
