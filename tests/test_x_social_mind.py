from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import wechat_traffic_agent.platforms.x.x_social as xs
from wechat_traffic_agent.platforms.x.x_social import XAutonomyLoop, _expression_model_context

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


class _FakeMind:
    def __init__(self, projection=None, fail=False):
        self._projection = projection if projection is not None else {}
        self._fail = fail

    def public_projection(self):
        if self._fail:
            raise RuntimeError("projection exploded")
        return self._projection


def _loop(tmp_path: Path, mind=None) -> XAutonomyLoop:
    return XAutonomyLoop(runtime=None, model_runtime=None, state_root=tmp_path,
                         base_url="https://example.com", mind=mind)


_PROJECTION = {"beliefs": [], "interests": [], "open_inquiries": [],
               "positions": {"open": [], "recent_resolutions": []}}


def test_mind_context_returns_projection_empty_or_degrades(tmp_path: Path):
    assert _loop(tmp_path, mind=_FakeMind(_PROJECTION))._mind_context()["positions_open"] == []
    assert _loop(tmp_path, mind=None)._mind_context() == {}
    assert _loop(tmp_path, mind=_FakeMind(fail=True))._mind_context() == {}


def test_expression_context_carries_mind_through_unified_projector():
    payload = _expression_model_context({"cycle": "c1", "mind": {"beliefs": [{"proposition": "p"}]}})
    assert payload["mind"]["beliefs"] == [{"proposition": "p"}]
    assert "mind" not in _expression_model_context({"cycle": "c1"})


def test_after_content_cycle_attaches_mind_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    loop = _loop(tmp_path, mind=_FakeMind(_PROJECTION))

    async def fake_publish(folder, context, now):
        return {}

    monkeypatch.setattr(loop, "_publish_prediction_outcome", fake_publish)
    monkeypatch.setattr(
        loop, "_cycle_context",
        lambda result: (tmp_path / "cycle-c1", {"cycle": "c1"}))
    outcome = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED"}, now=NOW))
    assert outcome["status"] == "NO_EXPRESSION"
    assert "mind" in loop._last_expression_context


def test_attach_verified_post_matches_topic_and_is_benign(tmp_path: Path):
    loop = _loop(tmp_path)
    row = loop.positions.register(topic="sol-staking", proposition="流入持续",
                                  falsifier="流入回落", evidence_refs=(),
                                  draft_id="d1", now=NOW)
    loop._attach_verified_post(text="SOL staking inflows remain strong", post_id="123", now=NOW)
    loop._attach_verified_post(text="eth gas fees dropped hard", post_id="456", now=NOW)
    posts = loop.positions.get(row["position_id"])["post_ids"]
    assert {"id": "123", "surface": "x"} in posts
    assert {"id": "456", "surface": "x"} not in posts
    loop._attach_verified_post(text="", post_id="789", now=NOW)
    assert loop.positions.get(row["position_id"])["post_ids"] == posts


def test_position_evidence_backflow_matches_and_caps(tmp_path: Path):
    loop = _loop(tmp_path)
    row = loop.positions.register(topic="sol-staking", proposition="流入持续",
                                  falsifier="流入回落", evidence_refs=(),
                                  draft_id="d1", now=NOW)
    mentions = [
        {"evidence_id": "x-post:a1", "summary": "SOL staking inflows at record high"},
        {"evidence_id": "x-post:b2", "summary": "unrelated eth chatter"},
    ]
    loop._backfill_position_evidence(mentions=mentions, now=NOW)
    refs = loop.positions.get(row["position_id"])["evidence_refs"]
    dicts = [r for r in refs if isinstance(r, dict)]
    assert len(dicts) == 1
    assert dicts[0]["source"] == "x_observe"
    assert dicts[0]["ref"] == "x-post:a1"
    assert len(dicts[0]["note"]) <= 120
    assert loop.positions.get(row["position_id"])["status"] == "OPEN"  # 证据不触发转移
    loop._backfill_position_evidence(mentions=[], now=NOW)  # 空观察不炸


def test_register_scanned_position_registers_with_source_x_or_skips(tmp_path: Path):
    from wechat_traffic_agent.platforms.x.x_social import XNativePosition, XPositionScan
    loop = _loop(tmp_path)
    assert loop._register_scanned_position(None, cycle="c1", now=NOW) is None
    assert loop._register_scanned_position(XPositionScan(), cycle="c1", now=NOW) is None
    row = loop._register_scanned_position(
        XPositionScan(position=XNativePosition(topic="strk", proposition="费率将转正",
                                               falsifier="费率持续为负")), cycle="c1", now=NOW)
    assert row["source"] == "x"
    assert row["status"] == "OPEN"


def test_position_scan_models_are_conservative_by_default():
    from wechat_traffic_agent.platforms.x.x_social import XPositionScan
    assert XPositionScan.model_validate({}).position is None
