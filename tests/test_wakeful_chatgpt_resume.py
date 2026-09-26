from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wechat_traffic_agent.livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
    LivingRuntimeChatGPTProviderError,
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


def test_stale_owner_cognition_resume_is_ignored_before_content_pipeline(
    monkeypatch, tmp_path
):
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=_Wakeful(),
    )
    runner.owner_agent_episode = SimpleNamespace(
        classify_request=lambda goal_id, request_id: (
            "STALE"
            if (goal_id, request_id)
            == ("goal-owner", "llmreq-owner-stale")
            else "NOT_OWNER"
        )
    )
    monkeypatch.setattr(
        runner,
        "_active_owner_goal_context",
        lambda: {
            "goal_id": "goal-owner",
            "origin": "EXTERNAL_MISSION",
            "description": "Advance owner mission",
        },
    )
    monkeypatch.setattr(
        runner,
        "_content",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale owner response must not enter content pipeline")
        ),
    )

    result = runner.run(
        "CONTENT_COGNITION",
        now=datetime.now(timezone.utc),
        relevant_refs=("cognition-request:llmreq-owner-stale", "goal-owner"),
    )

    assert result == {
        "task": "CONTENT_COGNITION",
        "status": "STALE_OWNER_COGNITION_IGNORED",
        "request_id": "llmreq-owner-stale",
        "owner_goal_id": "goal-owner",
        "resumable": False,
    }


def test_content_cognition_never_uses_task_wide_next_llm_resume(
    monkeypatch, tmp_path
):
    import wechat_traffic_agent.livingruntime_chatgpt as chatgpt_module

    @contextmanager
    def forbidden_resume(_request_id):
        raise AssertionError(
            "CONTENT_COGNITION must route durable requests by semantic owner"
        )
        yield

    monkeypatch.setattr(
        chatgpt_module,
        "resume_next_durable_cognition",
        forbidden_resume,
    )
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=_Wakeful(),
    )
    runner.owner_agent_episode = SimpleNamespace(
        classify_request=lambda _goal_id, _request_id: "NOT_OWNER"
    )
    monkeypatch.setattr(
        runner,
        "_active_owner_goal_context",
        lambda: {
            "goal_id": "goal-owner",
            "origin": "EXTERNAL_MISSION",
            "description": "Advance owner mission",
        },
    )
    monkeypatch.setattr(
        runner,
        "_content",
        lambda *args, **kwargs: {
            "task": "CONTENT_COGNITION",
            "status": "OK",
        },
    )

    result = runner.run(
        "CONTENT_COGNITION",
        now=datetime.now(timezone.utc),
        relevant_refs=("cognition-request:llmreq-content-phase", "goal-owner"),
    )

    assert result["status"] == "OK"


def test_content_provider_timeout_is_task_failure_not_process_failure(
    monkeypatch, tmp_path
):
    wakeful = _Wakeful()
    recoveries = []

    def schedule_provider_recovery(now, *, default_seconds=300):
        recoveries.append((now, default_seconds))
        return {
            "status": "SCHEDULED",
            "retry_seconds": default_seconds,
        }

    wakeful.schedule_provider_recovery = schedule_provider_recovery
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
    )

    def unavailable_content(now, *, focus_topic=None, relevant_refs=()):
        raise LivingRuntimeChatGPTProviderError(
            "LivingRuntime cognition request timed out"
        )

    monkeypatch.setattr(runner, "_content", unavailable_content)
    now = datetime.now(timezone.utc)

    result = runner.run("CONTENT_COGNITION", now=now)

    assert result["status"] == "CHATGPT_UNAVAILABLE"
    assert result["retryable"] is True
    assert result["error_type"] == "LivingRuntimeChatGPTProviderError"
    assert result["provider_recovery"]["status"] == "SCHEDULED"
    assert recoveries == [(now, 300)]


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



