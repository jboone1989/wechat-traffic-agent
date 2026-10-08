"""Contract tests for modular public chat and durable learning inquiry."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from wechat_traffic_agent.web_api.public_chat import PublicChatHandlers, PublicChatPorts
from wechat_traffic_agent.learning_inquiry_service import InquiryRequest, LearningInquiryService


def _chat_fixture(tmp_path):
    calls = []

    async def reply(_provider, *, prompt, message):
        calls.append(("reply", prompt, message))
        return "Evidence-backed answer."

    def record(**kwargs):
        calls.append(("record", kwargs["message"]))
        return {"event_id": "interaction-1"}

    ports = PublicChatPorts(
        classify=lambda message: {"category": "AI", "signal": "QUESTION"},
        snapshot=lambda *args, **kwargs: {
            "as_of": "2026-10-08T12:00:00+00:00",
            "public_reasoning": {"headline": "Current research", "evidence": "Source"},
            "activity": {"current_focus": "Researching memory"},
        },
        degraded=lambda state, exc: {
            "as_of": "2026-10-08T12:00:00+00:00",
        },
        project=lambda snapshot: {"focus": "Researching memory"},
        behavioral=lambda snapshot, relation: {"sociality": "neutral"},
        metadata=lambda *args, **kwargs: {"scope": "public"},
        realization=lambda snapshot: "",
        bounded=lambda obj: obj,
        generate=reply,
        chunks=lambda answer: ["Evidence-", "backed answer."],
        record=record,
    )
    handlers = PublicChatHandlers(
        state_root=tmp_path, application=None, mind=None,
        wakeful=None, started_at=datetime.now(timezone.utc),
        poll_seconds=60, backend=SimpleNamespace(provider=object()),
        limiter=SimpleNamespace(allow=lambda key, now: True),
        ports=ports, state_timeout=1, reply_timeout=2, max_chars=1000,
    )
    return handlers, calls


def test_modular_public_chat_json_and_sse_share_prompt_and_feedback(tmp_path):
    handlers, calls = _chat_fixture(tmp_path)
    app = Starlette(routes=[Route("/chat", handlers.chat, methods=["POST"])])
    client = TestClient(app)
    body = {"message": "How can I test agent memory?"}
    regular = client.post("/chat", json=body)
    assert regular.status_code == 200
    assert regular.json()["reply"] == "Evidence-backed answer."
    assert regular.json()["interaction"]["event_id"] == "interaction-1"
    streamed = client.post("/chat", json=body, headers={"Accept": "text/event-stream"})
    assert streamed.status_code == 200
    text = streamed.text
    for marker in ("event: status", "event: reasoning", "event: delta", "event: done"):
        assert marker in text
    assert '"private_chain_of_thought_exposed":false' in text
    assert '"snapshot_as_of":"2026-10-08T12:00:00+00:00"' in text
    replies = [row for row in calls if row[0] == "reply"]
    assert len(replies) == 2
    assert replies[0][1] == replies[1][1]
    assert len([row for row in calls if row[0] == "record"]) == 2


def test_public_chat_admission_rejects_invalid_and_no_backend(tmp_path):
    handlers, _ = _chat_fixture(tmp_path)
    client = TestClient(Starlette(routes=[Route("/chat", handlers.chat, methods=["POST"])]))
    assert client.post("/chat", json={"message": ""}).status_code == 400
    assert client.post("/chat", data="{", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/chat", json={"message": "x" * 1001}).status_code == 400
    handlers.backend = None
    assert client.post("/chat", json={"message": "valid"}).status_code == 503


def test_learning_inquiry_is_independent_of_content_cycle_and_keeps_no_progress_private(
    tmp_path, monkeypatch,
):
    import wechat_traffic_agent.learning_inquiry_service as inquiry_module

    async def investigate(**kwargs):
        return {
            "sources": [{"evidence_id": "research-1", "title": "Research evidence"}],
            "evidence_id": "research-1", "progressed": False,
            "next_question": "What comparison would establish causality?",
            "next_query": "memory ablation", "related_proposition": None,
            "rationale": "A controlled comparison has not yet been run.",
            "source_failures": [],
        }

    monkeypatch.setattr(inquiry_module, "investigate_learning", investigate)
    monkeypatch.setattr(inquiry_module, "learning_reward",
                        lambda learning: {"utility": 0.1, "source_evidence_ids": []})
    selection = SimpleNamespace(
        topic="ai-memory", model_copy=lambda update: SimpleNamespace(
            topic="ai-memory", evidence_id=update["evidence_id"],
            reader_question=update["reader_question"],
        ),
    )
    service = LearningInquiryService(
        state_root=tmp_path, explorer=None, mind=None, neural_runtime=None,
        activity=lambda *args, **kwargs: None,
        has_public_trace=lambda learning: False,
    )
    output = asyncio.run(service.execute(InquiryRequest(
        folder=tmp_path, cycle="cycle-1", now=datetime.now(timezone.utc),
        selection=selection, selected={"evidence_id": "original"},
        evidence=[], focus=SimpleNamespace(question="What evidence proves memory helps?"),
        memory={}, mind_plan={"goal": None}, exploration={},
    ), runtime=object()))
    assert output.selected["evidence_id"] == "research-1"
    assert output.selection.reader_question == "What evidence proves memory helps?"
    assert output.early_result["status"] == "COGNITION_ONLY"
    assert json.loads((tmp_path / "interest_study_commitment.json").read_text())["status"] == "NO_NEURAL_OWNER"
    assert json.loads((tmp_path / "interest_learning.json").read_text())["neural"]["status"] == "NO_STUDY_COMMITMENT"
    assert json.loads((tmp_path / "cognition.json").read_text())["worth_saying"] is False
