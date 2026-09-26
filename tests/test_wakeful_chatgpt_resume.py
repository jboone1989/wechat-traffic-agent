from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from wechat_traffic_agent.livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
)
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


class _Wakeful:
    def __init__(self):
        self.scheduled = []

    def emit_prediction_due(self, *, prediction_ref, observed_at):
        return None

    def schedule_chatgpt_resume(self, now, *, request_id, task="CONTENT_COGNITION", delay_seconds=60):
        row = {"request_id": request_id, "task": task}
        self.scheduled.append(row)
        return {"status": "SCHEDULED", **row}


class _Predictions:
    def due(self, now):
        return []


class _PendingLoop:
    predictions = _Predictions()

    async def verify_due_predictions(self, *, now):
        raise LivingRuntimeChatGPTPendingError("llmreq-verify")


class _CompletedLoop:
    predictions = _Predictions()

    async def verify_due_predictions(self, *, now):
        return {"verified": 1}


def test_verify_prediction_pending_suspends_instead_of_killing_process(tmp_path):
    wakeful = _Wakeful()
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
        continuous_loop=_PendingLoop(),
    )

    result = runner.run(
        "VERIFY_PREDICTION",
        now=datetime.now(timezone.utc),
    )

    assert result["status"] == "WAITING_FOR_CHATGPT"
    assert result["request_id"] == "llmreq-verify"
    assert result["resumable"] is True
    assert wakeful.scheduled == [{
        "request_id": "llmreq-verify",
        "task": "VERIFY_PREDICTION",
    }]


def test_wake_request_ref_resumes_exact_request_for_next_cognition_only(
    monkeypatch, tmp_path
):
    seen = []

    @contextmanager
    def fake_resume(request_id):
        seen.append(request_id)
        yield

    import wechat_traffic_agent.livingruntime_chatgpt as chatgpt_module

    monkeypatch.setattr(
        chatgpt_module,
        "resume_next_durable_cognition",
        fake_resume,
    )
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=_Wakeful(),
        continuous_loop=_CompletedLoop(),
    )

    result = runner.run(
        "VERIFY_PREDICTION",
        now=datetime.now(timezone.utc),
        relevant_refs=("cognition-request:llmreq-original",),
    )

    assert result["status"] == "OK"
    assert result["prediction_verification"] == {"verified": 1}
    assert seen == ["llmreq-original"]
