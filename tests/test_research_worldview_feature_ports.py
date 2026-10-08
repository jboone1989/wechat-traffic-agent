"""Worldview decisions and evidence selection run without Ferro orchestration."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.content_goal_contracts import EvidenceSelection, WorldviewDecision
from wechat_traffic_agent.evidence_choice_service import (
    EvidenceChoicePolicies, EvidenceChoiceRequest, EvidenceChoiceService,
)
from wechat_traffic_agent.worldview_assessment_service import (
    WorldviewAssessmentService, WorldviewRequest,
)
from wechat_traffic_agent.prospective_predictions import PredictionProposal


def test_bad_model_selection_uses_only_supplied_evidence(tmp_path):
    async def bad_selection(*args, **kwargs):
        raise ValueError("backend did not return a JSON object")

    selection = EvidenceSelection(
        title="Evidence-grounded comparison", evidence_id="actual-1",
        topic="agent-memory", related_ids=(),
        reader_question="What observation establishes that memory changes the outcome?",
        gap="The controlled comparison is not yet available.",
    )
    events = []
    service = EvidenceChoiceService(
        runtime=object(), explorer=object(), inbox=object(),
        activity=lambda *args, **kwargs: events.append(args),
        policies=EvidenceChoicePolicies(
            structured=bad_selection,
            decision_context=lambda **kw: {},
            fallback=lambda *args, **kw: selection,
            repair_related=lambda choice, site: choice,
            market_companion=lambda selected, rows: None,
            market_instruction=lambda selected: "",
            candidate_available=lambda inbox, row: True,
            title_key=lambda value: value.casefold(),
            scope_instruction="AI and finance",
        ),
    )
    source = {"evidence_id": "actual-1", "title": "Observational evidence",
              "summary": "A real source", "source_kind": "web-search"}
    request = EvidenceChoiceRequest(
        folder=tmp_path, cycle="cycle-1", now=datetime.now(timezone.utc),
        snapshot={"articles": [], "topics": {}}, evidence=[source],
        evidence_pool={"items": [source]}, previous_titles=[],
        traffic_payload={}, mind_plan={}, x_mission_context=None,
        learning_focus=None, required_topic=None, effective_topic=None,
        goal_active=False, explorer_runtime=None, topic_context=[],
    )
    result = asyncio.run(service.choose(request, receipt_runtime=object()))
    assert result.selected is source
    assert result.selection_mode == "DETERMINISTIC_EVIDENCE_FALLBACK"
    assert result.market_companion is None
    assert (tmp_path / "selection_fallback.json").is_file()
    assert any(row[1] == "DECIDE" for row in events)


def _worldview_fixture(tmp_path, action, status):
    calls = []
    decision = WorldviewDecision(
        action=action, proposition="A durable fact that can be tested in another cycle.",
        stance=0.4, reliability=0.75, support=0.65,
        rationale="The available observation warrants a bounded conclusion.",
    )

    async def structured(runtime, prompt, contract):
        calls.append("model")
        assert contract is WorldviewDecision
        return decision

    async def propose(**kwargs):
        calls.append("prediction")
        return PredictionProposal(
            action="NONE", rationale="The claim has no dated falsifiable test yet.",
        )

    def record(**kwargs):
        calls.append(("record", kwargs["commit"]))
        return {"status": status, "proposition": kwargs["proposition"],
                "stance": kwargs["stance"], "confidence": 0.7}

    mind = SimpleNamespace(public_projection=lambda: {"beliefs": []})
    service = WorldviewAssessmentService(
        mind=mind, predictions=object(), activity=lambda *args, **kwargs: None,
        structured=structured, decision_context=lambda **kw: {"facts": "source"},
        propose_prediction=propose,
    )
    request = WorldviewRequest(
        folder=tmp_path, cycle="cycle-2", now=datetime.now(timezone.utc),
        selection=SimpleNamespace(topic="finance", reader_question="What changed?"),
        selected={"evidence_id": "real-source-1"}, mind_plan={}, learning=None,
    )
    return service, request, record, calls


def test_worldview_defer_updates_memory_without_prediction(tmp_path):
    service, request, record, calls = _worldview_fixture(tmp_path, "DEFER", "UNCHANGED")
    result = asyncio.run(service.assess(request, object(), record))
    assert result.decision.action == "DEFER"
    assert result.update["status"] == "UNCHANGED"
    assert result.prediction is None
    assert calls == ["model", ("record", False)]
    assert (tmp_path / "worldview_decision.json").exists()
    assert (tmp_path / "worldview_update.json").exists()


def test_worldview_formed_records_prediction_proposal_once_and_resumes(tmp_path):
    service, request, record, calls = _worldview_fixture(tmp_path, "COMMIT", "FORMED")
    first = asyncio.run(service.assess(request, object(), record))
    assert first.update["status"] == "FORMED"
    assert first.prediction is None
    assert calls == ["model", ("record", True), "prediction"]
    second = asyncio.run(service.assess(request, object(), record))
    assert second.update == first.update
    assert second.prediction is None
    assert calls == ["model", ("record", True), "prediction"]
    assert (tmp_path / "prediction_proposal.json").exists()


def test_new_feature_modules_have_small_methods_and_no_monolith_imports():
    import ast
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for name in (
        "content_goal_contracts.py", "evidence_choice_service.py",
        "learning_inquiry_service.py", "worldview_assessment_service.py",
        "web_api/public_chat.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "live_agent" not in str(node.module), name
                assert "content_goal_loop" not in str(node.module), name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    name, node.name, node.end_lineno - node.lineno + 1,
                )


def test_remaining_legacy_entrypoints_continue_to_shrink():
    import ast
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for filename, parent_name, method_name, ceiling in (
        ("content_goal_loop.py", "ContinuousContentLoop", "_run", 1200),
        ("live_agent.py", "LiveAgentService", "_app", 1200),
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        parent = next(n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == parent_name)
        method = next(n for n in parent.body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.name == method_name)
        assert method.end_lineno - method.lineno + 1 < ceiling
