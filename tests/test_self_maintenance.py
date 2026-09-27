from datetime import datetime, timedelta, timezone
import json
from json import loads
from types import SimpleNamespace

from wechat_traffic_agent.self_maintenance import SelfMaintenanceService


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
KNOWN = "BELIEF_CHANGED requires a persisted worldview change"


class EvolutionRuntime:
    def __init__(self):
        self.calls = []
        self.persisted = 0

    def observe_evolution_fact(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(fact_id="evf:self-maintenance")

    def persist(self):
        self.persisted += 1


def _failure(cycle="v2-1", message=KNOWN):
    return {
        "status": "ERROR",
        "cycle": cycle,
        "error_type": "ValueError",
        "message": message,
    }


def test_known_content_failure_becomes_durable_problem_diagnosis_and_repair_proposal(tmp_path):
    evolution = EvolutionRuntime()
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=evolution,
    )

    result = service.ingest_failure(_failure(), now=NOW)

    assert result["status"] == "RECORDED"
    assert result["problem_status"] == "REPAIR_PROPOSED"
    assert result["repair_proposal"]["repair_kind"] == "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW"
    assert result["evolution"]["status"] == "RECORDED"
    assert evolution.persisted == 1
    assert evolution.calls[0]["kind"].value == "FAILURE"
    assert evolution.calls[0]["target_capability"] == "self-maintenance.content-cognition"

    latest = loads((tmp_path / "application/self_maintenance/latest.json").read_text())
    assert latest["occurrences"] == 1
    assert latest["cycles"] == ["v2-1"]
    assert latest["status"] == "REPAIR_PROPOSED"
    case = loads(next((tmp_path / "application/self_maintenance/diagnostics/cases").glob("*.json")).read_text())
    assert case["stage"] == "REMEDIATE"
    assert case["conclusion"]["scope"] == "content-cognition-contract"


def test_replaying_same_cycle_is_idempotent(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    first = service.ingest_failure(_failure(), now=NOW)
    second = service.ingest_failure(_failure(), now=NOW)

    assert first["status"] == "RECORDED"
    assert second["status"] == "ALREADY_RECORDED"
    latest = loads((tmp_path / "application/self_maintenance/latest.json").read_text())
    assert latest["occurrences"] == 1


def test_recurring_signature_across_cycles_increments_problem_and_preserves_identity(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    first = service.ingest_failure(_failure("v2-1"), now=NOW)
    second = service.ingest_failure(_failure("v2-2"), now=NOW)

    assert first["problem_id"] == second["problem_id"]
    latest = loads((tmp_path / "application/self_maintenance/latest.json").read_text())
    assert latest["occurrences"] == 2
    assert latest["cycles"] == ["v2-1", "v2-2"]


def test_unknown_error_is_preserved_for_diagnosis_without_inventing_repair(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    result = service.ingest_failure(
        {
            "status": "ERROR",
            "cycle": "v2-x",
            "error_type": "RuntimeError",
            "message": "unexpected provider failure",
        },
        now=NOW,
    )

    assert result["status"] == "RECORDED"
    assert result["problem_status"] == "DIAGNOSING"
    assert result["repair_proposal"] is None


def test_backfill_imports_historical_failed_cycle(tmp_path):
    result_path = tmp_path / "content/continuous/cycles/v2-9/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        '{"status":"ERROR","cycle":"v2-9","error_type":"ValueError","message":"'
        + KNOWN
        + '"}\n'
    )
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )

    result = service.backfill_recent_failures(now=NOW)

    assert result["recorded"] == 1
    assert service.review()["open_count"] == 1
    assert service.review()["repair_proposed_count"] == 1


def test_malformed_model_json_failure_gets_fail_closed_repair_proposal(tmp_path):
    evolution = EvolutionRuntime()
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=evolution,
    )

    result = service.ingest_failure(
        {
            "status": "ERROR",
            "cycle": "v2-json",
            "error_type": "ValueError",
            "message": "backend did not return a JSON object",
            "failure_site": "content_goal_loop.py:_run>content_explorer.py:filter_for_goal",
        },
        now=NOW,
    )

    assert result["status"] == "RECORDED"
    assert result["problem_status"] == "REPAIR_PROPOSED"
    assert result["repair_proposal"]["repair_kind"] == "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT"
    assert result["repair_proposal"]["target_files"] == [
        "src/wechat_traffic_agent/content_explorer.py"
    ]


