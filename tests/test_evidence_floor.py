from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.content_artifact import ContentArtifact, ContentKind, ContentSection
from wechat_traffic_agent.evidence_floor import (
    EvidenceFloorCheck,
    EvidenceFloorPlan,
    EvidenceFloorSource,
    artifact_risk_cues,
    evaluate_evidence_floor,
    verify_evidence_floor_publication,
)

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def artifact(*, paragraph: str, title: str = "A bounded systems explanation") -> ContentArtifact:
    return ContentArtifact(
        artifact_id="draft-1",
        kind=ContentKind.ARTICLE,
        slug="bounded-systems-explanation",
        title=title,
        summary="A careful explanation grounded in supplied evidence.",
        sections=(ContentSection(heading="Analysis", paragraphs=(paragraph,)),),
        tags=("rare",),
        created_at=NOW,
    )


def counter_source() -> EvidenceFloorSource:
    return EvidenceFloorSource(
        evidence_id="web:counter-1",
        source_kind="web-search",
        source="runtime-web-search+jina-reader",
        title="Observed limitations in live deployment",
        summary="Live results can diverge from backtests because costs, latency, and regime changes can erase an apparent edge.",
        url="https://example.org/counter",
        observed_at=NOW.isoformat(),
        source_revision="counter-1",
        evidence_refs=("runtime-receipt:counter-1",),
        search_query="strategy limitations risks failure cases",
        search_rank=1,
    )


def satisfied_plan() -> EvidenceFloorPlan:
    source = counter_source()
    return EvidenceFloorPlan(
        topic="backtesting",
        required=True,
        trigger_cues=("topic:backtesting",),
        status="SATISFIED",
        counter_query="strategy limitations risks failure cases",
        counter_evidence=source,
        counter_match_quote="costs, latency, and regime changes",
        counter_match_reason="The source states a concrete failure mode for apparent backtest edge.",
    )


def test_non_risk_artifact_passes_without_counter_evidence():
    draft = artifact(paragraph="The report describes a bounded observation and separates interpretation from evidence.")
    plan = EvidenceFloorPlan(topic="rare", required=False, trigger_cues=(), status="NOT_REQUIRED")
    check = evaluate_evidence_floor(artifact=draft, plan=plan, supports=[])
    assert check.status == "PASS"
    assert check.required is False
    assert check.independence_required is False
    assert check.independence_checked is False


def test_final_high_risk_claim_fails_even_when_plan_was_not_required():
    draft = artifact(
        title="Why this Bitcoin strategy may outperform",
        paragraph="This Bitcoin strategy may outperform after the observed market change.",
    )
    plan = EvidenceFloorPlan(topic="rare", required=False, trigger_cues=(), status="NOT_REQUIRED")
    assert artifact_risk_cues(draft, topic="rare")
    check = evaluate_evidence_floor(artifact=draft, plan=plan, supports=[])
    assert check.status == "FAIL"
    assert check.failure_reason == "COUNTER_EVIDENCE_NOT_ACQUIRED"


def test_counter_paragraph_without_exact_counter_source_support_fails():
    draft = artifact(
        title="Backtesting risk needs contrary evidence",
        paragraph="Counter-evidence: live trading can differ materially from a backtest.",
    )
    check = evaluate_evidence_floor(
        artifact=draft,
        plan=satisfied_plan(),
        supports=[{
            "section_index": 0,
            "source_id": "some-other-source",
            "source_quote": "unrelated exact quote",
        }],
    )
    assert check.status == "FAIL"
    assert check.failure_reason == "COUNTER_EVIDENCE_EXACT_SUPPORT_MISSING"


def test_exact_counter_source_support_passes_without_independence_claim():
    draft = artifact(
        title="Backtesting risk needs contrary evidence",
        paragraph="Counter-evidence: live results may diverge when trading costs and regimes change.",
    )
    check = evaluate_evidence_floor(
        artifact=draft,
        plan=satisfied_plan(),
        supports=[{
            "section_index": 0,
            "source_id": "web:counter-1",
            "source_quote": "costs, latency, and regime changes can erase an apparent edge",
        }],
    )
    assert check.status == "PASS"
    assert check.counter_section_index == 0
    assert check.independence_required is False
    verify_evidence_floor_publication(
        artifact=draft, plan=satisfied_plan(), check=check,
        supports=[{
            "section_index": 0,
            "source_id": "web:counter-1",
            "source_quote": "costs, latency, and regime changes can erase an apparent edge",
        }],
    )


def test_publication_revalidation_rejects_tampered_passing_check():
    draft = artifact(
        title="Backtesting risk needs contrary evidence",
        paragraph="Counter-evidence: live results may diverge when trading costs and regimes change.",
    )
    forged = EvidenceFloorCheck(
        required=True,
        status="PASS",
        trigger_cues=("topic:backtesting",),
        counter_section_index=0,
        counter_source_id="web:counter-1",
        counter_source_quote="Observed limitations in live deployment",
    )
    with pytest.raises(ValueError, match="actual Evidence Floor compliance"):
        verify_evidence_floor_publication(
            artifact=draft, plan=satisfied_plan(), check=forged,
            supports=[{
                "section_index": 0,
                "source_id": "web:counter-1",
                "source_quote": "costs, latency, and regime changes can erase an apparent edge",
            }],
        )


def test_explicit_dated_forecast_is_opinion_not_performance_claim():
    draft = artifact(
        title="My 24-hour Bitcoin liquidity view",
        paragraph="I forecast that Bitcoin liquidity will remain tight over the next 24 hours; this is a 62% confidence prediction, not an observed fact or investment recommendation.",
    )
    plan = EvidenceFloorPlan(topic="prediction", required=False, trigger_cues=(), status="NOT_REQUIRED")
    assert artifact_risk_cues(draft, topic="prediction") == ()
    check = evaluate_evidence_floor(artifact=draft, plan=plan, supports=[])
    assert check.status == "PASS"
    assert check.required is False


def test_sharper_word_does_not_trigger_sharpe_ratio_floor():
    draft = artifact(
        title="A sharper way to frame one market observation",
        paragraph="The evidence makes a sharper point about uncertainty without making a performance claim.",
    )
    assert artifact_risk_cues(draft, topic="crypto-market") == ()


def test_actual_sharpe_term_still_triggers_floor():
    draft = artifact(
        title="A crypto Sharpe comparison",
        paragraph="The strategy reports a higher Sharpe ratio in this market sample.",
    )
    cues = artifact_risk_cues(draft, topic="crypto-market")
    assert "claim:sharpe" in cues