def test_owner_context_falls_back_to_authoritative_goal_ledger(tmp_path):
    proposals = (
        tmp_path
        / "application"
        / "operator_chat"
        / "goal_proposals"
    )
    proposals.mkdir(parents=True)
    (proposals / "owner.json").write_text(
        '{"status":"ACTIVATED","kind":"EXTERNAL_MISSION","goal_id":"goal-owner"}',
        encoding="utf-8",
    )
    goal = SimpleNamespace(
        goal_id="goal-owner",
        origin=SimpleNamespace(value="USER"),
        status=SimpleNamespace(value="ACTIVE"),
        description="Execute the owner external mission",
        reason="operator-issued external mission",
        priority=0.9,
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    internal_goal = SimpleNamespace(
        goal_id="goal-internal",
        origin=SimpleNamespace(value="INTERNAL"),
        status=SimpleNamespace(value="ACTIVE"),
        description="Internal maintenance",
        reason="internal",
        priority=0.5,
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )
    domain_runtime = SimpleNamespace(
        active_goals=[internal_goal],
        goals=[internal_goal, goal],
    )
    runner = WakefulContentTaskRunner(
        app=SimpleNamespace(domain_runtime=domain_runtime),
        state_root=tmp_path,
        wakeful=_Wakeful(),
        continuous_loop=_CompletedLoop(),
    )

    context = runner._active_owner_goal_context()

    assert context is not None
    assert context["goal_id"] == "goal-owner"
    assert context["origin"] == "EXTERNAL_MISSION"



def test_owner_context_uses_active_operator_external_mission_without_proposal(tmp_path):
    goal = SimpleNamespace(
        goal_id="goal-owner-runtime",
        origin=SimpleNamespace(value="USER"),
        status=SimpleNamespace(value="ACTIVE"),
        description="Execute the runtime owner mission",
        reason="operator-issued external mission",
        priority=0.9,
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    mission = SimpleNamespace(
        goal_id="goal-owner-runtime",
        status=SimpleNamespace(value="ACTIVE"),
        issued_by="operator",
    )
    domain_runtime = SimpleNamespace(
        active_goals=[goal],
        goals=[goal],
        external_missions=[mission],
    )
    runner = WakefulContentTaskRunner(
        app=SimpleNamespace(domain_runtime=domain_runtime),
        state_root=tmp_path,
        wakeful=_Wakeful(),
        continuous_loop=_CompletedLoop(),
    )

    context = runner._active_owner_goal_context()

    assert context is not None
    assert context["goal_id"] == "goal-owner-runtime"
    assert context["origin"] == "EXTERNAL_MISSION"



class _OwnerMissionNoDuePredictions(_OwnerMissionPredictions):
    def due(self, now):
        return ()


def test_owner_external_mission_defers_prediction_task_even_without_due_rows(
    monkeypatch, tmp_path
):
    wakeful = _Wakeful()
    loop = _ShouldNotVerifyLoop()
    loop.predictions = _OwnerMissionNoDuePredictions()
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

    result = runner.run(
        "VERIFY_PREDICTION",
        now=datetime.now(timezone.utc),
    )

    assert result["status"] == "OWNER_MISSION_DEFERRED"
    assert result["owner_goal_id"] == "goal-owner"
    assert result["deferred_count"] == 0
    assert result["retry_after"] is None
    assert loop.called is False


def test_legacy_content_resume_is_consumed_before_new_owner_episode(
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
    monkeypatch.setattr(
        runner,
        "_pending_chatgpt_cycle",
        lambda: {"cycle": "v2-legacy", "request_id": "llmreq-legacy"},
    )
    monkeypatch.setattr(
        runner,
        "_owner_agent_control",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy content resume must run before a new owner episode")
        ),
    )

    result = runner._content(
        datetime.now(timezone.utc),
        focus_topic=None,
        relevant_refs=("cognition-request:llmreq-legacy", "goal-owner"),
    )

    assert result["content_chatgpt_resume"]["request_id"] == "llmreq-legacy"
    assert "owner_agent_episode" not in result
    assert result["status"] == "WAITING_FOR_CHATGPT"


def test_provider_timeout_releases_matching_owner_single_flight(
    monkeypatch, tmp_path
):
    wakeful = _Wakeful()
    recoveries = []
    cleared = []

    def schedule_provider_recovery(now, *, default_seconds=300):
        recoveries.append((now, default_seconds))
        return {"status": "SCHEDULED", "retry_seconds": default_seconds}

    wakeful.schedule_provider_recovery = schedule_provider_recovery
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        wakeful=wakeful,
    )
    runner.owner_agent_episode = SimpleNamespace(
        clear_pending_request=lambda request_id, **kwargs: (
            cleared.append((request_id, kwargs.get("error_type"))) or True
        )
    )

    def unavailable_content(now, *, focus_topic=None, relevant_refs=()):
        raise LivingRuntimeChatGPTProviderError(
            "LivingRuntime cognition request timed out"
        )

    monkeypatch.setattr(runner, "_content", unavailable_content)
    now = datetime.now(timezone.utc)

    result = runner.run(
        "CONTENT_COGNITION",
        now=now,
        relevant_refs=("cognition-request:llmreq-owner-timeout",),
    )

    assert result["status"] == "CHATGPT_UNAVAILABLE"
    assert result["owner_episode_pending_cleared"] is True
    assert result["provider_recovery"]["status"] == "SCHEDULED"
    assert cleared == [
        ("llmreq-owner-timeout", "LivingRuntimeChatGPTProviderError")
    ]
    assert recoveries == [(now, 300)]
