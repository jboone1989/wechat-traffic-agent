import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore
from wechat_traffic_agent.x_browser import _draft_sha256
from wechat_traffic_agent.x_outcomes import (
    MAX_CAPTURE_LAG, SOURCE, XOutcomeCollector, XPublicMetrics,
    _parse_compact_count, _published_at_from_status_id,
)

NOW = datetime(2026, 9, 20, 12, 5, tzinfo=timezone.utc)
X_EPOCH_MS = 1_288_834_974_657

def _snowflake(when: datetime) -> str:
    ms = int(when.timestamp() * 1000)
    return str((ms - X_EPOCH_MS) << 22)

def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

def _state(tmp_path: Path, *, actual_published_at: datetime | None = None) -> tuple[Path, str, str]:
    root = tmp_path / "state"
    cycle = "v2-x-outcome-1"
    folder = root / "content/continuous/cycles" / cycle
    actual = actual_published_at or (NOW - timedelta(hours=24, minutes=5))
    post_id = _snowflake(actual)
    draft = {
        "channel": "X", "format": "LONG_POST", "intent": "QUESTION",
        "text": "One fixed-age X post can teach expression strategy only. What presentation earns attention without changing factual certainty?",
    }
    _write(folder / "x_browser_publication.json", {
        "status": "POSTED", "cycle": cycle,
        "published_at": (actual + timedelta(hours=1)).isoformat(),
        "post_id": post_id, "url": f"https://x.com/agent/status/{post_id}", "handle": "agent",
        "draft_sha256": _draft_sha256(cycle=cycle, draft=draft),
    })
    _write(folder / "social_expression_drafts.json", {
        "status": "DRAFT_READY", "cycle": cycle, "drafts": {"x": draft},
        "evidence_decision": {"resolution": {"action": "QUESTION"}},
    })
    _write(folder / "publication.json", {
        "artifact_id": "continuous-x-artifact", "outcome_attribution": {"topic": "crypto-market"},
    })
    return root, cycle, post_id

class _Transport:
    def __init__(self, metrics: XPublicMetrics) -> None:
        self.metrics = metrics
        self.calls: list[str] = []
    async def collect(self, *, url: str) -> XPublicMetrics:
        self.calls.append(url)
        return self.metrics

def test_parse_x_public_counts_and_snowflake_time():
    assert _parse_compact_count("1.2K Replies") == 1200
    when = datetime(2026, 9, 17, 4, 43, 35, 978000, tzinfo=timezone.utc)
    post_id = _snowflake(when)
    assert abs((_published_at_from_status_id(post_id) - when).total_seconds()) < 0.001

def test_collect_records_four_x_metrics_at_actual_post_age(tmp_path: Path):
    root, _cycle, _post_id = _state(tmp_path)
    transport = _Transport(XPublicMetrics(views=600, replies=4, reposts=2, likes=20))
    result = asyncio.run(XOutcomeCollector(state_root=root, transport=transport).collect(now=NOW))
    assert result["status"] == "RECORDED"
    assert result["learning_authority"] is False
    assert result["publication_authority"] is False
    assert result["causal_claim"] is False
    assert len(result["recorded"]) == 4
    assert result["pending"] == 2
    assert len(transport.calls) == 1
    rows = ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW)
    assert {row.metric: row.value for row in rows} == {
        "views": 600.0, "replies": 4.0, "reposts": 2.0, "likes": 20.0,
    }
    assert all(row.channel == "x" and row.source == SOURCE for row in rows)
    assert all(row.agent_candidate_sha256 == row.published_content_sha256 for row in rows)

def test_receipt_backfill_time_does_not_shift_x_window(tmp_path: Path):
    actual = NOW - timedelta(hours=24, minutes=5)
    root, _cycle, _post_id = _state(tmp_path, actual_published_at=actual)
    result = asyncio.run(XOutcomeCollector(
        state_root=root, transport=_Transport(XPublicMetrics(views=1, replies=0, reposts=0, likes=0))
    ).collect(now=NOW))
    assert result["status"] == "RECORDED"
    row = ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW)[0]
    assert abs((row.published_at - actual).total_seconds()) < 0.001