def test_content_composition_json_failure_gets_structured_output_repair_proposal(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )

    result = service.ingest_failure(
        {
            "status": "ERROR",
            "cycle": "v2-compose-json",
            "error_type": "ValueError",
            "message": "backend did not return a JSON object",
            "failure_site": "content_goal_loop.py:_run>content_service.py:compose",
        },
        now=NOW,
    )

    assert result["problem_status"] == "REPAIR_PROPOSED"
    assert result["repair_proposal"]["repair_kind"] == (
        "USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION"
    )
    assert result["repair_proposal"]["target_files"] == [
        "src/wechat_traffic_agent/content_service.py"
    ]


def test_review_enriches_existing_composition_json_problem(tmp_path):
    repair_runtime = RepairCapabilityRuntime()
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
        capability_runtime=repair_runtime,
    )
    problem = tmp_path / "application/self_maintenance/problems/compose.json"
    problem.parent.mkdir(parents=True)
    problem.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:compose",
        "status": "DIAGNOSING",
        "symptom": "backend did not return a JSON object",
        "error_type": "ValueError",
        "failure_site": "content_goal_loop.py:_run>content_service.py:compose",
        "occurrences": 1,
        "cycles": ["v2-compose-json"],
        "last_seen_at": NOW.isoformat(),
        "repair_proposal": None,
    }))

    review = service.review()

    assert review["repair_proposed_count"] == 1
    assert review["repair_requested_count"] == 1
    updated = json.loads(problem.read_text())
    assert updated["repair_proposal"]["repair_kind"] == (
        "USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION"
    )
    assert updated["status"] == "REPAIR_REQUESTED"
    assert updated["repair_request"]["requestId"] == "repair-123"
    assert len(repair_runtime.calls) == 1
    capability, payload = repair_runtime.calls[0]
    assert capability == "REQUEST_CODE_REPAIR"
    assert payload["targetFiles"] == ["src/wechat_traffic_agent/content_service.py"]


def test_review_enriches_historical_known_problem_with_new_repair_knowledge(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    problem = tmp_path / "application/self_maintenance/problems/historical.json"
    problem.parent.mkdir(parents=True)
    problem.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:historical",
        "status": "DIAGNOSING",
        "symptom": "backend did not return a JSON object",
        "error_type": "ValueError",
        "failure_site": "content_goal_loop.py:_run>content_explorer.py:filter_for_goal",
        "occurrences": 6,
        "cycles": ["v2-1", "v2-2"],
        "last_seen_at": NOW.isoformat(),
        "repair_proposal": None,
    }))

    review = service.review()

    assert review["repair_proposed_count"] == 1
    updated = json.loads(problem.read_text())
    assert updated["status"] == "REPAIR_PROPOSED"
    assert updated["repair_proposal"]["repair_kind"] == "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT"


def _diagnostic(state_root, cycle, frames):
    path = state_root / "content/continuous/cycles" / cycle / "error_diagnostic.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    trace = "Traceback (most recent call last):\n" + "\n".join(
        f'  File "/repo/src/wechat_traffic_agent/{name}", line 10, in {func}\n    x()'
        for name, func in frames
    ) + "\nValueError: backend did not return a JSON object\n"
    path.write_text(json.dumps({"traceback": trace}))


def test_same_error_text_at_different_callsites_becomes_different_self_problems(tmp_path):
    _diagnostic(tmp_path, "v2-filter", [
        ("content_loop.py", "run_once"),
        ("content_goal_loop.py", "_run"),
        ("content_explorer.py", "filter_for_goal"),
        ("content_service.py", "_extract_json"),
    ])
    _diagnostic(tmp_path, "v2-select", [
        ("content_loop.py", "run_once"),
        ("content_goal_loop.py", "_run"),
        ("content_loop.py", "_structured"),
        ("content_service.py", "_extract_json"),
    ])
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )

    first = service.ingest_failure({
        "status": "ERROR", "cycle": "v2-filter", "error_type": "ValueError",
        "message": "backend did not return a JSON object",
    }, now=NOW)
    second = service.ingest_failure({
        "status": "ERROR", "cycle": "v2-select", "error_type": "ValueError",
        "message": "backend did not return a JSON object",
    }, now=NOW)

    assert first["problem_id"] != second["problem_id"]
    assert first["repair_proposal"]["repair_kind"] == "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT"
    assert second["repair_proposal"]["repair_kind"] == "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION"


