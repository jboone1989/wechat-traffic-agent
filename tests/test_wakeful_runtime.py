from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time

from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime, _connectome_body_currents
from agent_runtime.wakeful import PlannedWake


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)



def test_connectome_body_currents_are_low_semantic_and_bounded():
    currents = _connectome_body_currents(
        {
            "body": {
                "cpu_pressure": 0.75,
                "memory_pressure": 0.25,
                "disk_pressure": 1.2,
                "x_follower_gap": 0.4,
                "x_recent_decline": 0.1,
                "circadian_sin": -0.6,
            }
        },
        recent_stimulus_kinds=("NETWORK_ERROR", "NETWORK_ERROR"),
    )
    assert currents["sense_cpu_pressure"] == 0.75
    assert currents["sense_memory_pressure"] == 0.25
    assert currents["sense_disk_pressure"] == 1.0
    assert currents["sense_x_follower_gap"] == 0.4
    assert currents["sense_circadian_day"] == 0.2
    assert currents["sense_circadian_night"] == 0.8
    assert currents["sense_network_error"] == 0.5


def test_neural_life_stimulates_aux_body_electrodes_without_feedback_authority(tmp_path):
    class _AuxProbe:
        aux_channels = (
            "sense_cpu_pressure", "sense_memory_pressure", "sense_disk_pressure",
            "sense_x_follower_gap", "sense_x_recent_decline",
            "sense_circadian_day", "sense_circadian_night", "sense_network_error",
        )

        def __init__(self):
            self.calls = []

        def stimulate(self, currents, observed_at, *, source_event_id=None, feedback_eligible=None):
            self.calls.append({
                "currents": dict(currents),
                "source_event_id": source_event_id,
                "feedback_eligible": feedback_eligible,
            })
            return {"delta_readout_hz": {key: 0.0 for key in self.aux_channels}}

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    probe = _AuxProbe()
    runtime.core.reflex.substrate = probe
    runtime.refresh_neural_life(NOW)

    assert len(probe.calls) == 1
    call = probe.calls[0]
    assert call["source_event_id"].startswith("body-sense:")
    assert call["feedback_eligible"] is False
    assert set(call["currents"]) <= set(probe.aux_channels)
    assert call["currents"]



def test_initial_plans_are_explicit_and_restart_safe(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path, content_interval_hours=6)
    runtime.ensure_initial_plans(NOW)
    tasks = {wake.task for wake in runtime.core.snapshot().pending_plans}
    assert {"OBSERVE_WORLD", "RESOURCE_REVIEW", "CHECK_OUTCOMES", "CONTENT_COGNITION", "REFLECT"} <= tasks
    restored = ContentWakefulRuntime(state_root=tmp_path, content_interval_hours=6)
    before = len(restored.core.snapshot().pending_plans)
    restored.ensure_initial_plans(NOW)
    assert len(restored.core.snapshot().pending_plans) == before


def test_content_cognition_is_scheduled_by_semantic_interval(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path, content_interval_hours=6)
    runtime.schedule_next("CONTENT_COGNITION", NOW)
    snapshot = runtime.core.snapshot()
    wake = next(row for row in snapshot.pending_plans if row.task == "CONTENT_COGNITION")
    assert wake.at == NOW + timedelta(hours=6)


def test_autonomous_goal_requires_positive_world_utility_to_be_satisfied(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:resource-test",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]

    settled = runtime.observe_autonomous_goal_outcome(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": 0.65},
        goal_id=goal_id,
        observed_at=NOW + timedelta(seconds=30),
    )

    assert settled is not None
    assert settled["status"] == "SATISFIED"
    assert settled["world_utility"] == 0.65
    feedback = settled["drive_feedback"]
    assert feedback["last_satisfied_at"] == (
        NOW + timedelta(seconds=30)
    ).isoformat()
    assert feedback["outcome_satisfaction"] >= 0.65


def test_autonomous_goal_ok_without_world_effect_is_only_executed(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:no-effect",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]

    settled = runtime.observe_autonomous_goal_outcome(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": 0.0},
        goal_id=goal_id,
        observed_at=NOW + timedelta(seconds=30),
    )

    assert settled is not None
    assert settled["status"] == "EXECUTED"
    assert settled["drive_feedback"]["no_effect_streak"] == 1
    assert settled["drive_feedback"].get("last_satisfied_at") is None


def test_matching_regular_task_settles_unique_active_autonomous_goal(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:regular-resource",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]

    settled = runtime.observe_autonomous_goal_outcome(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": 0.4},
        goal_id=None,
        observed_at=NOW + timedelta(seconds=30),
    )

    assert settled is not None
    assert settled["goal_id"] == goal_id
    assert settled["status"] == "SATISFIED"
    assert settled["drive_feedback"]["last_outcome_task"] == "RESOURCE_REVIEW"


def test_existing_autonomous_goal_recovers_missing_wake_during_cooldown(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:orphan-resource",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]
    assert not any(
        wake.wake_id == f"autonomous:{goal_id}"
        for wake in runtime.core.snapshot().pending_plans
    )

    restored = runtime._ensure_autonomous_goal_wakes(
        NOW + timedelta(minutes=1)
    )

    assert restored == 1
    wake = next(
        wake
        for wake in runtime.core.snapshot().pending_plans
        if wake.wake_id == f"autonomous:{goal_id}"
    )
    assert wake.task == "RESOURCE_REVIEW"
    assert wake.relevant_refs == (goal_id,)


def test_durable_resume_replaces_autonomous_goal_wake_instead_of_duplicating_it(
    tmp_path,
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:suspended-resource",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]
    runtime._ensure_autonomous_goal_wakes(NOW)
    assert any(
        wake.wake_id == f"autonomous:{goal_id}"
        for wake in runtime.core.snapshot().pending_plans
    )

    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_suspended_resource",
        task="RESOURCE_REVIEW",
        relevant_refs=(goal_id,),
    )
    restored = runtime._ensure_autonomous_goal_wakes(
        NOW + timedelta(seconds=1)
    )

    assert restored == 0
    pending = tuple(runtime.core.snapshot().pending_plans)
    assert not any(
        wake.wake_id == f"autonomous:{goal_id}"
        for wake in pending
    )
    resume = next(
        wake for wake in pending
        if wake.wake_id == "content:RESOURCE_REVIEW"
    )
    assert resume.relevant_refs == (
        goal_id,
        "cognition-request:llmreq_suspended_resource",
    )


def test_regular_task_settlement_cancels_recovered_autonomous_wake(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    scheduled = runtime.autonomous_life.tick(
        now=NOW,
        organism={"updated_at": NOW.isoformat()},
        drives={
            "last_decision": {
                "decision_id": "drive-decision:cancel-stale-wake",
                "winner": {
                    "drive": "resource",
                    "task": "RESOURCE_REVIEW",
                    "reason": "reduce resource pressure",
                    "arbitration_score": 0.8,
                },
            }
        },
        developmental_history={"open_developmental_needs": []},
    )
    goal_id = scheduled["goal"]["goal_id"]
    runtime._ensure_autonomous_goal_wakes(NOW)

    settled = runtime.observe_autonomous_goal_outcome(
        task="RESOURCE_REVIEW",
        result={"status": "OK", "world_utility": 0.4},
        goal_id=None,
        observed_at=NOW + timedelta(seconds=30),
    )

    assert settled is not None
    assert settled["goal_id"] == goal_id
    assert not any(
        wake.wake_id == f"autonomous:{goal_id}"
        for wake in runtime.core.snapshot().pending_plans
    )


def test_chatgpt_only_cognition_ignores_legacy_api_pool_pressure(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for name, pressure in (("survival_resources", "WATCH"), ("llm_resource_pool", "CRITICAL")):
        path = tmp_path / "application" / name / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "observed_at": now.isoformat(),
            "pressure": pressure,
            "paid_available_funds": 0.0,
        }), encoding="utf-8")
    pool = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    pool.write_text(json.dumps({"observed_at": now.isoformat(), "resources": []}), encoding="utf-8")
    state = tmp_path / "application" / "domain_runtime" / "agent_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "data": {
            "goals": [{
                "data": {
                    "goal_id": "goal_owner",
                    "origin": {"value": "USER"},
                    "status": {"value": "ACTIVE"},
                }
            }]
        }
    }), encoding="utf-8")

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_goal_followup(
        now - timedelta(seconds=10),
        seconds=5,
        relevant_refs=("goal_owner",),
    )

    wake = runtime.core.pop_ready()

    assert wake is not None
    assert wake.task == "CONTENT_COGNITION"
    assert wake.relevant_refs == ("goal_owner",)

    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=now - timedelta(seconds=1),
        reason="ordinary scheduled cognition",
        task="CONTENT_COGNITION",
        relevant_refs=(),
        preemptible=True,
    ))
    ordinary = runtime.core.pop_ready()
    assert ordinary is not None
    assert ordinary.task == "CONTENT_COGNITION"
    assert ordinary.relevant_refs == ()