def test_missed_x_window_is_not_backfilled(tmp_path: Path):
    actual = NOW - timedelta(hours=24) - MAX_CAPTURE_LAG - timedelta(seconds=1)
    root, _cycle, post_id = _state(tmp_path, actual_published_at=actual)
    transport = _Transport(XPublicMetrics(views=99999, replies=99, reposts=99, likes=999))
    result = asyncio.run(XOutcomeCollector(state_root=root, transport=transport).collect(now=NOW))
    assert result["status"] == "MISSED_WINDOW"
    assert result["recorded"] == []
    assert result["missed"] == [{"publication_id": f"x:{post_id}", "window": "24h"}]
    assert transport.calls == []

def test_changed_x_draft_cannot_inherit_post_outcomes(tmp_path: Path):
    root, cycle, _ = _state(tmp_path)
    path = root / "content/continuous/cycles" / cycle / "social_expression_drafts.json"
    payload = json.loads(path.read_text())
    payload["drafts"]["x"]["text"] += " changed"
    _write(path, payload)
    collector = XOutcomeCollector(state_root=root, transport=_Transport(XPublicMetrics(1, 0, 0, 0)))
    with pytest.raises(ValueError, match="draft binding mismatch"):
        asyncio.run(collector.collect(now=NOW))

def test_x_receipt_url_must_match_post_id(tmp_path: Path):
    root, cycle, _ = _state(tmp_path)
    path = root / "content/continuous/cycles" / cycle / "x_browser_publication.json"
    payload = json.loads(path.read_text())
    payload["post_id"] = _snowflake(NOW - timedelta(hours=24, minutes=6))
    _write(path, payload)
    with pytest.raises(ValueError, match="URL/post id mismatch"):
        asyncio.run(XOutcomeCollector(state_root=root, transport=_Transport(XPublicMetrics(1, 0, 0, 0))).collect(now=NOW))

def test_no_x_publication_has_no_outcome_authority(tmp_path: Path):
    result = asyncio.run(XOutcomeCollector(
        state_root=tmp_path, transport=_Transport(XPublicMetrics(1, 0, 0, 0))
    ).collect(now=NOW))
    assert result == {
        "status": "NO_ELIGIBLE_PUBLICATIONS", "source": SOURCE,
        "publication_authority": False, "learning_authority": False, "causal_claim": False,
    }


def _omnipost_square_doc(root: Path, *, post_id: str, status: str = "SUCCESS", cycle: str = "v2-owner-syn") -> None:
    from wechat_traffic_agent.editorial.channel_records import ChannelRecords
    _write(root / "content/continuous/cycles" / cycle / "cognition.json", {"topic": "onchain-flows"})
    records = ChannelRecords(root)
    with records.transaction():
        records.put("square-d1", "binance_square", {
            "draft_id": "d1", "status": "SUCCESS", "body": "链上资金流向的观察，正文。$BTC",
            "symbols": ["BTC"], "observation_provenance": {"cycle": cycle},
            "x_syndication": {"status": status, "post_id": post_id,
                              "url": f"https://x.com/agent/status/{post_id}"},
        })


def test_omnipost_cross_posted_x_is_collected_with_cycle_topic(tmp_path: Path):
    root = tmp_path / "state"
    post_id = _snowflake(NOW - timedelta(hours=24, minutes=5))
    _omnipost_square_doc(root, post_id=post_id)
    transport = _Transport(XPublicMetrics(views=40, replies=1, reposts=0, likes=2))
    result = asyncio.run(XOutcomeCollector(state_root=root, transport=transport).collect(now=NOW))
    assert result["status"] == "RECORDED"
    assert transport.calls == [f"https://x.com/agent/status/{post_id}"]
    rows = ChannelEvidenceStore(root / "content/channel_evidence").as_of(NOW)
    assert {row.topic for row in rows} == {"onchain-flows"}
    assert {row.structure_version for row in rows} == {"x:OMNIPOST_SYNDICATION:v1"}


def test_unverified_omnipost_x_receipt_is_not_collected(tmp_path: Path):
    root = tmp_path / "state"
    _omnipost_square_doc(root, post_id=_snowflake(NOW - timedelta(hours=24, minutes=5)), status="UNVERIFIED")
    result = asyncio.run(XOutcomeCollector(
        state_root=root, transport=_Transport(XPublicMetrics(views=1, replies=0, reposts=0, likes=0))
    ).collect(now=NOW))
    assert result["status"] == "NO_ELIGIBLE_PUBLICATIONS"