def test_unknown_json_callsite_does_not_receive_wrong_classifier_repair(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )

    result = service.ingest_failure({
        "status": "ERROR", "cycle": "v2-unknown", "error_type": "ValueError",
        "message": "backend did not return a JSON object",
        "failure_site": "content_writer.py:compose",
    }, now=NOW)

    assert result["problem_status"] == "DIAGNOSING"
    assert result["repair_proposal"] is None


def test_reindex_legacy_problem_splits_different_callsites_and_quarantines_aggregate(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    legacy = tmp_path / "application/self_maintenance/problems/legacy.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:legacy",
        "status": "REPAIR_PROPOSED",
        "symptom": "backend did not return a JSON object",
        "error_type": "ValueError",
        "occurrences": 2,
        "cycles": ["v2-filter", "v2-select"],
        "repair_proposal": {"repair_kind": "OLD_WRONG_REPAIR"},
    }))
    for cycle in ("v2-filter", "v2-select"):
        result = tmp_path / "content/continuous/cycles" / cycle / "result.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({
            "status": "ERROR",
            "cycle": cycle,
            "error_type": "ValueError",
            "message": "backend did not return a JSON object",
        }))
    _diagnostic(tmp_path, "v2-filter", [
        ("content_goal_loop.py", "_run"),
        ("content_explorer.py", "filter_for_goal"),
        ("content_explorer.py", "checked"),
        ("content_service.py", "_extract_json"),
    ])
    _diagnostic(tmp_path, "v2-select", [
        ("content_goal_loop.py", "_run"),
        ("content_loop.py", "_structured"),
        ("content_loop.py", "checked"),
        ("content_service.py", "_extract_json"),
    ])

    result = service.reindex_legacy_problems_by_callsite(now=NOW)

    assert result["legacy_rows"] == 1
    assert result["new_problem_count"] == 2
    updated = json.loads(legacy.read_text())
    assert updated["status"] == "LEGACY_AGGREGATE"
    assert updated["repair_proposal"] is None
    assert len(updated["superseded_by"]) == 2
    review = service.review()
    assert review["open_count"] == 2
    kinds = {
        row["repair_proposal"]["repair_kind"]
        for row in review["problems"]
        if row.get("repair_proposal")
    }
    assert kinds == {
        "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT",
        "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION",
    }


def test_reconcile_legacy_duplicates_archives_only_missing_callsite_copy(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    root = tmp_path / "application/self_maintenance/problems"
    root.mkdir(parents=True)
    canonical = root / "canonical.json"
    canonical.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:canonical",
        "status": "REPAIR_PROPOSED",
        "symptom": "BELIEF_CHANGED requires a persisted worldview change",
        "error_type": "ValueError",
        "failure_site": "unknown",
        "occurrences": 2,
        "cycles": ["v2-1", "v2-2"],
        "repair_proposal": {
            "repair_kind": "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW",
        },
    }))
    legacy = root / "legacy.json"
    legacy.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:legacy",
        "status": "REPAIR_PROPOSED",
        "symptom": "BELIEF_CHANGED requires a persisted worldview change",
        "error_type": "ValueError",
        "occurrences": 2,
        "cycles": ["v2-1", "v2-2"],
        "repair_proposal": {
            "repair_kind": "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW",
        },
    }))

    result = service.reconcile_legacy_duplicates(now=NOW)

    assert result == {"groups_reconciled": 1, "archived_duplicates": 1}
    archived = json.loads(legacy.read_text())
    assert archived["status"] == "LEGACY_DUPLICATE"
    assert archived["repair_proposal"] is None
    assert archived["superseded_by"] == ["self-problem:canonical"]
    kept = json.loads(canonical.read_text())
    assert kept["status"] == "REPAIR_PROPOSED"
    assert service.review()["open_count"] == 1