def test_persisted_owner_goal_reason_is_compatibly_admitted_under_pressure(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for name, pressure in (("survival_resources", "WATCH"), ("llm_resource_pool", "CRITICAL")):
        path = tmp_path / "application" / name / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"observed_at": now.isoformat(), "pressure": pressure}), encoding="utf-8")
    pool = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    pool.write_text(json.dumps({"observed_at": now.isoformat(), "resources": []}), encoding="utf-8")
    state = tmp_path / "application" / "domain_runtime" / "agent_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "data": {
            "goals": [{
                "data": {
                    "goal_id": "goal_owner",
                    "origin": {"value": "USER"},
                    "status": {"value": "ACTIVE"},
                }
            }]
        }
    }), encoding="utf-8")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=now - timedelta(seconds=1),
        reason="reconsider priorities after owner activated a goal",
        task="CONTENT_COGNITION",
        relevant_refs=(),
        preemptible=True,
    ))

    wake = runtime.core.pop_ready()

    assert wake is not None
    assert wake.task == "CONTENT_COGNITION"


def test_owner_followup_upgrades_earlier_ordinary_cognition_wake(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for name, pressure in (("survival_resources", "WATCH"), ("llm_resource_pool", "CRITICAL")):
        path = tmp_path / "application" / name / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "observed_at": now.isoformat(),
            "pressure": pressure,
            "paid_available_funds": 0.0,
        }), encoding="utf-8")
    pool = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    pool.write_text(json.dumps({"observed_at": now.isoformat(), "resources": []}), encoding="utf-8")
    state = tmp_path / "application" / "domain_runtime" / "agent_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "data": {"goals": [{"data": {
            "goal_id": "goal_owner",
            "origin": {"value": "USER"},
            "status": {"value": "ACTIVE"},
        }}]}
    }), encoding="utf-8")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    ordinary_at = now - timedelta(seconds=1)
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=ordinary_at,
        reason="run the next due content cognition cycle",
        task="CONTENT_COGNITION",
    ))

    runtime.schedule_goal_followup(
        now,
        seconds=5,
        relevant_refs=("goal_owner",),
    )

    pending = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:CONTENT_COGNITION"
    )
    assert pending.at == ordinary_at
    assert pending.reason == "reconsider priorities after owner activated a goal"
    assert pending.relevant_refs == ("goal_owner",)
    ready = runtime.core.pop_ready()
    assert ready is not None
    assert ready.relevant_refs == ("goal_owner",)


def test_external_owner_wake_upgrades_earlier_ordinary_cognition_wake(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for name, pressure in (("survival_resources", "WATCH"), ("llm_resource_pool", "CRITICAL")):
        path = tmp_path / "application" / name / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "observed_at": now.isoformat(),
            "pressure": pressure,
            "paid_available_funds": 0.0,
        }), encoding="utf-8")
    pool = tmp_path / "application" / "llm_resource_pool" / "resources.json"
    pool.write_text(json.dumps({"observed_at": now.isoformat(), "resources": []}), encoding="utf-8")
    state = tmp_path / "application" / "domain_runtime" / "agent_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "data": {"goals": [{"data": {
            "goal_id": "goal_owner",
            "origin": {"value": "USER"},
            "status": {"value": "ACTIVE"},
        }}]}
    }), encoding="utf-8")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    ordinary_at = now - timedelta(seconds=1)
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=ordinary_at,
        reason="run the next due content cognition cycle",
        task="CONTENT_COGNITION",
    ))
    runtime._enqueue_external_wake(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=now + timedelta(seconds=30),
        reason="resume active owner goal",
        task="CONTENT_COGNITION",
        relevant_refs=("goal_owner",),
    ))

    assert runtime._drain_external_wake_requests() == 1

    pending = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:CONTENT_COGNITION"
    )
    assert pending.at == ordinary_at
    assert pending.reason == "resume active owner goal"
    assert pending.relevant_refs == ("goal_owner",)
    ready = runtime.core.pop_ready()
    assert ready is not None
    assert ready.relevant_refs == ("goal_owner",)


