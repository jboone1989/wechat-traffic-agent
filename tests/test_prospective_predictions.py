from datetime import datetime, timedelta, timezone

import pytest

from wechat_traffic_agent.prospective_predictions import PredictionEvidence, ProspectivePredictionLedger

NOW = datetime(2026, 9, 16, 3, tzinfo=timezone.utc)


def _record(ledger: ProspectivePredictionLedger):
    return ledger.record(
        cycle="v2-1", topic="rates-regime",
        proposition="Persistently higher yields tighten crypto financial conditions.",
        proposition_stance=0.3,
        statement="By tomorrow, crypto funding conditions will remain tighter than the prior day.",
        confidence=0.7, created_at=NOW, due_at=NOW + timedelta(days=1),
        verification_query="crypto funding conditions September 17 2026",
        falsifier="Funding conditions materially ease before the horizon.",
        source_evidence_refs=("world-evidence:1",),
    )


def test_prediction_is_restart_stable_and_not_due_early(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    assert ledger.due(NOW + timedelta(hours=23)) == ()
    restarted = ProspectivePredictionLedger(tmp_path)
    assert restarted.open()[0].prediction_id == row.prediction_id
    assert restarted.due(NOW + timedelta(days=1))[0].statement == row.statement
    assert _record(restarted).prediction_id == row.prediction_id


def test_outcome_rejects_hindsight_evidence_and_scores_prediction_error(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    with pytest.raises(ValueError, match="prospective time boundary"):
        ledger.check(row.prediction_id, checked_at=NOW + timedelta(days=1, hours=1), status="CONFIRMED",
            evidence=(PredictionEvidence(evidence_id="old", evidence_quote="Old evidence existed before the prediction.",
                observed_at=NOW - timedelta(minutes=1), source="example", title="Old evidence", source_revision="old-rev"),), reliability=0.8, rationale="Old evidence cannot resolve a prospective prediction.")
    resolved = ledger.check(row.prediction_id, checked_at=NOW + timedelta(days=1, hours=1), status="REFUTED",
        evidence=(PredictionEvidence(evidence_id="new", evidence_quote="Funding conditions materially eased after the prediction.",
            observed_at=NOW + timedelta(days=1), source="example", title="Fresh evidence", source_revision="new-rev"),), reliability=0.9,
        rationale="Fresh post-prediction evidence directly falsifies the dated claim.")
    assert resolved.status == "RESOLVED"
    assert resolved.outcome.status == "REFUTED"
    assert resolved.outcome.prediction_error == pytest.approx(0.7)
    assert ProspectivePredictionLedger(tmp_path).resolved()[0].prediction_id == row.prediction_id


def test_inconclusive_check_keeps_prediction_open(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    checked = ledger.check(row.prediction_id, checked_at=NOW + timedelta(days=1, hours=1), status="INCONCLUSIVE",
        evidence=(PredictionEvidence(evidence_id="new", evidence_quote="The available data remains mixed at the horizon.",
            observed_at=NOW + timedelta(days=1), source="example", title="Fresh evidence", source_revision="new-rev"),), reliability=0.4,
        rationale="The new evidence is real but does not distinguish confirmation from refutation.")
    assert checked.status == "OPEN"
    assert checked.checks[-1].prediction_error is None
    assert ledger.due(NOW + timedelta(days=2))[0].prediction_id == row.prediction_id

import asyncio
import json

from wechat_traffic_agent.prospective_predictions import assess_prediction, settle_predictions


class AssessmentRuntime:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        return json.dumps(self.response)


def test_assessment_uses_only_post_prediction_evidence_and_exact_quotes(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    runtime = AssessmentRuntime({
        "status": "REFUTED",
        "evidence": [{"evidence_id": "fresh", "evidence_quote": "Funding conditions materially eased"}],
        "reliability": 0.85,
        "rationale": "The post-prediction evidence directly matches the registered falsifier.",
    })
    assessment, evidence = asyncio.run(assess_prediction(
        runtime=runtime, prediction=row, now=NOW + timedelta(days=1, hours=1), items=[
            {"evidence_id": "old", "source": "old-source", "title": "Old story",
             "summary": "Funding conditions materially eased before the prediction.",
             "published_at": (NOW - timedelta(days=1)).isoformat()},
            {"evidence_id": "fresh", "source": "fresh-source", "title": "Fresh story",
             "summary": "Funding conditions materially eased after the prediction horizon.",
             "published_at": (NOW + timedelta(days=1)).isoformat()},
        ]))
    assert assessment.status == "REFUTED"
    assert [row.evidence_id for row in evidence] == ["fresh"]
    assert '"evidence_id": "old"' not in runtime.calls[0]


def test_assessment_without_post_prediction_evidence_is_inconclusive_without_model_call(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    runtime = AssessmentRuntime({})
    assessment, evidence = asyncio.run(assess_prediction(
        runtime=runtime, prediction=row, now=NOW + timedelta(days=1), items=[
            {"evidence_id": "old", "source": "old-source", "title": "Old story", "summary": "Old evidence text.",
             "published_at": (NOW - timedelta(minutes=1)).isoformat()},
        ]))
    assert assessment.status == "INCONCLUSIVE"
    assert evidence == ()
    assert runtime.calls == []


def test_web_discovery_time_cannot_substitute_for_post_prediction_publication_time(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    runtime = AssessmentRuntime({
        "status": "CONFIRMED", "evidence": [], "reliability": 1.0,
        "rationale": "This response must never be used because the candidate lacks a prospective publication time.",
    })
    assessment, evidence = asyncio.run(assess_prediction(
        runtime=runtime, prediction=row, now=NOW + timedelta(days=1, hours=1), items=[{
            "evidence_id": "web:old-undated", "source_kind": "web-search", "source": "runtime:web.fetch",
            "title": "Undated page discovered later", "summary": "The page may have existed before the prediction.",
            "source_revision": "undated-rev", "observed_at": (NOW + timedelta(days=1)).isoformat(),
        }]))
    assert assessment.status == "INCONCLUSIVE"
    assert evidence == ()
    assert runtime.calls == []


def test_check_history_rolls_oldest_entry_into_durable_archive_at_cap(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    due = NOW + timedelta(days=1)
    first_check_id = None
    for index in range(33):
        updated = ledger.check(
            row.prediction_id,
            checked_at=due + timedelta(minutes=index + 1),
            status="INCONCLUSIVE",
            evidence=(),
            reliability=0.0,
            rationale="No decisive post-prediction evidence is available yet.",
        )
        if index == 0:
            first_check_id = updated.checks[-1].check_id

    assert len(updated.checks) == 32
    assert first_check_id not in {item.check_id for item in updated.checks}
    archived = list(
        (tmp_path / "content" / "predictions" / "check_archive").glob("*/*.json")
    )
    assert len(archived) == 1
    payload = json.loads(archived[0].read_text(encoding="utf-8"))
    assert payload["prediction_id"] == row.prediction_id
    assert payload["check"]["check_id"] == first_check_id
    restarted = ProspectivePredictionLedger(tmp_path)
    assert len(restarted.open()[0].checks) == 32


class FailingAssessmentRuntime:
    async def cognitive_probe(self, task, max_tokens):
        raise RuntimeError("provider unavailable")


def test_settlement_error_records_durable_verification_retry(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    attempted = NOW + timedelta(days=1, hours=1)
    result = asyncio.run(settle_predictions(
        ledger=ledger,
        runtime=FailingAssessmentRuntime(),
        items=[{
            "evidence_id": "fresh",
            "source": "fresh-source",
            "title": "Fresh post-horizon evidence",
            "summary": "Fresh evidence that requires model assessment.",
            "published_at": (NOW + timedelta(days=1)).isoformat(),
        }],
        now=attempted,
        mind=None,
        cycle="wakeful-prediction-test",
    ))

    assert result[0]["check_status"] == "ERROR"
    assert result[0]["error_type"] == "RuntimeError"
    assert ledger.verification_retry_after(row.prediction_id) == attempted + timedelta(hours=1)
    retry_files = list(
        (tmp_path / "content" / "predictions" / "verification_retries").glob("*.json")
    )
    assert len(retry_files) == 1


def test_policy_defer_preserves_error_count_and_durably_moves_retry(tmp_path):
    ledger = ProspectivePredictionLedger(tmp_path)
    row = _record(ledger)
    deferred = NOW + timedelta(days=1, hours=2)

    payload = ledger.defer_verification(
        row.prediction_id,
        deferred_at=deferred,
        delay=timedelta(minutes=45),
        reason="OWNER_MISSION_PRIORITY",
    )

    assert payload["error_type"] is None
    assert payload["consecutive_errors"] == 0
    assert payload["deferred_reason"] == "OWNER_MISSION_PRIORITY"
    assert ledger.verification_retry_after(row.prediction_id) == (
        deferred + timedelta(minutes=45)
    )

    restarted = ProspectivePredictionLedger(tmp_path)
    assert restarted.verification_retry_after(row.prediction_id) == (
        deferred + timedelta(minutes=45)
    )
