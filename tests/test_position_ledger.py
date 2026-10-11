from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wechat_traffic_agent.position_ledger import PositionLedger, topic_matches

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
    assert row["post_ids"] == [{"id": "d1", "surface": "square"}]
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


def test_write_triggers_sweep_reads_stay_pure(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    later = NOW + timedelta(days=6)
    # 读是纯读：过期 OPEN 原样返回，文件字节不变
    assert [r["position_id"] for r in ledger.open_positions(now=later)] == [row["position_id"]]
    before = ledger.path.read_bytes()
    assert ledger.get(row["position_id"])["status"] == "OPEN"
    assert ledger.recent_resolutions(now=later) == []
    assert ledger.path.read_bytes() == before
    # 写入口触发 sweep：第二次 register 时旧 OPEN 被放弃并持久化
    ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                    draft_id="d2", now=later)
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "ABANDONED"
    assert "5 days" in stored["resolution"]
    assert [r["position_id"] for r in ledger.open_positions(now=later)] != [row["position_id"]]
    # 已放弃的不能再转移
    with pytest.raises(ValueError):
        ledger.transition(stored["position_id"], verdict="CONFIRMED", resolution="x",
                          evidence_refs=(), draft_id="d", now=later)


def test_abandoned_positions_surface_in_recent_resolutions(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    later = NOW + timedelta(days=6)
    # 写入口触发 sweep：过期 OPEN 被放弃，原因需出现在 recent_resolutions
    ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                    draft_id="d2", now=later)
    rows = ledger.recent_resolutions(now=later)
    assert len(rows) == 1
    assert rows[0]["position_id"] == row["position_id"]
    assert rows[0]["status"] == "ABANDONED"
    assert rows[0]["resolution"] == "no verification within 5 days"
    # 7 天窗口对 ABANDONED 同样生效
    assert ledger.recent_resolutions(now=later + timedelta(days=8)) == []


def test_recent_resolutions_returns_terminal_within_window(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    ledger.transition(row["position_id"], verdict="REFUTED", resolution="wrong about x",
                      evidence_refs=(), draft_id="d", now=NOW)
    assert len(ledger.recent_resolutions(now=NOW)) == 1
    assert ledger.recent_resolutions(now=NOW + timedelta(days=8)) == []


def test_chinese_content_distinct_ids_same_second(tmp_path: Path):
    ledger = _ledger(tmp_path)
    a = ledger.register(topic="质押", proposition="流入将持续", falsifier="流入回落",
                        evidence_refs=(), draft_id="d1", now=NOW)
    b = ledger.register(topic="费率", proposition="符号将转正", falsifier="维持负值",
                        evidence_refs=(), draft_id="d2", now=NOW)
    assert a["position_id"] != b["position_id"]
    assert not a["position_id"].startswith("pos--")


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


def test_register_records_source_and_normalized_post_ids(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d1", now=NOW, source="x")
    assert row["source"] == "x"
    assert row["post_ids"] == [{"id": "d1", "surface": "square"}]
    row2 = ledger.register(topic="t2", proposition="p2", falsifier="f2", evidence_refs=(),
                           draft_id="d2", now=NOW)
    assert row2["source"] == "square"


def test_attach_post_is_idempotent_and_tolerates_legacy_strings(tmp_path: Path):
    root = tmp_path / "content_mind"
    root.mkdir(parents=True)
    (root / "positions.json").write_text(json.dumps({"positions": [
        {"position_id": "pos-legacy", "topic": "t", "proposition": "p", "falsifier": "f",
         "status": "OPEN", "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
         "evidence_refs": [], "post_ids": ["legacy-draft"], "resolution": None}]}),
        encoding="utf-8")
    ledger = PositionLedger(root)
    ledger.attach_post("pos-legacy", "x-post:1", surface="x")
    row = ledger.get("pos-legacy")
    assert row["post_ids"] == [{"id": "legacy-draft", "surface": "square"},
                               {"id": "x-post:1", "surface": "x"}]
    ledger.attach_post("pos-legacy", "x-post:1", surface="x")
    assert len(ledger.get("pos-legacy")["post_ids"]) == 2
    with pytest.raises(ValueError):
        ledger.attach_post("pos-missing", "x-post:2", surface="x")


def test_transition_keeps_dict_entries_and_dedups(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d1", now=NOW)
    pid = row["position_id"]
    ledger.attach_post(pid, "x-post:9", surface="x")
    done = ledger.transition(pid, verdict="CONFIRMED", resolution="结算数据符合",
                             evidence_refs=("web:e2",), draft_id="d2", now=NOW)
    assert done["post_ids"] == [{"id": "d1", "surface": "square"},
                                {"id": "x-post:9", "surface": "x"},
                                {"id": "d2", "surface": "square"}]


def test_append_evidence_caps_fifo_and_keeps_string_refs(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f",
                          evidence_refs=("web:s1",), draft_id="d1", now=NOW)
    pid = row["position_id"]
    for i in range(12):
        ledger.append_evidence(pid, {"source": "x_observe", "ref": f"x-post:{i}",
                                     "at": NOW.isoformat(), "note": f"n{i}"})
    refs = ledger.get(pid)["evidence_refs"]
    strings = [r for r in refs if isinstance(r, str)]
    dicts = [r for r in refs if isinstance(r, dict)]
    assert strings == ["web:s1"]
    assert len(dicts) == 10
    assert dicts[0]["ref"] == "x-post:2"
    assert dicts[-1]["ref"] == "x-post:11"
    ledger.append_evidence(pid, {"ref": "x-post:11", "at": NOW.isoformat(), "note": "dup"})
    refs_after = ledger.get(pid)["evidence_refs"]
    assert len([r for r in refs_after if isinstance(r, dict)]) == 10
    # 字符串证据从不被淘汰：重复 append 之后重新取回条目验证（局部 strings 在 append 前捕获，恒真无断言力）
    assert [r for r in refs_after if isinstance(r, str)] == ["web:s1"]


def test_append_evidence_requires_ref_and_bounds_note(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    with pytest.raises(ValueError):
        ledger.append_evidence(row["position_id"], {"note": "no ref"})
    ledger.append_evidence(row["position_id"], {"source": "x_observe", "ref": "x-post:1",
                                                "at": NOW.isoformat(), "note": "长" * 300})
    assert len(ledger.get(row["position_id"])["evidence_refs"][-1]["note"]) == 120


def test_topic_matches_tokens_and_chinese():
    assert topic_matches("sol-staking", "SOL staking inflows doubled")
    assert topic_matches("质押", "SOL 质押流入持续扩张")
    assert topic_matches("strk+费率", "strk funding rate flipped positive")
    assert not topic_matches("sol-staking", "eth gas fees dropped")
    assert not topic_matches("", "anything")


def test_writes_serialize_under_lock(tmp_path: Path):
    ledger = _ledger(tmp_path)
    row = ledger.register(topic="t", proposition="p", falsifier="f", evidence_refs=(),
                          draft_id="d", now=NOW)
    pid = row["position_id"]
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            for j in range(5):
                ledger.attach_post(pid, f"post-{i}-{j}", surface="x")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ledger.get(pid)["post_ids"]) == 21  # 1 注册 + 4*5，零丢失
