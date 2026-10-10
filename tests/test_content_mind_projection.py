from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_traffic_agent.content_mind import ContentMind

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _mind(tmp_path: Path) -> ContentMind:
    return ContentMind(root=tmp_path / "state", subject_id="content-agent")


def test_projection_includes_positions_block(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.positions.register(topic="sol-staking", proposition="p", falsifier="f",
                            evidence_refs=(), draft_id="d1", now=NOW)
    projection = mind.public_projection()
    assert projection["positions"]["open"][0]["topic"] == "sol-staking"
    assert projection["positions"]["recent_resolutions"] == []


def test_projection_rows_carry_decayed_salience(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.observe_learning_interest(cycle="c1", topic="fresh",
                                   reward={"utility": 1.0, "kind": "research"},
                                   evidence_refs=("r",))
    projection = mind.public_projection(now=NOW)
    rows = {row["name"]: row for row in projection["interests"]}
    assert "decayed_salience" in rows["fresh"]
    assert 0.0 <= rows["fresh"]["decayed_salience"] <= rows["fresh"]["salience"]


def test_decay_math_half_life():
    # 纯函数直接测，不依赖 VirtualBrain interests history 的 observed_at
    from wechat_traffic_agent.content_mind import _decayed_salience
    fresh = NOW - timedelta(minutes=10)
    assert _decayed_salience(1.0, fresh.isoformat(), NOW) >= 0.99
    assert _decayed_salience(1.0, (NOW - timedelta(hours=48)).isoformat(), NOW) <= 0.51
    assert _decayed_salience(1.0, (NOW - timedelta(hours=96)).isoformat(), NOW) <= 0.26
    assert _decayed_salience(0.8, None, NOW) == 0.8          # 无历史不衰减
    assert _decayed_salience(0.8, "not-a-date", NOW) == 0.8  # 坏时间戳不衰减
    assert _decayed_salience(1.0, (NOW + timedelta(hours=5)).isoformat(), NOW) == 1.0  # 未来时间戳钳制


def test_missing_history_no_decay(tmp_path: Path):
    mind = _mind(tmp_path)
    mind.observe_learning_interest(cycle="c1", topic="t1",
                                   reward={"utility": 0.3, "kind": "research"},
                                   evidence_refs=("r",))
    projection = mind.public_projection(now=NOW)
    for row in projection["interests"]:
        if row.get("last_observed_at") is None:
            assert row["decayed_salience"] == row["salience"]
