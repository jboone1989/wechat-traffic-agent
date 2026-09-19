from datetime import datetime, timedelta, timezone
import json
import threading
import time

from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime
from agent_runtime.wakeful import PlannedWake


NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


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
    assert substrate["recent_bursts"][-1]["synaptic_events"] > 0
    assert snapshot["connectome"]["status"] == "SHADOW_ACTIVE"


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
