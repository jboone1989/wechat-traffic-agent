from datetime import datetime, timedelta, timezone
import asyncio
import json

import pytest

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.prospective_predictions import ProspectivePredictionLedger, settle_predictions

NOW = datetime(2026, 9, 16, 3, tzinfo=timezone.utc)

def test_matured_prediction_updates_life_experience_and_same_worldview_claim(tmp_path):
    from wechat_traffic_agent.content_mind import ContentMind
    from wechat_traffic_agent.prospective_predictions import settle_predictions

    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")
    proposition = "Persistently higher yields tighten crypto financial conditions."
    mind.record_research_belief(
        cycle="v2-before", topic="rates-regime", question="Do higher yields tighten conditions?",
        source_evidence={
            "evidence_id": "web:initial", "title": "Yields remained elevated",
            "source_revision": "initial-rev", "source": "initial-source",
            "evidence_refs": ("web:initial",),
        },
        proposition=proposition, stance=0.7, reliability=0.8, support=0.8, commit=True,
    )
    ledger = ProspectivePredictionLedger(root)
    prediction = ledger.record(
        cycle="v2-before", topic="rates-regime", proposition=proposition, proposition_stance=0.7,
        statement="By tomorrow, crypto funding conditions will remain tighter than the prior day.",
        confidence=0.75, created_at=NOW, due_at=NOW + timedelta(days=1),
        verification_query="crypto funding conditions September 17 2026",
        falsifier="Funding conditions materially ease before the horizon.",
        source_evidence_refs=("world-evidence:initial",),
    )

    class Runtime:
        def __init__(self): self.calls = []
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.calls.append(task)
            if task.startswith("EVALUATE_DATED_PREDICTION:"):
                return json.dumps({
                    "status": "REFUTED",
                    "evidence": [{"evidence_id": "web:fresh", "evidence_quote": "Funding conditions materially eased"}],
                    "reliability": 0.9,
                    "rationale": "Fresh evidence directly satisfies the registered falsifier.",
                })
            if task.startswith("REVISE_WORLDVIEW_FROM_PREDICTION:"):
                return json.dumps({
                    "action": "COMMIT", "stance": 0.25, "reliability": 0.9, "support": 0.85,
                    "rationale": "The failed forecast weakens but does not reverse the broader proposition.",
                })
            raise AssertionError(task[:80])

    runtime = Runtime()
    items = [{
        "evidence_id": "web:fresh", "source": "fresh-source", "title": "Funding conditions eased",
        "summary": "Funding conditions materially eased after the prediction horizon.",
        "source_revision": "fresh-rev", "evidence_refs": ["web:fresh"],
        "published_at": (NOW + timedelta(days=1)).isoformat(),
    }]
    results = asyncio.run(settle_predictions(
        ledger=ledger, runtime=runtime, items=items, now=NOW + timedelta(days=1, hours=1),
        mind=mind, cycle="v2-after",
    ))
    assert any(row.get("check_status") == "REFUTED" for row in results)
    applied = next(row for row in results if row.get("learning_status") == "APPLIED")
    assert applied["life_experience"]["prediction_error"] == pytest.approx(0.75)
    assert applied["worldview_revision"]["status"] == "REVISED"
    belief = next(row for row in mind.public_projection()["beliefs"] if row["proposition"] == proposition)
    assert belief["revision_count"] == 2
    assert belief["stance"] != pytest.approx(0.7)
    event = next(row for row in mind.session.snapshot()["autobiography"]["events"]
                 if row["event_id"] == "prediction-outcome:" + prediction.prediction_id)
    assert event["outcome"] == 0.0
    call_count = len(runtime.calls)
    again = asyncio.run(settle_predictions(
        ledger=ledger, runtime=runtime, items=items, now=NOW + timedelta(days=1, hours=2),
        mind=mind, cycle="v2-after-2",
    ))
    assert again == ()
    assert len(runtime.calls) == call_count
    assert next(row for row in mind.public_projection()["beliefs"] if row["proposition"] == proposition)["revision_count"] == 2


