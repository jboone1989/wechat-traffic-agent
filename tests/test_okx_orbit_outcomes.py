import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore
from wechat_traffic_agent.okx_orbit import _draft_sha256
from wechat_traffic_agent.okx_orbit_outcomes import (
    MAX_CAPTURE_LAG,
    OKXOrbitOutcomeCollector,
    OrbitPublicMetrics,
    SOURCE,
    _count_from_action_text,
    _parse_compact_count,
)

NOW = datetime(2026, 9, 17, 12, 5, tzinfo=timezone.utc)
PUBLISHED = NOW - timedelta(hours=24, minutes=5)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _state(tmp_path: Path, *, published_at: datetime = PUBLISHED) -> tuple[Path, str]:
    root = tmp_path / "state"
    cycle = "v2-okx-outcome-1"
    folder = root / "content/continuous/cycles" / cycle
    draft = {
        "channel": "OKX",
        "format": "SHORT_POST",
        "intent": "QUESTION",
        "text": "BTC moved while market structure stayed ambiguous. Which public evidence separates flow from broad risk-off?",
    }
    _write(folder / "okx_orbit_publication.json", {
        "status": "POSTED",
        "cycle": cycle,
        "published_at": published_at.isoformat(),
        "post_id": "87555650159168",
        "url": "https://www.okx.com/orbit/post/87555650159168",
        "draft_sha256": _draft_sha256(cycle=cycle, draft=draft),
    })
    _write(folder / "social_expression_drafts.json", {
        "status": "DRAFT_READY",
        "cycle": cycle,
        "drafts": {"okx": draft},
        "evidence_decision": {"resolution": {"action": "QUESTION"}},
    })
    _write(folder / "publication.json", {
        "artifact_id": "continuous-test-artifact",
        "outcome_attribution": {"topic": "crypto-market"},
    })
    return root, cycle


class _Transport:
    def __init__(self, metrics: OrbitPublicMetrics) -> None:
        self.metrics = metrics
        self.calls: list[str] = []

    async def collect(self, *, url: str) -> OrbitPublicMetrics:
        self.calls.append(url)
        return self.metrics


def test_parse_compact_public_counts():
    assert _parse_compact_count("50.78K") == 50_780
    assert _parse_compact_count("1.2M") == 1_200_000
    assert _parse_compact_count("6") == 6


def test_parse_semantic_action_container_counts():
    assert _count_from_action_text("3") == 3
    assert _count_from_action_text("0") == 0
    assert _count_from_action_text("6") == 6
    assert _count_from_action_text(" 1.2K ") == 1200
    assert _count_from_action_text("") is None


def test_collect_records_four_public_metrics_only_near_mature_window(tmp_path):
    root, _cycle = _state(tmp_path)
    transport = _Transport(OrbitPublicMetrics(views=50_780, comments=3, quotes=0, likes=6))
    collector = OKXOrbitOutcomeCollector(state_root=root, transport=transport)

    result = asyncio.run(collector.collect(now=NOW))

    assert result["status"] == "RECORDED"
    assert result["learning_authority"] is False
    assert result["publication_authority"] is False
    assert result["causal_claim"] is False
    assert len(result["recorded"]) == 4
    assert result["pending"] == 2
    assert len(transport.calls) == 1
    rows = ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW)
    assert {row.metric: row.value for row in rows} == {
        "views": 50780.0, "comments": 3.0, "quotes": 0.0, "likes": 6.0,
    }
    assert all(row.channel == "okx-orbit" for row in rows)
    assert all(row.source == SOURCE for row in rows)
    assert all(row.agent_candidate_sha256 == row.published_content_sha256 for row in rows)


def test_missed_window_is_not_backfilled_with_current_cumulative_counts(tmp_path):
    published_at = NOW - timedelta(hours=24) - MAX_CAPTURE_LAG - timedelta(seconds=1)
    root, _cycle = _state(tmp_path, published_at=published_at)
    transport = _Transport(OrbitPublicMetrics(views=999_999, comments=999, quotes=99, likes=999))
    collector = OKXOrbitOutcomeCollector(state_root=root, transport=transport)

    result = asyncio.run(collector.collect(now=NOW))

    assert result["status"] == "MISSED_WINDOW"
    assert result["recorded"] == []
    assert result["missed"] == [{"publication_id": "okx-orbit:87555650159168", "window": "24h"}]
    assert transport.calls == []
    assert ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW) == []


def test_collection_is_idempotent_per_post_window_metric(tmp_path):
    root, _cycle = _state(tmp_path)
    transport = _Transport(OrbitPublicMetrics(views=1200, comments=1, quotes=0, likes=4))
    collector = OKXOrbitOutcomeCollector(state_root=root, transport=transport)

    first = asyncio.run(collector.collect(now=NOW))
    second = asyncio.run(collector.collect(now=NOW))

    assert first["status"] == "RECORDED"
    assert second["status"] == "NO_MATURE_WINDOWS"
    assert len(transport.calls) == 1
    assert len(ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW)) == 4



def test_changed_persisted_draft_cannot_inherit_published_post_outcomes(tmp_path):
    root, cycle = _state(tmp_path)
    path = root / "content/continuous/cycles" / cycle / "social_expression_drafts.json"
    payload = json.loads(path.read_text())
    payload["drafts"]["okx"]["text"] = "This text was changed after publication and must not inherit the original post outcomes."
    _write(path, payload)
    transport = _Transport(OrbitPublicMetrics(views=100, comments=1, quotes=0, likes=2))
    collector = OKXOrbitOutcomeCollector(state_root=root, transport=transport)

    with pytest.raises(ValueError, match="draft binding mismatch"):
        asyncio.run(collector.collect(now=NOW))
    assert transport.calls == []


def test_receipt_url_must_match_recorded_post_id(tmp_path):
    root, cycle = _state(tmp_path)
    path = root / "content/continuous/cycles" / cycle / "okx_orbit_publication.json"
    payload = json.loads(path.read_text())
    payload["post_id"] = "99999999999999"
    _write(path, payload)
    collector = OKXOrbitOutcomeCollector(
        state_root=root, transport=_Transport(OrbitPublicMetrics(views=1, comments=0, quotes=0, likes=0))
    )
    with pytest.raises(ValueError, match="URL/post id mismatch"):
        asyncio.run(collector.collect(now=NOW))

def test_no_okx_publication_means_no_outcome_authority(tmp_path):
    collector = OKXOrbitOutcomeCollector(
        state_root=tmp_path,
        transport=_Transport(OrbitPublicMetrics(views=1, comments=0, quotes=0, likes=0)),
    )
    result = asyncio.run(collector.collect(now=NOW))
    assert result == {
        "status": "NO_ELIGIBLE_PUBLICATIONS",
        "source": SOURCE,
        "publication_authority": False,
        "learning_authority": False,
        "causal_claim": False,
    }
