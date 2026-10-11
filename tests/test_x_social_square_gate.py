"""Square→X release gate: owner-reviewed Square cycles express on X."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
from wechat_traffic_agent.x_social import XAutonomyLoop, XHistoryLedger
from wechat_traffic_agent.learning_expression_quality import learning_required_for_sharing
from wechat_traffic_agent.orchestration.social import SocialTaskMethods
from wechat_traffic_agent.orchestration.content import ContentTaskMethods

NOW = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)


class _L1:
    def choose_x_strategy(self, **kwargs):
        return {
            "schema_version": "ferro-l1-x-strategy.v1", "status": "SELECTED",
            "post_timing": "POST_NOW", "x_format": "SHORT_POST",
            "exploration_mode": "EXPLOIT", "angle": "EVIDENCE_FIRST",
            "interaction_mode": "ORIGINAL", "provider": "TEST", "model": "test",
        }


class _Model:
    def __init__(self, response=None):
        self.response = response or {
            "post_text": "ETH 价格回升而持仓数量未增长，还不能据此认定新增多头在接力。",
            "rationale": "Square 评审已裁定该认识值得公开表达，这里是更短的 X 版本。",
        }
        self.tasks = []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.tasks.append(task)
        return json.dumps(self.response)

    def persist(self):
        pass


class _BrowserTransport:
    def __init__(self):
        self.calls = []

    async def identity(self):
        return {"id": "me", "username": "agent", "name": "Agent",
                "public_metrics": {"followers_count": 2000}}

    async def publish(self, *, text):
        self.calls.append(text)
        return SimpleNamespace(post_id="p42", url="https://x.com/agent/status/p42",
                               handle="agent", provider="JEV_AGENT_RUNTIME")


def _square_draft(tmp_path: Path) -> dict:
    return SquareDraftStore(tmp_path).create(
        body="ETH 价格回升而持仓数量未增长，这组观察还不足以认定新增多头正在接力。$ETH 来源：https://example.org/evidence",
        symbols=["ETH"],
        evidence={"observed_at": NOW.isoformat(), "title": "ETH market structure",
                  "summary": "持仓未随价格回升。", "url": "https://example.org/evidence"},
        reader_question="反弹有没有新增持仓配合？",
        thesis="尚未看到持仓扩张的证据",
        invalidation="仅限本次观测窗口",
    )


def _square_result(tmp_path: Path, cycle: str, status: str) -> dict:
    doc = _square_draft(tmp_path)
    return {"status": status, "cycle": cycle, "draft_id": doc["draft_id"],
            "publication_surface": "BINANCE_SQUARE", "draft_status": doc["status"]}


def _loop(tmp_path: Path, model):
    return XAutonomyLoop(
        runtime=SimpleNamespace(),
        model_runtime=model,
        state_root=tmp_path,
        base_url="",
        browser_publish_transport=_BrowserTransport(),
        l1_decisions=_L1(),
    )


def test_square_expression_ready_owner_cycle_expresses_on_x(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-owner-square-express"
    result = _square_result(tmp_path, cycle, "SQUARE_EXPRESSION_READY")
    doc = SquareDraftStore(tmp_path).get(result["draft_id"])
    model = _Model()
    outcome = asyncio.run(_loop(tmp_path, model).after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "POSTED"
    assert outcome["post_id"] == "p42"
    # The owner-reviewed Square copy feeds the X draft prompt: artifact body
    # plus the durable square_draft reference, without any cognition.json.
    prompt = model.tasks[-1]
    assert "持仓数量未增长" in prompt
    assert doc["draft_id"] in prompt
    assert '"square_draft"' in prompt
    assert (tmp_path / "content/continuous/cycles" / cycle / "social_expression_drafts.json").exists()
    row = XHistoryLedger(tmp_path).recent()[-1]
    assert row["event_type"] == "EXPRESSION" and row["cycle"] == cycle


def test_cognition_only_square_cycle_is_still_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    result = _square_result(tmp_path, "v2-owner-square-cognition", "COGNITION_ONLY")
    model = _Model()
    outcome = asyncio.run(_loop(tmp_path, model).after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "NO_EXPRESSION"
    assert model.tasks == []


def test_learning_first_policy_does_not_hold_square_reviewed_cycles(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    policy = tmp_path / "content" / "learning"
    policy.mkdir(parents=True)
    (policy / "sharing_policy.json").write_text(json.dumps({"require_learning_discovery": True}))
    assert learning_required_for_sharing(tmp_path) is True
    result = _square_result(tmp_path, "v2-owner-square-learning", "SQUARE_EXPRESSION_READY")
    outcome = asyncio.run(_loop(tmp_path, _Model()).after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "POSTED"


def _runner(tmp_path: Path, **components) -> SimpleNamespace:
    return SimpleNamespace(state_root=tmp_path, _run_async=lambda awaitable: asyncio.run(awaitable),
                           **components)


def test_square_cycle_runs_x_hook_and_skips_website_lanes(tmp_path: Path):
    class Boom:
        async def after_content_cycle(self, **kwargs):
            raise AssertionError("Square cycle must not run website-first social lanes")

    seen = []

    class XLoop:
        async def after_content_cycle(self, *, result, now):
            seen.append(result)
            return {"status": "POSTED", "cycle": result.get("cycle")}

    result = _square_result(tmp_path, "v2-owner-square-stage", "SQUARE_EXPRESSION_READY")
    runner = _runner(
        tmp_path,
        continuous_loop=SimpleNamespace(publisher=object()),
        social_expression_loop=Boom(), x_browser_publisher=Boom(),
        okx_orbit_publisher=Boom(), x_loop=XLoop(),
        _error=lambda exc: {"status": "ERROR", "detail": str(exc)},
    )
    rows = {"status": "OK"}
    SocialTaskMethods._run_social_stage(runner, rows=rows, continuous=result, now=NOW)
    assert len(seen) == 1 and seen[0]["cycle"] == result["cycle"]
    assert rows["x_social_expression"]["status"] == "POSTED"
    assert "x_syndication" not in rows and "social_expression_drafts" not in rows
    assert "x_browser_publication" not in rows and "okx_orbit_publication" not in rows


def test_website_cycle_syndication_lane_is_unchanged(tmp_path: Path):
    cycle = "v2-website-unchanged"
    folder = tmp_path / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    (folder / "publication.json").write_text(json.dumps({
        "audit": {"passed": True},
        "outcome_attribution": {"public_route": "/notes/website-first/"},
        "publication_event_id": "evt-1",
    }))

    class Boom:
        async def after_content_cycle(self, **kwargs):
            raise AssertionError("Website cycle must not reach the Square-released X hook")

    runner = _runner(
        tmp_path,
        continuous_loop=SimpleNamespace(publisher=object()),
        social_expression_loop=None, x_browser_publisher=None,
        okx_orbit_publisher=None, x_loop=Boom(),
        _error=lambda exc: {"status": "ERROR", "detail": str(exc)},
    )
    rows = {"status": "OK"}
    SocialTaskMethods._run_social_stage(
        runner, rows=rows, continuous={"status": "PUBLISHED", "cycle": cycle}, now=NOW)
    assert rows["x_syndication"] == {"status": "QUEUED", "event_id": "evt-1", "cycle": cycle}


def test_replay_accepts_square_expression_ready_cycle(tmp_path: Path):
    result_path = tmp_path / "result.json"
    prior = _square_result(tmp_path, "v2-owner-square-replay", "SQUARE_EXPRESSION_READY")
    result_path.write_text(json.dumps(prior), encoding="utf-8")
    replayed = []

    runner = SimpleNamespace(
        _run_social_stage=lambda *, rows, continuous, now: replayed.append(continuous),
        state_events=SimpleNamespace(emit=lambda *args, **kwargs: {"emitted": True}),
    )
    rows = {}
    ContentTaskMethods._replay_prior_content_publication(
        runner, rows, {"result_path": str(result_path)}, prior["cycle"], NOW)
    assert len(replayed) == 1 and replayed[0]["status"] == "SQUARE_EXPRESSION_READY"
    assert rows["social_stage_replay"]["status"] == "REPLAYED"
    assert rows["cognition_state_event"] == {"emitted": True}