def test_reconcile_does_not_merge_same_message_with_different_cycles(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )
    root = tmp_path / "application/self_maintenance/problems"
    root.mkdir(parents=True)
    (root / "one.json").write_text(json.dumps({
        "problem_id": "self-problem:one", "status": "DIAGNOSING",
        "symptom": "same", "error_type": "ValueError", "failure_site": "unknown",
        "cycles": ["v2-1"],
    }))
    (root / "two.json").write_text(json.dumps({
        "problem_id": "self-problem:two", "status": "DIAGNOSING",
        "symptom": "same", "error_type": "ValueError",
        "cycles": ["v2-2"],
    }))

    result = service.reconcile_legacy_duplicates(now=NOW)

    assert result["archived_duplicates"] == 0
    assert service.review()["open_count"] == 2



def test_stale_model_recovery_input_change_gets_invalidation_repair_proposal(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
    )

    result = service.ingest_failure({
        "status": "ERROR",
        "cycle": "v2-stale",
        "error_type": "ValueError",
        "message": "model phase input changed during recovery",
        "failure_site": "content_goal_loop.py:_run>content_loop.py:_structured>content_loop.py:cognitive_probe",
    }, now=NOW)

    assert result["status"] == "RECORDED"
    assert result["problem_status"] == "REPAIR_PROPOSED"
    assert result["repair_proposal"]["repair_kind"] == "INVALIDATE_STALE_MODEL_RECOVERY_INPUT_CHANGE"
    assert result["repair_proposal"]["target_files"] == [
        "src/wechat_traffic_agent/content_loop.py"
    ]



class RepairCapabilityRuntime:
    def __init__(self):
        self.calls = []

    def available_capabilities(self):
        return {"REQUEST_CODE_REPAIR"}

    def invoke(self, capability, payload):
        self.calls.append((capability, payload))
        return {"status": "RECEIVED", "requestId": "repair-123", "unexpected": {"token": "discard"}}


def test_low_risk_failure_submits_only_stable_repair_request_and_audits_receipt(tmp_path):
    runtime = RepairCapabilityRuntime()
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
        capability_runtime=runtime,
    )

    result = service.ingest_failure(_failure(), now=NOW)

    assert result["repair_request"]["status"] == "RECEIVED"
    assert result["repair_request"]["requestId"] == "repair-123"
    assert len(runtime.calls) == 1
    capability, payload = runtime.calls[0]
    assert capability == "REQUEST_CODE_REPAIR"
    assert payload["risk"] == "LOW"
    assert payload["sourceProblemId"] == result["problem_id"]
    assert "token" not in payload
    stored = loads(next((tmp_path / "application/self_maintenance/problems").glob("*.json")).read_text())
    assert stored["repair_request"]["status"] == "RECEIVED"
    assert len(stored["repair_request"]["request_sha256"]) == 64


def test_missing_repair_capability_fails_closed_without_local_edit_authority(tmp_path):
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
        capability_runtime=None,
    )

    result = service.ingest_failure(_failure(), now=NOW)

    assert result["repair_request"]["status"] == "CAPABILITY_UNAVAILABLE"
    assert "request_id" not in result["repair_request"]


def test_review_retries_prior_capability_unavailable_repair_when_transport_recovers(
    tmp_path,
):
    runtime = RepairCapabilityRuntime()
    service = SelfMaintenanceService(
        state_root=tmp_path,
        evolution_runtime=EvolutionRuntime(),
        capability_runtime=runtime,
    )
    problem = tmp_path / "application/self_maintenance/problems/retryable.json"
    problem.parent.mkdir(parents=True)
    problem.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:retryable",
        "status": "REPAIR_PROPOSED",
        "symptom": "backend did not return a JSON object",
        "error_type": "ValueError",
        "failure_site": "content_goal_loop.py:_run>content_service.py:compose",
        "last_seen_at": NOW.isoformat(),
        "cycles": ["v2-compose-retry"],
        "repair_proposal": {
            "proposal_id": "repair-proposal:retryable",
            "status": "PROPOSED",
            "repair_kind": "USE_STRUCTURED_OUTPUT_FOR_CONTENT_COMPOSITION",
            "target_files": ["src/wechat_traffic_agent/content_service.py"],
            "verification": ["structured composition"],
            "risk": "LOW",
            "reversible": True,
            "source_cycle": "v2-compose-retry",
        },
        "repair_request": {
            "status": "CAPABILITY_UNAVAILABLE",
            "requested_at": (NOW - timedelta(minutes=5)).isoformat(),
        },
    }))

    review = service.review()

    assert review["repair_requested_count"] == 1
    assert review["proposal_refresh"]["repair_candidate_count"] == 1
    stored = json.loads(problem.read_text())
    assert stored["status"] == "REPAIR_REQUESTED"
    assert stored["repair_request"]["status"] == "RECEIVED"
    assert stored["repair_request"]["requestId"] == "repair-123"
    assert len(runtime.calls) == 1


