from datetime import datetime, timezone
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
