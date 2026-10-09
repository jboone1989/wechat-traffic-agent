"""Independent contracts for method grounding, practice, strategy, and owner streaming."""
import ast
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from wechat_traffic_agent.content_goal_contracts import EvidenceSelection
from wechat_traffic_agent.editorial_strategy_service import (
    EditorialStrategyService, StrategyDecisionRequest,
)
from wechat_traffic_agent.method_evidence_binding_service import (
    MethodBindingRequest, MethodEvidenceBindingService,
)
from wechat_traffic_agent.operator_private_chat_handlers import OperatorPrivateChatHandlers
from wechat_traffic_agent.writing_practice_service import (
    WritingPracticeService, WritingPracticeRequest,
)

ROOT = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"


def _selection():
    return EvidenceSelection(
        title="Why memory alters an AI agent decision", evidence_id="e1",
        topic="agent-memory", related_ids=(),
        reader_question="What comparison shows whether memory changes behavior?",
        gap="No paired memory-ablation trial has been run.",
    )


def _binder():
    return MethodEvidenceBindingService(
        activity=lambda *args, **kw: None,
        structured=lambda *args, **kw: None,
        research_supports=lambda learning, row: False,
        title_key=lambda text: text.casefold(),
        repair_related=lambda selection, site: selection,
        website_view=lambda site: {},
    )


def _binding_request(tmp_path, *, observations, world):
    return MethodBindingRequest(
        folder=tmp_path, cycle="cycle-1", selection=_selection(),
        selected={"evidence_id": "e1", "title": "Unrelated article"},
        related=[], cognition=SimpleNamespace(
            expression_kind="METHOD_NOTE",
            model_dump=lambda **kw: {"expression_kind": "METHOD_NOTE"},
        ),
        learning=None, mind_plan={"world_observations": observations},
        world_payload={"items": world}, snapshot={"articles": []},
        recent_titles=[],
    )


def test_method_note_without_matching_source_is_not_published(tmp_path):
    result = asyncio.run(_binder().bind(
        _binding_request(tmp_path, observations=[], world=[]), object(),
    ))
    assert result.early_result["status"] == "HELD_BY_EXPRESSION_EVIDENCE"
    assert result.authority is None


def test_method_note_can_use_restart_stable_observed_method(tmp_path):
    method = {"evidence_id": "method-1", "learning_kind": "METHOD", "title": "Tool paper"}
    chosen = _selection().model_copy(update={
        "evidence_id": "method-1", "topic": "agent-memory",
    })
    (tmp_path / "expression_binding.json").write_text(json.dumps({
        "selection": chosen.model_dump(mode="json"),
    }))
    req = _binding_request(
        tmp_path,
        observations=[{"evidence_id": "method-1", "topic": "agent-memory",
                       "learning_kind": "METHOD"}],
        world=[method],
    )
    result = asyncio.run(_binder().bind(req, object()))
    assert result.early_result is None
    assert result.authority == "COGNITION_EVIDENCE_REBIND"
    assert result.selected is method
    assert result.selection.evidence_id == "method-1"


def test_writing_practice_runs_without_site_publisher(tmp_path):
    messages = []
    lesson = SimpleNamespace(
        model_dump=lambda **kwargs: {"techniques": [{"name": "precise example"}]},
    )
    async def structured(runtime, prompt, contract):
        messages.append(prompt)
        return lesson
    practice = WritingPracticeService(
        activity=lambda *args, **kw: None, structured=structured,
        lesson_contract=object(), compact_experience=lambda items: [],
        resolve_requirements=lambda lesson, exp: {
            "required_indices": [0], "deferred_indices": [],
        },
        experiment_instruction=lambda exp: None,
        market_instruction=lambda selected: "",
        social_style_loader=lambda path, **kw: {"observations": []},
        digest=lambda item: "hash1",
    )
    result = asyncio.run(practice.prepare(WritingPracticeRequest(
        folder=tmp_path, cycle="cycle-1", curriculum_path=tmp_path / "curriculum.json",
        inputs={"curriculum": [], "prior_experience": []},
        expression_kind="METHOD_NOTE", selected={"evidence_id": "method-1"},
        learning={"progressed": True}, experiment=None, experiment_payload=None,
    ), object()))
    assert result.is_short_note and result.minimum_words is None
    assert result.required_indices == {0}
    assert messages and "LEARN_WRITING" in messages[0]
    assert json.loads((tmp_path / "lesson.json").read_text())["lesson_id"] == "hash1"