def test_talk_stimuli_accumulate_without_storing_prompt_text(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime.emit_talk(message="first private-ish visitor text", observed_at=now)
    assert runtime.public_snapshot()["cognition_threshold_crossed"] is False
    runtime.emit_talk(message="second visitor text", observed_at=now + timedelta(seconds=1))
    runtime.emit_talk(message="third visitor text", observed_at=now + timedelta(seconds=2))
    snapshot = runtime.public_snapshot()
    assert snapshot["cognition_threshold_crossed"] is True
    persisted = (tmp_path / "application" / "wakeful.json").read_text(encoding="utf-8")
    assert "private-ish" not in persisted
    assert "visitor text" not in persisted


def test_quiet_runtime_has_zero_cognition_triggers_for_thirty_minutes(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.ensure_initial_plans(NOW)
    runtime.core.mark_cognition(NOW)
    snapshot = runtime.core.snapshot()
    assert snapshot.cognition_triggers == ()
    assert runtime.public_snapshot()["presence"] == "AWAKE"


def test_open_prediction_becomes_precise_planned_wake(tmp_path):
    from wechat_traffic_agent.prospective_predictions import ProspectivePredictionLedger
    ledger = ProspectivePredictionLedger(tmp_path)
    due = NOW + timedelta(hours=2)
    prediction = ledger.record(
        cycle="v2-1", topic="btc", proposition="BTC liquidity conditions matter", proposition_stance=0.1,
        statement="BTC evidence changes", confidence=0.6,
        created_at=NOW, due_at=due, verification_query="BTC evidence",
        falsifier="No material change", source_evidence_refs=("e:1",),
    )
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.ensure_initial_plans(NOW)
    wake = next(row for row in runtime.core.snapshot().pending_plans
                if row.task == "VERIFY_PREDICTION")
    assert wake.at == due
    assert wake.relevant_refs == (prediction.prediction_id,)
    assert wake.reason == f"verify prediction {prediction.prediction_id}"


def test_pending_chatgpt_prediction_verification_is_single_flight(tmp_path):
    from wechat_traffic_agent.prospective_predictions import ProspectivePredictionLedger

    ledger = ProspectivePredictionLedger(tmp_path)
    prediction = ledger.record(
        cycle="v2-single-flight",
        topic="btc",
        proposition="BTC liquidity conditions matter",
        proposition_stance=0.1,
        statement="BTC evidence changes",
        confidence=0.6,
        created_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(hours=1),
        verification_query="BTC evidence",
        falsifier="No material change",
        source_evidence_refs=("e:1",),
    )
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.ensure_initial_plans(NOW)
    assert any(
        row.wake_id == f"wake:{prediction.prediction_id}"
        for row in runtime.core.snapshot().pending_plans
    )

    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_singleflight",
        task="VERIFY_PREDICTION",
    )
    runtime.sync_prediction_wakes(NOW + timedelta(seconds=1))

    pending = runtime.core.snapshot().pending_plans
    resume = next(
        row for row in pending
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    assert resume.relevant_refs == ("cognition-request:llmreq_singleflight",)
    assert not any(
        row.wake_id.startswith("wake:prediction:")
        for row in pending
    )


def test_pending_chatgpt_request_defers_new_cognition_and_optional_internal_work(
    tmp_path,
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_pending",
        task="VERIFY_PREDICTION",
    )

    assert runtime.pending_chatgpt_request_ids() == ("llmreq_pending",)
    verify = PlannedWake(
        wake_id="wake:prediction:new",
        at=NOW,
        reason="verify another prediction",
        task="VERIFY_PREDICTION",
    )
    play = PlannedWake(
        wake_id="content:NEURAL_PLAY",
        at=NOW,
        reason="optional internal play",
        task="NEURAL_PLAY",
    )
    assert runtime._plan_admission_delay(verify) == 60.0
    assert runtime._plan_admission_delay(play) == 60.0


def test_optional_internal_work_gets_bounded_slot_after_chatgpt_starvation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONTENT_OPTIONAL_INTERNAL_MAX_DEFERRAL_SECONDS", "900")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    monkeypatch.setattr(runtime.core, "clock", lambda: NOW)
    monkeypatch.setattr(runtime.resource_regulator, "defer_seconds", lambda task, now: None)
    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_long_pending",
        task="VERIFY_PREDICTION",
    )
    play = PlannedWake(
        wake_id="content:NEURAL_PLAY",
        at=NOW,
        reason="bounded organism play",
        task="NEURAL_PLAY",
    )

    assert runtime._plan_admission_delay(play) == 60.0

    # Starvation evidence is durable: a process restart must not reset the
    # owner/ChatGPT deferral clock.
    restarted = ContentWakefulRuntime(state_root=tmp_path)
    monkeypatch.setattr(
        restarted.core,
        "clock",
        lambda: NOW + timedelta(seconds=901),
    )
    monkeypatch.setattr(
        restarted.resource_regulator,
        "defer_seconds",
        lambda task, now: None,
    )

    assert restarted._plan_admission_delay(play) is None


def test_owner_continuation_cannot_starve_neural_play_forever(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONTENT_OPTIONAL_INTERNAL_MAX_DEFERRAL_SECONDS", "900")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    clock = {"now": NOW}
    monkeypatch.setattr(runtime.core, "clock", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_active_user_goal_ids", lambda: {"goal-owner"})
    monkeypatch.setattr(runtime.resource_regulator, "defer_seconds", lambda task, now: None)
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=NOW - timedelta(seconds=1),
        reason="resume durable content cognition after ChatGPT response",
        task="CONTENT_COGNITION",
        relevant_refs=("goal-owner", "cognition-request:llmreq_owner_long"),
    ))
    play = PlannedWake(
        wake_id="content:NEURAL_PLAY",
        at=NOW - timedelta(minutes=30),
        reason="organism play",
        task="NEURAL_PLAY",
    )

    assert runtime._plan_admission_delay(play) == 30.0
    clock["now"] = NOW + timedelta(seconds=901)
    assert runtime._plan_admission_delay(play) is None


def test_starvation_cap_does_not_bypass_hard_resource_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_OPTIONAL_INTERNAL_MAX_DEFERRAL_SECONDS", "60")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    clock = {"now": NOW}
    monkeypatch.setattr(runtime.core, "clock", lambda: clock["now"])
    runtime.schedule_chatgpt_resume(
        NOW, request_id="llmreq_resource_block", task="VERIFY_PREDICTION"
    )
    play = PlannedWake(
        wake_id="content:NEURAL_PLAY", at=NOW, reason="play", task="NEURAL_PLAY"
    )
    assert runtime._plan_admission_delay(play) == 60.0
    clock["now"] = NOW + timedelta(seconds=61)
    monkeypatch.setattr(
        runtime.resource_regulator, "defer_seconds", lambda task, now: 900.0
    )
    assert runtime._plan_admission_delay(play) == 900.0


def test_exact_chatgpt_resume_is_not_rethrottled_by_resource_admission(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    resume = PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=NOW,
        reason="resume durable content cognition",
        task="CONTENT_COGNITION",
        relevant_refs=(
            "goal-owner",
            "cognition-request:llmreq_resume",
        ),
    )

    assert runtime._plan_admission_delay(resume) is None


def test_pending_chatgpt_request_inhibits_new_neural_motor_selection(
    monkeypatch, tmp_path
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_pending",
        task="VERIFY_PREDICTION",
    )

    def forbidden_select(*args, **kwargs):
        raise AssertionError("neural motor must not select optional work")

    monkeypatch.setattr(runtime.neural_motor, "select", forbidden_select)
    runtime.refresh_neural_life(NOW)

    assert not any(
        wake.wake_id.startswith("motor:")
        for wake in runtime.core.snapshot().pending_plans
    )


def test_owner_goal_keeps_bounded_neural_learning_but_cancels_motor_control(
    monkeypatch, tmp_path
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    monkeypatch.setattr(
        runtime,
        "_active_user_goal_ids",
        lambda: {"goal-owner"},
    )
    runtime.core.schedule(PlannedWake(
        wake_id="content:NEURAL_PLAY",
        at=NOW,
        reason="bounded sensorimotor learning",
        task="NEURAL_PLAY",
    ))
    runtime.core.schedule(PlannedWake(
        wake_id="motor:decision-1",
        at=NOW,
        reason="autonomous external motor action",
        task="CHECK_X",
        relevant_refs=("motor-decision:decision-1",),
    ))

    runtime.refresh_neural_life(NOW)

    pending = {
        wake.wake_id: wake
        for wake in runtime.core.snapshot().pending_plans
    }
    assert "content:NEURAL_PLAY" in pending
    assert "motor:decision-1" not in pending


def test_owner_goal_cognition_respects_existing_chatgpt_single_flight(
    monkeypatch, tmp_path
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_pending",
        task="VERIFY_PREDICTION",
    )
    monkeypatch.setattr(
        runtime,
        "_active_user_goal_ids",
        lambda: {"goal-owner"},
    )
    owner = PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=NOW,
        reason="reconsider priorities after owner activated a goal",
        task="CONTENT_COGNITION",
        relevant_refs=("goal-owner",),
    )

    assert runtime._plan_admission_delay(owner) == 60.0


def test_due_owner_continuation_preempts_older_nonessential_wake(
    monkeypatch, tmp_path
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    monkeypatch.setattr(runtime.core, "clock", lambda: NOW)
    monkeypatch.setattr(
        runtime,
        "_active_user_goal_ids",
        lambda: {"goal-owner"},
    )
    runtime.core.schedule(PlannedWake(
        wake_id="content:CHECK_X",
        at=NOW - timedelta(minutes=5),
        reason="older social check",
        task="CHECK_X",
    ))
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=NOW - timedelta(seconds=1),
        reason="resume durable content cognition after ChatGPT response",
        task="CONTENT_COGNITION",
        relevant_refs=(
            "goal-owner",
            "cognition-request:llmreq_owner_resume",
        ),
    ))

    ready = runtime.core.pop_ready()

    assert ready is not None
    assert ready.task == "CONTENT_COGNITION"
    assert "goal-owner" in ready.relevant_refs
    deferred = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.task == "CHECK_X"
    )
    assert deferred.at == NOW + timedelta(seconds=30)


