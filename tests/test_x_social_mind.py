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