def test_strategy_receipts_are_bound_before_admission(monkeypatch, tmp_path):
    import wechat_traffic_agent.editorial_strategy_service as mod
    calls = []
    class Strategy:
        strategy_id = "strategy-1"
        selection_mode = "FORECAST"
        profile = SimpleNamespace(name="research-note", structure_pattern="observation first")
        def model_dump(self, **kwargs):
            return {"strategy_id": self.strategy_id}
    class Prediction:
        def model_dump(self, **kwargs):
            return {"prediction_id": "forecast-1"}
    monkeypatch.setattr(mod, "forecast_strategy_candidates",
                        lambda **kw: calls.append("forecast") or {"candidates": []})
    monkeypatch.setattr(mod, "bind_content_strategy",
                        lambda *args, **kw: calls.append("strategy") or Strategy())
    monkeypatch.setattr(mod, "bind_action_prediction",
                        lambda *args, **kw: calls.append("prediction") or Prediction())
    memory = SimpleNamespace(select_process_variant=lambda **kw: None)
    service = EditorialStrategyService(
        state_root=tmp_path, account="https://example.org", mind=None,
        memory_life=memory, evolution_bridge=None,
        activity=lambda *args, **kw: None,
    )
    outcome = service.decide(StrategyDecisionRequest(
        folder=tmp_path, cycle="cycle-1", now=datetime.now(timezone.utc),
        selection=_selection(), traffic_context=SimpleNamespace(topics=[]),
        market_pulse=False, experiment=None,
    ))
    assert calls == ["forecast", "strategy", "prediction"]
    assert outcome.strategy_payload["strategy_id"] == "strategy-1"
    assert outcome.retry_verification_once is False
    assert (tmp_path / "memory_process_variant.json").exists()


def test_private_chat_native_protocol_is_independent_of_starlette_app_factory(tmp_path):
    class Conversation:
        def append(self, role, message):
            return {"message_id": "owner-1", "role": role, "text": message}
    backend = SimpleNamespace(provider=SimpleNamespace())
    handlers = OperatorPrivateChatHandlers(
        state_root=tmp_path, application=None, backend=backend, mind=None,
        wakeful=None, started_at=datetime.now(timezone.utc), poll_seconds=5,
        account_manager=None, conversation=Conversation(), memory=None,
        memory_runtime=None, authorized=lambda req: True,
        write_allowed=lambda req: True, snapshot=lambda *args, **kwargs: {},
    )
    handlers.prepare_operator_private_turn = lambda: {"prompt": "owner context"}
    async def reply():
        return {"reply": "Ferro reply", "message": {"message_id": "ferro-1"}}
    handlers.run_operator_private_turn = reply
    client = TestClient(Starlette(routes=[
        Route("/chat", handlers.operator_chat_stream, methods=["POST"]),
    ]))
    result = client.post("/chat", json={"message": "Hello"})
    assert result.status_code == 200
    payload = [json.loads(line) for line in result.text.splitlines() if line]
    assert [row["type"] for row in payload if row["type"] in {"ack", "done"}] == ["ack", "done"]
    assert payload[-1]["transport"] == "compat"


def test_new_modules_enforce_no_oversized_methods_and_no_app_import():
    for name in (
        "operator_private_chat_handlers.py", "web_api/app_factory.py",
        "web_api/operator_pages.py", "web_api/public_status.py",
        "method_evidence_binding_service.py", "writing_practice_service.py",
        "editorial_strategy_service.py", "counter_evidence_service.py",
    ):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "live_agent" not in str(node.module), name
                assert "content_goal_loop" not in str(node.module), name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    name, node.name, node.end_lineno - node.lineno + 1,
                )
    live = ast.parse((ROOT / "live_agent.py").read_text())
    klass = next(x for x in live.body if isinstance(x, ast.ClassDef)
                 and x.name == "LiveAgentService")
    app = next(x for x in klass.body if isinstance(x, ast.FunctionDef) and x.name == "_app")
    assert app.end_lineno - app.lineno + 1 <= 50


def test_counter_evidence_missing_downgrades_scope_and_reuses_receipt(monkeypatch, tmp_path):
    import wechat_traffic_agent.counter_evidence_service as mod
    calls = []
    monkeypatch.setattr(mod, "planned_risk_cues", lambda **kw: ("performance",))
    class Searcher:
        async def search_counter_evidence(self, **kw):
            calls.append("search")
            return {"items": [], "failures": [], "query": "counter sources"}
        async def filter_counter_evidence(self, **kw):
            calls.append("filter")
            return {"items": [], "matches": [], "candidate_count": 0}
    from wechat_traffic_agent.counter_evidence_service import CounterEvidenceRequest, CounterEvidenceService
    service = CounterEvidenceService(
        explorer=Searcher(), activity=lambda *args, **kw: None,
    )
    request = CounterEvidenceRequest(
        folder=tmp_path, cycle="cycle-1", now=datetime.now(timezone.utc),
        selection=_selection(), selected={"evidence_id": "e1"},
    )
    outcome = asyncio.run(service.prepare(request, object()))
    assert outcome.plan.status == "NOT_REQUIRED"
    assert outcome.scope_downgraded is True
    assert "do not claim" in outcome.writing_requirement
    assert json.loads((tmp_path / "evidence_floor_attempt.json").read_text())["status"] == "MISSING"
    again = asyncio.run(service.prepare(request, object()))
    assert again.plan == outcome.plan
    assert calls == ["search", "filter"]