def test_due_owner_continuation_does_not_preempt_survival_lane(
    monkeypatch, tmp_path
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    monkeypatch.setattr(runtime.core, "clock", lambda: NOW)
    monkeypatch.setattr(
        runtime,
        "_active_user_goal_ids",
        lambda: {"goal-owner"},
    )
    runtime.core.schedule(PlannedWake(
        wake_id="content:NEURAL_INTEROCEPTION",
        at=NOW - timedelta(minutes=5),
        reason="sense body state",
        task="NEURAL_INTEROCEPTION",
    ))
    runtime.core.schedule(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=NOW - timedelta(seconds=1),
        reason="resume durable content cognition after ChatGPT response",
        task="CONTENT_COGNITION",
        relevant_refs=(
            "goal-owner",
            "cognition-request:llmreq_owner_resume",
        ),
    ))

    ready = runtime.core.pop_ready()

    assert ready is not None
    assert ready.task == "NEURAL_INTEROCEPTION"


def test_chatgpt_resume_replaces_stale_request_ref_without_losing_provenance(
    tmp_path,
):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_old",
        task="VERIFY_PREDICTION",
    )
    existing = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    runtime.core.schedule(type(existing)(
        wake_id=existing.wake_id,
        at=existing.at,
        reason=existing.reason,
        task=existing.task,
        relevant_refs=(
            "prediction:abc",
            "cognition-request:llmreq_old",
        ),
        cancel_conditions=existing.cancel_conditions,
        preemptible=existing.preemptible,
    ))

    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_new",
        task="VERIFY_PREDICTION",
    )

    resumed = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    assert resumed.relevant_refs == (
        "prediction:abc",
        "cognition-request:llmreq_new",
    )


def test_chatgpt_resume_preserves_autonomous_goal_identity(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_resource",
        task="RESOURCE_REVIEW",
        relevant_refs=("autonomous-goal:goal-42", "mission:test"),
    )

    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:RESOURCE_REVIEW"
    )
    assert wake.relevant_refs == (
        "autonomous-goal:goal-42",
        "mission:test",
        "cognition-request:llmreq_resource",
    )