def test_non_content_task_failure_enters_durable_maintenance_without_unsafe_repair(tmp_path):
    service = SelfMaintenanceService(state_root=tmp_path, evolution_runtime=EvolutionRuntime())
    recorded = service.ingest_task_outcome("NEURAL_PLAY", {
        "status": "DEGRADED", "brain_lab": {"status": "ERROR"},
    }, now=NOW)
    assert recorded["status"] == "RECORDED"
    assert recorded["repair_proposal"] is None
    latest = json.loads((tmp_path / "application" / "self_maintenance" / "latest.json").read_text())
    assert latest["task"] == "NEURAL_PLAY"
    assert latest["affected_capability"] == "wakeful.neural_play"
    assert latest["status"] == "DIAGNOSING"


def test_same_task_success_reconciles_older_problem_and_recurrence_reopens_it(tmp_path):
    service = SelfMaintenanceService(state_root=tmp_path, evolution_runtime=EvolutionRuntime())
    failed = service.ingest_task_outcome(
        "NEURAL_PLAY",
        {"status": "DEGRADED", "brain_lab": {"status": "ERROR"}},
        now=NOW,
    )
    assert failed["status"] == "RECORDED"
    assert service.review()["open_count"] == 1

    recovered = service.ingest_task_outcome(
        "NEURAL_PLAY",
        {"status": "OK", "cycle": "neural-play-success"},
        now=NOW + timedelta(minutes=1),
    )
    assert recovered["status"] == "RECOVERY_RECORDED"
    assert recovered["recovered_count"] == 1
    assert recovered["recovered_problem_ids"] == [failed["problem_id"]]
    stored = json.loads(
        next((tmp_path / "application/self_maintenance/problems").glob("*.json")).read_text()
    )
    assert stored["status"] == "RECOVERED_VERIFIED"
    assert stored["recovery_result_status"] == "OK"
    assert recovered["evidence_ref"] in stored["recovery_evidence_refs"]
    assert service.review()["open_count"] == 0

    recurring = service.ingest_task_outcome(
        "NEURAL_PLAY",
        {"status": "DEGRADED", "brain_lab": {"status": "ERROR"}},
        now=NOW + timedelta(minutes=2),
    )
    assert recurring["status"] == "RECORDED"
    assert recurring["problem_id"] == failed["problem_id"]
    assert recurring["problem_status"] == "DIAGNOSING"
    assert service.review()["open_count"] == 1


def test_success_from_other_task_does_not_close_problem(tmp_path):
    service = SelfMaintenanceService(state_root=tmp_path, evolution_runtime=EvolutionRuntime())
    service.ingest_task_outcome(
        "NEURAL_PLAY",
        {"status": "DEGRADED", "brain_lab": {"status": "ERROR"}},
        now=NOW,
    )
    outcome = service.ingest_task_outcome(
        "REFLECT",
        {"status": "OK", "cycle": "reflect-success"},
        now=NOW + timedelta(minutes=1),
    )
    assert outcome["status"] == "RECOVERY_RECORDED"
    assert outcome["recovered_count"] == 0
    assert service.review()["open_count"] == 1


def test_deferred_task_outcome_is_not_recorded_as_recovery(tmp_path):
    service = SelfMaintenanceService(state_root=tmp_path, evolution_runtime=EvolutionRuntime())
    service.ingest_task_outcome(
        "CONTENT_COGNITION",
        {"status": "ERROR", "error_type": "RuntimeError", "cycle": "content-fail"},
        now=NOW,
    )
    outcome = service.ingest_task_outcome(
        "CONTENT_COGNITION",
        {"status": "RESOURCE_DEFERRED", "cycle": "content-deferred"},
        now=NOW + timedelta(minutes=1),
    )
    assert outcome["status"] == "IGNORED_NON_ERROR"
    assert service.review()["open_count"] == 1
