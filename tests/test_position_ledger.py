from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wechat_traffic_agent.position_ledger import PositionLedger

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _ledger(tmp_path: Path) -> PositionLedger:
    return PositionLedger(tmp_path / "content_mind")


def test_register_creates_open_position(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="sol-staking", proposition="质押流入翻倍将持续",
                          falsifier="流入回落到升级前水平", evidence_refs=("web:s1",),
                          draft_id="d1", now=NOW)
    assert row["status"] == "OPEN"
    assert row["position_id"].startswith("pos-")
    assert row["post_ids"] == ["d1"]
    assert ledger.open_positions(now=NOW)[0]["position_id"] == row["position_id"]


def test_confirm_and_refute_transitions(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f",
                          evidence_refs=(), draft_id="d1", now=NOW)
    pid = row["position_id"]
    confirmed = ledger.transition(pid, verdict="CONFIRMED", resolution="结算数据符合",
                                  evidence_refs=("web:e2",), draft_id="d2", now=NOW)
    assert confirmed["status"] == "CONFIRMED"
    with pytest.raises(ValueError):
        ledger.transition(pid, verdict="REFUTED", resolution="再改", evidence_refs=(), draft_id="d3", now=NOW)
    row2 = ledger.register(topic="t2", proposition="p2", falsifier="f2",
                           evidence_refs=(), draft_id="d4", now=NOW)
    refuted = ledger.transition(row2["position_id"], verdict="REFUTED", resolution="错在忽略了 X",
                                evidence_refs=(), draft_id="d5", now=NOW)
    assert refuted["status"] == "REFUTED"
    assert refuted["resolution"] == "错在忽略了 X"
    with pytest.raises(ValueError):
        ledger.transition(row2["position_id"], verdict="CONFIRMED", resolution="",
                          evidence_refs=(), draft_id="d6", now=NOW)


def test_transition_requires_open_position_and_resolution_for_refuted(tmp_path: Path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.transition("pos-missing", verdict="CONFIRMED", resolution="x",
                          evidence_refs=(), draft_id="d", now=NOW)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    with pytest.raises(ValueError):
        ledger.transition(row["position_id"], verdict="REFUTED", resolution="  ",
                          evidence_refs=(), draft_id="d", now=NOW)


def test_open_positions_sweeps_expired_after_five_days(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    later = NOW + timedelta(days=6)
    open_rows = ledger.open_positions(now=later)
    assert open_rows == []
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "ABANDONED"
    assert "5 days" in stored["resolution"]
    # 已放弃的不能再转移
    with pytest.raises(ValueError):
        ledger.transition(stored["position_id"], verdict="CONFIRMED", resolution="x",
                          evidence_refs=(), draft_id="d", now=later)


def test_recent_resolutions_returns_terminal_within_window(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    ledger.transition(row["position_id"], verdict="REFUTED", resolution="wrong about x",
                      evidence_refs=(), draft_id="d", now=NOW)
    assert len(ledger.recent_resolutions(now=NOW)) == 1
    assert ledger.recent_resolutions(now=NOW + timedelta(days=8)) == []


def test_corrupt_and_malformed_entries_are_skipped(tmp_path: Path):
    root = tmp_path / "content_mind"
    root.mkdir(parents=True)
    (root / "positions.json").write_text(json.dumps({
        "positions": [
            {"position_id": "pos-broken"},                       # 缺字段
            "not-a-dict",                                        # 类型错
            {"position_id": "pos-ok", "topic": "t", "proposition": "p", "falsifier": "f",
             "status": "OPEN", "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "evidence_refs": [], "post_ids": [], "resolution": None},
        ]
    }), encoding="utf-8")
    ledger = PositionLedger(root)
    rows = ledger.open_positions(now=NOW)
    assert [r["position_id"] for r in rows] == ["pos-ok"]
    ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                    draft_id="d", now=NOW)  # 原子写不因坏行失败