def test_chatgpt_resume_replaces_stale_cognition_pointer(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    runtime.schedule_chatgpt_resume(
        NOW,
        request_id="llmreq_old",
        task="VERIFY_PREDICTION",
    )
    first = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    first_at = first.at

    runtime.schedule_chatgpt_resume(
        NOW + timedelta(seconds=5),
        request_id="llmreq_new",
        task="VERIFY_PREDICTION",
    )

    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    assert wake.at == first_at
    assert wake.relevant_refs == ("cognition-request:llmreq_new",)


def test_external_chatgpt_resume_replaces_stale_cognition_pointer(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    first_at = NOW + timedelta(seconds=30)
    runtime.core.schedule(PlannedWake(
        wake_id="content:VERIFY_PREDICTION",
        at=first_at,
        reason="resume old cognition",
        task="VERIFY_PREDICTION",
        relevant_refs=(
            "prediction:test",
            "cognition-request:llmreq_old",
        ),
    ))
    runtime._enqueue_external_wake(PlannedWake(
        wake_id="content:VERIFY_PREDICTION",
        at=NOW + timedelta(seconds=60),
        reason="resume latest cognition",
        task="VERIFY_PREDICTION",
        relevant_refs=("cognition-request:llmreq_new",),
    ))

    assert runtime._drain_external_wake_requests() == 1

    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:VERIFY_PREDICTION"
    )
    assert wake.at == first_at
    assert wake.relevant_refs == (
        "prediction:test",
        "cognition-request:llmreq_new",
    )


def test_external_wake_spool_uses_write_order_not_filename_for_latest_cognition(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    root = tmp_path / "application" / "wake_requests"
    root.mkdir(parents=True, exist_ok=True)

    def write(path, *, request_id, at):
        path.write_text(json.dumps({
            "schema_version": "content-wake-request.v1",
            "wake_id": "content:CONTENT_COGNITION",
            "at": at.isoformat(),
            "reason": "resume cognition",
            "task": "CONTENT_COGNITION",
            "relevant_refs": [
                "goal-owner",
                f"cognition-request:{request_id}",
            ],
            "cancel_conditions": [],
            "preemptible": True,
        }), encoding="utf-8")

    # Deliberately make the newer request sort *before* the older one by name.
    old_path = root / "z-old.json"
    new_path = root / "a-new.json"
    write(old_path, request_id="llmreq_old", at=NOW + timedelta(seconds=30))
    write(new_path, request_id="llmreq_new", at=NOW + timedelta(seconds=60))
    os.utime(old_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new_path, ns=(2_000_000_000, 2_000_000_000))

    assert runtime._drain_external_wake_requests() == 2
    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:CONTENT_COGNITION"
    )
    assert wake.at == NOW + timedelta(seconds=30)
    assert wake.relevant_refs == (
        "goal-owner",
        "cognition-request:llmreq_new",
    )


def test_full_neural_graph_is_exposed_without_claiming_future_plasticity(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    snapshot = runtime.public_snapshot()
    graph = snapshot["neural_graph"]
    assert set(graph["neurons"]) == {
        "curiosity", "threat_risk", "social_attention", "task_urgency",
        "prediction_due", "resource_pressure", "opportunity", "error_recovery",
    }
    assert graph["synapses"]
    assert graph["capabilities"]["recurrent_graph"] is True
    assert graph["capabilities"]["cpg"] is False
    assert graph["capabilities"]["synaptic_plasticity"] is True
    assert graph["capabilities"]["structural_plasticity"] is False


def test_prediction_deadline_enters_neural_graph_without_creating_duplicate_wake(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    before = len(runtime.core.snapshot().pending_plans)
    runtime.emit_prediction_due(prediction_ref="prediction:test", observed_at=now)
    snap = runtime.core.snapshot()
    assert len(snap.pending_plans) == before
    assert snap.neural_state["neurons"]["prediction_due"]["membrane"] > 0.0
    assert snap.neural_state["neurons"]["prediction_due"]["fire_count"] == 0
    assert not any(row.source_event_id.startswith("prediction-due:")
                   for row in snap.neural_control_signals)


def test_prospective_prediction_outcome_changes_existing_path_and_is_idempotent(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    before = runtime.public_snapshot()["neural_graph"]
    before_weight = next(
        row["weight"] for row in before["synapses"]
        if row["source"] == "prediction_due" and row["target"] == "task_urgency"
    )
    outcome = {
        "prediction_id": "content-action-prediction:0123456789abcdef01234567",
        "window": "24h", "expected_pageviews": 4.0, "actual_pageviews": 8.0,
        "prediction_error": 4.0 / 13.0, "reliability": 1.0,
        "observed_at": NOW.isoformat(),
        "evidence_refs": ["content-action-prediction:0123456789abcdef01234567", "nginx:test"],
    }
    first = runtime.apply_prediction_outcome_feedback(outcome)
    after = runtime.public_snapshot()["neural_graph"]
    after_weight = next(
        row["weight"] for row in after["synapses"]
        if row["source"] == "prediction_due" and row["target"] == "task_urgency"
    )
    assert first["status"] == "APPLIED"
    assert after_weight > before_weight
    assert after["neurons"]["prediction_due"]["input_gain"] > 1.0
    assert after["neurons"]["prediction_due"]["threshold"] < before["neurons"]["prediction_due"]["threshold"]
    duplicate = runtime.apply_prediction_outcome_feedback(outcome)
    assert duplicate["status"] == "ALREADY_APPLIED"
    assert runtime.public_snapshot()["neural_graph"] == after


def test_prediction_feedback_survives_content_runtime_restart(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    outcome = {
        "prediction_id": "content-action-prediction:fedcba9876543210fedcba98",
        "window": "4h", "expected_pageviews": 9.0, "actual_pageviews": 3.0,
        "prediction_error": 6.0 / 13.0, "reliability": 0.65,
        "observed_at": NOW.isoformat(), "evidence_refs": ["prediction:test", "nginx:test"],
    }
    runtime.apply_prediction_outcome_feedback(outcome)
    snapshot = runtime.public_snapshot()
    restored = ContentWakefulRuntime(state_root=tmp_path).public_snapshot()
    assert restored["neural_graph"] == snapshot["neural_graph"]
    assert restored["neural_feedback"][-1]["outcome_id"].endswith(":4h")


def _write_action_prediction_cycle(root, *, published_at):
    import json
    cycle = root / "content" / "continuous" / "cycles" / "v2-deadline"
    cycle.mkdir(parents=True)
    (cycle / "action_prediction.json").write_text("{}", encoding="utf-8")
    (cycle / "publication.json").write_text(json.dumps({
        "outcome_attribution": {"published_at": published_at.isoformat()}
    }), encoding="utf-8")
    outcomes = cycle / "action_prediction_outcomes"
    outcomes.mkdir()
    (outcomes / "1h.json").write_text("{}", encoding="utf-8")
    return cycle


def test_outcome_check_wakes_at_known_maturity_before_hourly_fallback(tmp_path):
    cycle = _write_action_prediction_cycle(tmp_path, published_at=NOW - timedelta(hours=3, minutes=57))
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_next("CHECK_OUTCOMES", NOW)
    wake = next(row for row in runtime.core.snapshot().pending_plans if row.task == "CHECK_OUTCOMES")
    assert wake.at == NOW + timedelta(minutes=3)
    assert not (cycle / "action_prediction_outcomes" / "4h.json").exists()


def test_outcome_deadline_never_delays_hourly_fallback(tmp_path):
    cycle = _write_action_prediction_cycle(tmp_path, published_at=NOW - timedelta(hours=3, minutes=57))
    (cycle / "action_prediction_outcomes" / "4h.json").write_text("{}", encoding="utf-8")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_next("CHECK_OUTCOMES", NOW)
    wake = next(row for row in runtime.core.snapshot().pending_plans if row.task == "CHECK_OUTCOMES")
    assert wake.at == NOW + timedelta(hours=1)


def test_connectome_provider_degrades_without_becoming_a_life_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_ENABLED", "1")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_SOURCE", str(tmp_path / "missing-connectome.npz"))
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    snapshot = runtime.public_snapshot()
    assert snapshot["presence"] == "AWAKE"
    assert snapshot["connectome"]["enabled"] is True
    assert snapshot["connectome"]["status"] == "DEGRADED"
    assert snapshot["connectome"]["error"] is not None
    assert "substrate" not in snapshot["neural_graph"]


def test_real_minifly_shadow_substrate_is_separate_from_semantic_ports(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    pytest.importorskip("flydrones")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_ENABLED", "1")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_SOURCE", "minifly")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_ELECTRODE_SIZE", "12")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_STIMULUS_MS", "10")
    monkeypatch.setenv("CONTENT_FLY_CONNECTOME_SETTLE_MS", "20")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime.emit_network_error(code="shadow-test", observed_at=now)
    snapshot = runtime.public_snapshot()
    substrate = snapshot["neural_graph"]["substrate"]
    assert len(snapshot["neural_graph"]["neurons"]) == 8
    assert substrate["neuron_count"] == 850
    assert substrate["connection_count"] == 4_928
    assert substrate["lifecycle"] == "SHADOW"
    assert substrate["auxiliary_electrodes"] is True
    assert substrate["auxiliary_channel_count"] == 8
    assert "sense_cpu_pressure" in substrate["auxiliary_channels"]
    assert substrate["recent_bursts"][-1]["synaptic_events"] > 0
    assert snapshot["connectome"]["status"] == "SHADOW_ACTIVE"

    manifest_path = tmp_path / "application" / "neural_topology.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "ferro-neural-topology.v1"
    assert manifest["enabled"] is True
    assert manifest["source"] == "minifly"
    assert manifest["electrode_size"] == 12

    # A Researcher/offline process may not inherit the production systemd
    # environment.  It must still restore exactly the same persisted body
    # topology instead of falling back to a semantically incompatible graph.
    for key in (
        "CONTENT_FLY_CONNECTOME_ENABLED",
        "CONTENT_FLY_CONNECTOME_SOURCE",
        "CONTENT_FLY_CONNECTOME_UPSTREAM_COMMIT",
        "CONTENT_FLY_CONNECTOME_ELECTRODE_SIZE",
        "CONTENT_FLY_CONNECTOME_INPUT_HZ",
        "CONTENT_FLY_CONNECTOME_STIMULUS_MS",
        "CONTENT_FLY_CONNECTOME_SETTLE_MS",
        "CONTENT_FLY_CONNECTOME_BASELINE_MS",
        "CONTENT_NEUROGENESIS_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    restored = ContentWakefulRuntime(state_root=tmp_path)
    restored_snapshot = restored.public_snapshot()
    assert restored_snapshot["connectome"]["status"] == "SHADOW_ACTIVE"
    assert restored_snapshot["neural_graph"]["substrate"]["neuron_count"] == 850
    assert restored_snapshot["neural_graph"]["substrate"]["auxiliary_channel_count"] == 8


def test_action_commitment_is_a_stable_provenance_bound_neural_stimulus(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    prediction_id = "content-action-prediction:aaaaaaaaaaaaaaaaaaaaaaaa"
    first = runtime.emit_action_commitment(prediction_id=prediction_id, observed_at=NOW)
    second = runtime.emit_action_commitment(prediction_id=prediction_id, observed_at=NOW + timedelta(seconds=5))
    assert first["event_id"] == f"action:{prediction_id}"
    assert second["event_id"] == first["event_id"]
    matching = [row for row in runtime.core.snapshot().recent_stimuli if row.event_id == first["event_id"]]
    assert len(matching) == 1
    assert matching[0].kind == "ACTION_COMMITMENT"
    assert matching[0].payload_ref == prediction_id


def test_prediction_outcome_feedback_points_to_exact_action_commitment(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    prediction_id = "content-action-prediction:bbbbbbbbbbbbbbbbbbbbbbbb"
    commitment = runtime.emit_action_commitment(prediction_id=prediction_id, observed_at=NOW)
    outcome = {
        "prediction_id": prediction_id,
        "window": "1h", "expected_pageviews": 2.0, "actual_pageviews": 4.0,
        "prediction_error": 2.0 / 7.0, "reliability": 0.9,
        "observed_at": (NOW + timedelta(hours=1)).isoformat(),
        "evidence_refs": [prediction_id, "nginx:outcome-1"],
    }
    result = runtime.apply_prediction_outcome_feedback(outcome)
    assert result["status"] == "APPLIED"
    feedback = runtime.core.snapshot().recent_neural_feedback[-1]
    assert feedback.source_event_id == commitment["event_id"] == f"action:{prediction_id}"
    assert runtime.public_snapshot()["neural_feedback"][-1]["source_event_id"] == f"action:{prediction_id}"

def test_normal_resource_cadence_does_not_postpone_earlier_followup(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.schedule_resource_followup(NOW, seconds=300)
    runtime.schedule_next("RESOURCE_REVIEW", NOW)
    wake = next(row for row in runtime.core.snapshot().pending_plans if row.task == "RESOURCE_REVIEW")
    assert wake.at == NOW + timedelta(minutes=5)
    assert wake.reason == "continue unresolved autonomous resource verification"


def test_schedule_next_can_still_make_existing_plan_earlier(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.core.schedule(PlannedWake(
        wake_id="content:RESOURCE_REVIEW", at=NOW + timedelta(hours=2),
        reason="later placeholder", task="RESOURCE_REVIEW",
    ))
    runtime.schedule_next("RESOURCE_REVIEW", NOW)
    wake = next(row for row in runtime.core.snapshot().pending_plans if row.task == "RESOURCE_REVIEW")
    assert wake.at == NOW + timedelta(hours=1)



def test_external_self_maintenance_request_wakes_live_runtime_without_restart(tmp_path):
    live = ContentWakefulRuntime(
        state_root=tmp_path,
        content_interval_hours=6,
        watchdog_seconds=60,
    )
    now = datetime.now(timezone.utc)
    for name in ("survival_resources", "llm_resource_pool"):
        policy = tmp_path / "application" / name / "policy.json"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(json.dumps({"observed_at": now.isoformat(), "pressure": "NORMAL"}))
    live.core.schedule(PlannedWake(
        wake_id="content:REFLECT",
        at=now + timedelta(hours=1),
        reason="daily reflection",
        task="REFLECT",
    ))
    results = []

    thread = threading.Thread(
        target=lambda: results.append(live.wait(stopping=lambda: False)),
        daemon=True,
    )
    thread.start()
    time.sleep(0.15)

    external = ContentWakefulRuntime(
        state_root=tmp_path,
        content_interval_hours=6,
        watchdog_seconds=60,
    )
    external._enqueue_external_wake(PlannedWake(
        wake_id="content:REFLECT",
        at=datetime.now(timezone.utc) + timedelta(milliseconds=350),
        reason="diagnose production failure and form bounded self-repair hypothesis",
        task="REFLECT",
    ))

    thread.join(timeout=3.0)

    assert thread.is_alive() is False
    assert len(results) == 1
    assert results[0].kind == "PLAN"
    assert results[0].wake_id == "content:REFLECT"
    assert results[0].task == "REFLECT"
    assert list((tmp_path / "application/wake_requests").glob("*.json")) == []


def test_external_wake_requests_can_only_make_existing_plan_earlier(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime.now(timezone.utc)
    original_at = now + timedelta(minutes=10)
    runtime.core.schedule(PlannedWake(
        wake_id="content:REFLECT",
        at=original_at,
        reason="daily reflection",
        task="REFLECT",
    ))

    runtime._enqueue_external_wake(PlannedWake(
        wake_id="content:REFLECT",
        at=now + timedelta(minutes=20),
        reason="later request must not postpone",
        task="REFLECT",
    ))
    assert runtime._drain_external_wake_requests() == 0
    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:REFLECT"
    )
    assert wake.at == original_at

    earlier_at = now + timedelta(minutes=1)
    runtime._enqueue_external_wake(PlannedWake(
        wake_id="content:REFLECT",
        at=earlier_at,
        reason="urgent self maintenance",
        task="REFLECT",
    ))
    assert runtime._drain_external_wake_requests() == 1
    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:REFLECT"
    )
    assert wake.at == earlier_at
    assert wake.reason == "urgent self maintenance"


def test_initial_plans_drain_persisted_external_wake_before_startup_defaults(tmp_path):
    external = ContentWakefulRuntime(state_root=tmp_path)
    recovery_at = NOW - timedelta(seconds=30)
    external._enqueue_external_wake(PlannedWake(
        wake_id="content:CONTENT_COGNITION",
        at=recovery_at,
        reason="retry content cognition after temporary LLM provider outage",
        task="CONTENT_COGNITION",
        relevant_refs=("llm-resource-cooldown:test",),
        preemptible=True,
    ))

    restored = ContentWakefulRuntime(state_root=tmp_path)
    restored.ensure_initial_plans(NOW)

    wake = next(
        row for row in restored.core.snapshot().pending_plans
        if row.wake_id == "content:CONTENT_COGNITION"
    )
    assert wake.at == recovery_at
    assert wake.reason == "retry content cognition after temporary LLM provider outage"
    assert wake.relevant_refs == ("llm-resource-cooldown:test",)
    assert list((tmp_path / "application/wake_requests").glob("*.json")) == []


def test_external_wake_spool_rejects_unapproved_task(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    root = tmp_path / "application/wake_requests"
    root.mkdir(parents=True)
    request = root / "untrusted.json"
    request.write_text(json.dumps({
        "schema_version": "content-wake-request.v1",
        "wake_id": "content:DEPLOY",
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": "try to gain deployment authority",
        "task": "DEPLOY",
        "preemptible": True,
    }), encoding="utf-8")

    merged = runtime._drain_external_wake_requests()

    assert merged == 0
    assert request.exists() is False
    assert not any(
        row.wake_id == "content:DEPLOY"
        for row in runtime.core.snapshot().pending_plans
    )


def test_schedule_self_maintenance_persists_cross_process_request(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime.now(timezone.utc)

    runtime.schedule_self_maintenance(now, seconds=30)

    requests = list((tmp_path / "application/wake_requests").glob("*.json"))
    assert len(requests) == 1
    payload = json.loads(requests[0].read_text(encoding="utf-8"))
    assert payload["wake_id"] == "content:REFLECT"
    assert payload["task"] == "REFLECT"
    assert payload["at"] == (now + timedelta(seconds=30)).isoformat()


def test_reflect_has_periodic_heartbeat_instead_of_daily_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_REFLECTION_INTERVAL_MINUTES", "45")
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    runtime.schedule_next("REFLECT", NOW)

    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:REFLECT"
    )
    assert wake.at == NOW + timedelta(minutes=45)
    assert wake.task == "REFLECT"
    assert any(
        ref.startswith("reflection-trigger:periodic:")
        for ref in wake.relevant_refs
    )


def test_meaningful_outcome_schedules_near_term_reflection(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    result = runtime.schedule_reflection_from_outcome(
        "VERIFY_PREDICTION",
        {
            "status": "OK",
            "verification": {"status": "VERIFIED"},
        },
        NOW,
    )

    assert result["status"] == "SCHEDULED"
    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:REFLECT"
    )
    assert wake.at == NOW + timedelta(seconds=60)
    assert "task-result:VERIFY_PREDICTION:OK" in wake.relevant_refs
    assert any(
        ref.startswith("reflection-trigger:event:")
        for ref in wake.relevant_refs
    )


def test_routine_outcome_does_not_force_reflection(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    result = runtime.schedule_reflection_from_outcome(
        "RESOURCE_REVIEW",
        {"status": "OK"},
        NOW,
    )
    assert result["status"] == "NOT_SALIENT"


def test_schedule_provider_recovery_uses_earliest_runtime_cooldown(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime(2026, 9, 24, 7, 20, 0, tzinfo=timezone.utc)
    health = (
        tmp_path
        / "application"
        / "llm_resource_pool"
        / "runtime"
        / "health.json"
    )
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({
        "schema_version": "llm-resource-health.v1",
        "resources": {
            "later": {"cooldown_until": (now + timedelta(minutes=10)).isoformat()},
            "earlier": {"cooldown_until": (now + timedelta(seconds=90)).isoformat()},
            "past": {"cooldown_until": (now - timedelta(seconds=1)).isoformat()},
        },
    }), encoding="utf-8")

    result = runtime.schedule_provider_recovery(now)

    assert result["status"] == "SCHEDULED"
    assert result["retry_seconds"] == 95
    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:CONTENT_COGNITION"
    )
    assert wake.at == now + timedelta(seconds=95)
    assert wake.relevant_refs == (
        f"llm-resource-cooldown:earlier:{(now + timedelta(seconds=90)).isoformat()}",
    )
    requests = list((tmp_path / "application/wake_requests").glob("*.json"))
    assert len(requests) == 1


def test_schedule_provider_recovery_ignores_unaffordable_paid_cooldown(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime(2026, 9, 24, 7, 20, 0, tzinfo=timezone.utc)
    pool_root = tmp_path / "application" / "llm_resource_pool"
    runtime_root = pool_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    paid_at = now + timedelta(seconds=30)
    free_at = now + timedelta(seconds=90)
    (runtime_root / "health.json").write_text(json.dumps({
        "schema_version": "llm-resource-health.v1",
        "resources": {
            "paid": {"cooldown_until": paid_at.isoformat()},
            "free": {"cooldown_until": free_at.isoformat()},
        },
    }), encoding="utf-8")
    (pool_root / "resources.json").write_text(json.dumps({
        "resources": [
            {"resource_id": "paid", "pricing_class": "PAID"},
            {"resource_id": "free", "pricing_class": "FREE"},
        ],
    }), encoding="utf-8")
    (pool_root / "policy.json").write_text(json.dumps({
        "paid_available_funds": 0.0,
        "paid_reserve_floor": 20.0,
    }), encoding="utf-8")

    result = runtime.schedule_provider_recovery(now)

    assert result["status"] == "SCHEDULED"
    assert result["retry_seconds"] == 95
    assert result["evidence_ref"] == (
        f"llm-resource-cooldown:free:{free_at.isoformat()}"
    )


def test_schedule_provider_recovery_prefers_runtime_proven_route(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime(2026, 9, 24, 8, 27, 0, tzinfo=timezone.utc)
    pool_root = tmp_path / "application" / "llm_resource_pool"
    runtime_root = pool_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    never_succeeded_at = now + timedelta(seconds=73)
    proven_at = now + timedelta(seconds=84)
    (runtime_root / "health.json").write_text(json.dumps({
        "schema_version": "llm-resource-health.v1",
        "resources": {
            "never-succeeded": {
                "cooldown_until": never_succeeded_at.isoformat(),
                "successes": 0,
                "failures": 223,
            },
            "runtime-proven": {
                "cooldown_until": proven_at.isoformat(),
                "successes": 38,
                "failures": 294,
                "last_success_at": (now - timedelta(hours=4)).isoformat(),
            },
        },
    }), encoding="utf-8")
    (pool_root / "resources.json").write_text(json.dumps({
        "resources": [
            {"resource_id": "never-succeeded", "pricing_class": "FREE"},
            {"resource_id": "runtime-proven", "pricing_class": "FREE"},
        ],
    }), encoding="utf-8")
    (pool_root / "policy.json").write_text(json.dumps({
        "paid_available_funds": 0.0,
        "paid_reserve_floor": 20.0,
    }), encoding="utf-8")

    result = runtime.schedule_provider_recovery(now)

    assert result["status"] == "SCHEDULED"
    assert result["retry_seconds"] == 89
    assert result["evidence_ref"] == (
        f"llm-resource-cooldown:runtime-proven:{proven_at.isoformat()}"
    )


def test_schedule_provider_recovery_retries_originating_task_and_drops_expired_request_ref(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    now = datetime(2026, 9, 24, 8, 27, 0, tzinfo=timezone.utc)

    result = runtime.schedule_provider_recovery(
        now,
        default_seconds=60,
        task="REFLECT",
        relevant_refs=(
            "cognition-request:llmreq-expired",
            "reflection-date:2026-09-23",
        ),
    )

    assert result["status"] == "SCHEDULED"
    wake = next(
        row
        for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:REFLECT"
    )
    assert wake.task == "REFLECT"
    assert "reflection-date:2026-09-23" in wake.relevant_refs
    assert "cognition-request:llmreq-expired" not in wake.relevant_refs



def test_brain_lab_uses_handoff_environment_and_settles_trial(tmp_path, monkeypatch):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    captured = {}

    def choose(**kwargs):
        captured["choose"] = kwargs
        return {
            "status": "ASSIGNED",
            "function_id": "BRAIN_LAB_ENVIRONMENT_SELECTION",
            "environment": "MAZE",
            "authority": "NEURAL_ORGAN",
            "organ_id": "organ_001",
            "trial": {
                "decision_id": "organ-handoff:test",
                "organ_id": "organ_001",
                "arm": "NEURAL_ORGAN",
                "environment": "MAZE",
            },
            "external_action_authority": False,
        }

    def settle(**kwargs):
        captured["settle"] = kwargs
        return {
            "decision_id": kwargs["decision_id"],
            "status": "SETTLED",
            "evaluation": {"status": "COLLECTING_EVIDENCE"},
        }

    monkeypatch.setattr(runtime.neural_organ_handoff, "choose", choose)
    monkeypatch.setattr(runtime.neural_organ_handoff, "settle", settle)

    result = runtime.run_brain_lab(now=NOW, steps=3)

    assert result["environment"] == "MAZE"
    assert result["organ_handoff"]["decision"]["authority"] == "NEURAL_ORGAN"
    assert result["organ_handoff"]["settled"]["status"] == "SETTLED"
    assert captured["settle"]["decision_id"] == "organ-handoff:test"
    assert captured["settle"]["result"]["environment"] == "MAZE"
    assert "brain_lab" in captured["choose"]
    assert "substrate_projection" in captured["choose"]


def test_explicit_brain_lab_environment_bypasses_organ_handoff(tmp_path, monkeypatch):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    def forbidden_choose(**kwargs):
        raise AssertionError("explicit environment must bypass organ handoff")

    monkeypatch.setattr(runtime.neural_organ_handoff, "choose", forbidden_choose)

    result = runtime.run_brain_lab(
        now=NOW,
        environment="FORAGING",
        steps=2,
    )

    assert result["environment"] == "FORAGING"
    decision = result["organ_handoff"]["decision"]
    assert decision["status"] == "EXPLICIT_ENVIRONMENT_OVERRIDE"
    assert decision["authority"] == "CALLER_EXPLICIT"
    assert decision["trial"] is None


def test_public_snapshot_exposes_reversible_neural_organ_handoff(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    handoff = runtime.public_snapshot()["neural_organ_handoff"]

    assert handoff["function_id"] == "BRAIN_LAB_ENVIRONMENT_SELECTION"
    assert handoff["phase"] == "EVALUATING"
    assert handoff["python_baseline_preserved"] is True
    assert handoff["automatic_rollback"] is True
    assert handoff["external_action_authority"] is False
    assert handoff["llm_used_for_handoff"] is False



def test_neural_refresh_records_longitudinal_individuality_without_llm_dependency(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    runtime.refresh_neural_life(NOW)
    snapshot = runtime.public_snapshot()
    individuality = snapshot["longitudinal_individuality"]

    assert individuality["snapshot_count"] == 1
    assert individuality["organism_id"] == snapshot["cortex_continuity"]["organism_id"]
    assert individuality["month_comparison"]["status"] == "ACCUMULATING_HISTORY"
    assert individuality["cortex_in_individuality_fingerprint"] is False
    assert individuality["raw_relationship_ids_public"] is False
    assert (tmp_path / "application" / "longitudinal_individuality.json").exists()


def test_frequent_neural_refresh_does_not_manufacture_longitudinal_age(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    runtime.refresh_neural_life(NOW)
    runtime.refresh_neural_life(NOW + timedelta(hours=2))

    individuality = runtime.public_snapshot()["longitudinal_individuality"]
    assert individuality["snapshot_count"] == 1
    assert individuality["month_comparison"]["history_days"] == 0.0



def test_neural_refresh_schedules_bounded_open_ended_evolution_consolidation(tmp_path, monkeypatch):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    pending_cycle = {
        "cycle_id": "evolution-cycle:test",
        "need_id": "development-need:test",
        "proposal_id": "development-proposal:test",
        "world_id": "WORLD_0001",
        "status": "ADOPTED_AWAITING_EMERGENCE",
    }

    monkeypatch.setattr(
        runtime.open_ended_evolution,
        "refresh",
        lambda **kwargs: {"recent_cycles": [pending_cycle]},
    )
    monkeypatch.setattr(
        runtime.open_ended_evolution,
        "next_consolidation",
        lambda: dict(pending_cycle),
    )

    runtime.refresh_neural_life(NOW)

    wake = next(
        row
        for row in runtime.core.snapshot().pending_plans
        if row.task == "NEURAL_EVOLUTION_CONSOLIDATION"
    )
    assert wake.wake_id == "content:NEURAL_EVOLUTION_CONSOLIDATION"
    assert "WORLD_0001" in wake.reason
    assert "evolution-cycle:test" in wake.relevant_refs


def test_evolution_consolidation_persists_topology_before_final_verdict(tmp_path, monkeypatch):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    calls = []

    monkeypatch.setattr(
        runtime.open_ended_evolution,
        "run_consolidation",
        lambda **kwargs: {
            "status": "ADOPTED_AWAITING_EMERGENCE",
            "cycle_id": "evolution-cycle:test",
            "world_id": "WORLD_0001",
        },
    )

    def refresh(**kwargs):
        calls.append("refresh")
        return {
            "recent_cycles": [{
                "cycle_id": "evolution-cycle:test",
                "world_id": "WORLD_0001",
                "status": "EVOLVED",
                "completion_kind": "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT",
            }]
        }

    monkeypatch.setattr(runtime.open_ended_evolution, "refresh", refresh)
    original_persist = runtime.core._persist

    def persist():
        calls.append("persist")
        return original_persist()

    monkeypatch.setattr(runtime.core, "_persist", persist)

    result = runtime.run_evolution_consolidation(NOW, steps=24)

    assert calls[:2] == ["persist", "refresh"]
    assert result["status"] == "EVOLVED"
    assert result["cycle"]["completion_kind"] == (
        "ADOPTED_TRANSFER_STABLE_WORLD_CIRCUIT"
    )


def test_public_snapshot_exposes_n36_without_claiming_semantic_capability(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    projection = runtime.public_snapshot()["open_ended_evolution"]

    assert projection["completion_requires_real_world_adoption"] is True
    assert projection[
        "completion_requires_world_linked_stable_circuit_or_organ"
    ] is True
    assert projection["single_neuron_is_not_called_a_new_capability"] is True
    assert projection["semantic_capability_claim"] is False
    assert projection["llm_used_for_cycle_selection"] is False



def test_public_snapshot_separates_engineering_frontier_from_production_evidence(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)

    audit = runtime.public_snapshot()["open_development_acceptance"]

    assert audit["scope"] == "N26-N36"
    assert audit["engineering_frontier"] == "N36"
    assert audit["production_behavior_accepted_count"] == 0
    assert audit["production_behavior_missing_count"] == 11
    assert audit["production_accepted_contiguously_through"] is None


def test_expired_action_loops_are_closed_during_rest(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    old = NOW - timedelta(hours=2)
    runtime.neural_motor.state["decisions"] = [{
        "decision_id": "motor-decision:expired", "created_at": old.isoformat(),
        "status": "PENDING_OUTCOME", "task": "CHECK_X",
    }]
    runtime.neural_transfer.state["experiments"] = [{
        "experiment_id": "experiment:expired", "created_at": old.isoformat(),
        "status": "AWAITING_TARGET_OUTCOME",
    }]
    runtime.developmental_world_transfer.state["trials"] = [{
        "trial_id": "trial:expired", "created_at": old.isoformat(),
        "status": "AWAITING_TARGET_OUTCOME",
    }]
    for wake_id in (
        "motor:motor-decision:expired",
        "transfer-target:experiment:expired",
        "development-target:trial:expired",
    ):
        runtime.core.schedule(PlannedWake(
            wake_id=wake_id, at=NOW, reason="stale target", task="CHECK_X",
        ))

    runtime._expire_unsettled_actions(NOW)

    assert runtime.neural_motor.state["decisions"][0]["status"] == "TIMED_OUT"
    assert runtime.neural_transfer.state["experiments"][0]["status"] == "INCONCLUSIVE"
    assert runtime.developmental_world_transfer.state["trials"][0]["status"] == "INCONCLUSIVE"
    assert not any(
        row.wake_id.endswith(":expired")
        for row in runtime.core.snapshot().pending_plans
    )


def test_prediction_verification_error_defers_same_prediction_for_one_hour(tmp_path):
    from wechat_traffic_agent.prospective_predictions import ProspectivePredictionLedger

    ledger = ProspectivePredictionLedger(tmp_path)
    prediction = ledger.record(
        cycle="v2-retry",
        topic="btc",
        proposition="BTC liquidity conditions matter for execution quality.",
        proposition_stance=0.1,
        statement="BTC execution evidence will remain mixed at the horizon.",
        confidence=0.6,
        created_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(hours=1),
        verification_query="BTC execution evidence",
        falsifier="Fresh evidence clearly resolves the execution claim.",
        source_evidence_refs=("e:retry",),
    )
    ledger.record_verification_error(
        prediction.prediction_id,
        attempted_at=NOW,
        error_type="LLMProviderError",
    )

    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.ensure_initial_plans(NOW)

    wake = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == f"wake:{prediction.prediction_id}"
    )
    assert wake.at == NOW + timedelta(hours=1)
    assert wake.relevant_refs == (prediction.prediction_id,)


def test_unchanged_world_learning_due_set_does_not_self_reschedule_every_refresh(tmp_path):
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.world_learning.commit(
        action_kind="TEST",
        prediction="A delayed outcome should eventually be observed.",
        expected_utility=0.5,
        confidence=0.5,
        evidence_refs=("test:evidence",),
        now=NOW,
        windows=(("10m", timedelta(seconds=-1)),),
    )

    runtime.refresh_neural_life(NOW)
    first = next(
        row for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "content:CHECK_OUTCOMES"
    )
    assert first.at == NOW + timedelta(seconds=10)

    # Simulate the fast check being consumed without new evidence. The same
    # unresolved due set must fall back to the normal cadence, not recreate the
    # ten-second wake on every neural refresh.
    runtime.core.cancel("content:CHECK_OUTCOMES")
    runtime.refresh_neural_life(NOW + timedelta(seconds=20))

    assert not any(
        row.wake_id == "content:CHECK_OUTCOMES"
        and row.at <= NOW + timedelta(minutes=5)
        for row in runtime.core.snapshot().pending_plans
    )
