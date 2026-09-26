from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
)
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


class _Wakeful:
    def __init__(self):
        self.scheduled = []

    def emit_prediction_due(self, *, prediction_ref, observed_at):
        return None

    def schedule_chatgpt_resume(
        self,
        now,
        *,
        request_id,
        task="CONTENT_COGNITION",
        relevant_refs=(),
        delay_seconds=60,
    ):
        row = {"request_id": request_id, "task": task}
        if relevant_refs:
            row["relevant_refs"] = tuple(relevant_refs)
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


def test_pending_autonomous_task_preserves_goal_provenance_for_resume(tmp_path):
    wakeful = _Wakeful()
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
        continuous_loop=_PendingLoop(),
    )

    result = runner.run(
        "VERIFY_PREDICTION",
        now=datetime.now(timezone.utc),
        relevant_refs=("autonomous-goal:goal-42", "prediction:abc"),
    )

    assert result["status"] == "WAITING_FOR_CHATGPT"
    assert wakeful.scheduled == [{
        "request_id": "llmreq-verify",
        "task": "VERIFY_PREDICTION",
        "relevant_refs": (
            "autonomous-goal:goal-42",
            "prediction:abc",
        ),
    }]


def test_pending_content_injects_active_owner_goal_into_resume_provenance(
    monkeypatch, tmp_path
):
    wakeful = _Wakeful()
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
    )

    def pending_content(now, *, focus_topic=None, relevant_refs=()):
        raise LivingRuntimeChatGPTPendingError("llmreq-owner-content")

    monkeypatch.setattr(runner, "_content", pending_content)
    monkeypatch.setattr(
        runner,
        "_active_owner_goal_context",
        lambda: {
            "goal_id": "goal-owner",
            "origin": "EXTERNAL_MISSION",
            "description": "Advance owner mission",
        },
    )

    result = runner.run(
        "CONTENT_COGNITION",
        now=datetime.now(timezone.utc),
        relevant_refs=("cycle:v2-owner",),
    )

    assert result["status"] == "WAITING_FOR_CHATGPT"
    assert wakeful.scheduled == [{
        "request_id": "llmreq-owner-content",
        "task": "CONTENT_COGNITION",
        "relevant_refs": ("cycle:v2-owner", "goal-owner"),
    }]


class _GenerationReady:
    generation_ready = True


class _WaitingContentLoop:
    def preflight(self, *, now, cycle_id=None):
        return {
            "cycle": cycle_id or "v2-1790416800",
            "status": "READY",
        }

    async def run_once(self, **kwargs):
        return {
            "status": "WAITING_FOR_CHATGPT",
            "request_id": "llmreq-inner-content",
            "cycle": kwargs.get("cycle_id") or "v2-1790416800",
        }


class _NoCostEconomy:
    def begin_cycle(self, *, now, cycle):
        return {
            "status": "DECIDED",
            "decision": "NO_RESOURCE_ACTION",
            "cycle": cycle,
        }


def test_internal_content_wait_preserves_owner_and_wake_provenance(
    monkeypatch, tmp_path
):
    wakeful = _Wakeful()
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
        continuous_loop=_WaitingContentLoop(),
        resource_economy=_NoCostEconomy(),
        web_worker=None,
        gsc_collector=None,
    )
    monkeypatch.setattr(
        runner,
        "_runtime_tick",
        lambda now: (_GenerationReady(), {"application": {}}),
    )
    monkeypatch.setattr(
        runner,
        "_active_owner_goal_context",
        lambda: {
            "goal_id": "goal-owner",
            "origin": "EXTERNAL_MISSION",
            "description": "Advance owner mission",
            "priority": 0.9,
        },
    )

    result = runner._content(
        datetime.now(timezone.utc),
        focus_topic="owner-test",
        relevant_refs=("mission:test", "goal-owner"),
    )

    assert result["status"] == "WAITING_FOR_CHATGPT"
    assert wakeful.scheduled == [{
        "request_id": "llmreq-inner-content",
        "task": "CONTENT_COGNITION",
        "relevant_refs": ("mission:test", "goal-owner"),
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


class _ResumeMustNotClaimResources:
    def claim(self, *args, **kwargs):
        raise AssertionError("durable resume must not re-run resource admission")


def test_exact_resume_skips_task_runner_resource_claim(monkeypatch, tmp_path):
    @contextmanager
    def fake_resume(request_id):
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
        resource_regulator=_ResumeMustNotClaimResources(),
    )

    result = runner.run(
        "VERIFY_PREDICTION",
        now=datetime.now(timezone.utc),
        relevant_refs=("cognition-request:llmreq-original",),
    )

    assert result["status"] == "OK"


def test_wake_request_ref_uses_latest_cognition_pointer(
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
        relevant_refs=(
            "prediction:test",
            "cognition-request:llmreq-old",
            "cognition-request:llmreq-new",
        ),
    )

    assert result["status"] == "OK"
    assert seen == ["llmreq-new"]


class _DeferredPrediction:
    prediction_id = "prediction:owner-mission"


class _OwnerMissionPredictions:
    def __init__(self):
        self.deferred = []

    def due(self, now):
        return (_DeferredPrediction(),)

    def defer_verification(self, prediction_id, *, deferred_at, delay, reason):
        self.deferred.append({
            "prediction_id": prediction_id,
            "deferred_at": deferred_at,
            "delay": delay,
            "reason": reason,
        })
        return {
            "prediction_id": prediction_id,
            "retry_after": (deferred_at + delay).isoformat(),
            "deferred_reason": reason,
        }


class _ShouldNotVerifyLoop:
    def __init__(self):
        self.predictions = _OwnerMissionPredictions()
        self.called = False

    async def verify_due_predictions(self, *, now):
        self.called = True
        raise AssertionError("owner mission deferral must not call verification LLM")


def test_owner_external_mission_defers_prediction_verification(monkeypatch, tmp_path):
    wakeful = _Wakeful()
    wakeful.sync_calls = []

    def sync_prediction_wakes(now):
        wakeful.sync_calls.append(now)

    wakeful.sync_prediction_wakes = sync_prediction_wakes
    loop = _ShouldNotVerifyLoop()
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
        continuous_loop=loop,
    )
    monkeypatch.setattr(
        runner,
        "_active_owner_goal_context",
        lambda: {
            "goal_id": "goal-owner",
            "origin": "EXTERNAL_MISSION",
            "description": "Run the real owner mission",
        },
    )
    now = datetime.now(timezone.utc)

    result = runner.run("VERIFY_PREDICTION", now=now)

    assert result["status"] == "OWNER_MISSION_DEFERRED"
    assert result["owner_goal_id"] == "goal-owner"
    assert result["deferred_count"] == 1
    assert loop.called is False
    assert loop.predictions.deferred == [{
        "prediction_id": "prediction:owner-mission",
        "deferred_at": now,
        "delay": timedelta(minutes=15),
        "reason": "OWNER_EXTERNAL_MISSION_PRIORITY",
    }]
    assert wakeful.sync_calls == [now]
