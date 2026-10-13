import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.decision_log import append_decision, discard_streak, recent_decisions
from wechat_traffic_agent.narrative_memory import build_story_so_far  # noqa: F401  确保模块就位
from wechat_traffic_agent.square_observation import (
    EditorialBrief, ObservationExpression, ObservationReview, prepare_observation,
)

ZONE = timezone(timedelta(hours=8))


class _FakeMind:
    def __init__(self, projection):
        self._projection = projection

    def public_projection(self):
        return self._projection


def _explorer():
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    from types import SimpleNamespace
    return SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)


_SOURCE = {"evidence_id": "sol-news", "source_kind": "rss",
           "title": "Solana staking change",
           "summary": "Solana staking inflows doubled after the network upgrade.",
           "observed_at": "2026-10-13T12:00:00+00:00"}
_NOW = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)  # 北京 21:00 → 配额强制深度体裁


def _expression(genre_body="Solana 升级后质押流入翻倍，我认为这是网络被真实使用的证据；若流入回落则我错。",
                action="SHARE"):
    return ObservationExpression(action=action, format="OBSERVATION",
        rationale="Connects to a held belief.", body=genre_body, symbols=["SOL"],
        source_ids=["sol-news"], thesis="网络升级与质押流入", reader_question="流入能否持续",
        invalidation="流入回落", value_focus="explanatory_progress")


def test_genre_pipeline_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    prompts = []

    async def structured(runtime, prompt, model):
        prompts.append((model, prompt))
        if model is EditorialBrief:
            return model(topic="Solana 质押流入翻倍", angle="用升级后的真实使用解释资金去向",
                         evidence_ids=["sol-news"], callback="",
                         structure_plan="先给事实，再给判断，最后给认错条件")
        if model is ObservationExpression:
            return _expression()
        if model is ObservationReview:
            return ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                     adds_value=True, timely=True, genre_fit=True,
                                     template_repeat=False, rationale="solid deep take")
        raise AssertionError(model)

    result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
        state_root=tmp_path, cycle="genre-happy", now=_NOW, seed_evidence=[dict(_SOURCE)],
        structured=structured,
        mind=_FakeMind({"positions": {"open": [{"proposition": "ETH 多头", "falsifier": "跌破 3800"}]}})))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    editor_prompts = [p for m, p in prompts if m is EditorialBrief]
    assert editor_prompts and "story_so_far" in editor_prompts[0]
    writer_prompts = [p for m, p in prompts if m is ObservationExpression]
    assert any("declared genre" in p and "deep" in p for p in writer_prompts)
    review_prompts = [p for m, p in prompts if m is ObservationReview]
    assert any("recent_openings" in p for p in review_prompts)
    rows = recent_decisions(tmp_path)
    assert len(rows) == 1 and rows[0]["review"]["verdict"] == "pass"
    assert rows[0]["editorial_brief"]["topic"] == "Solana 质押流入翻倍"
    assert rows[0]["depth_tier"] == "deep"


def test_genre_pipeline_rewrites_then_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    writer_prompts = []
    reviews = [ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                 adds_value=True, timely=True, genre_fit=False,
                                 template_repeat=False, rationale="argument lacks counter-case"),
               ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                 adds_value=True, timely=True, genre_fit=True,
                                 template_repeat=False, rationale="pass now")]

    async def structured(runtime, prompt, model):
        if model is EditorialBrief:
            return model(topic="Solana 质押流入翻倍", angle="数据解释", evidence_ids=["sol-news"],
                         callback="", structure_plan="四段论证")
        if model is ObservationExpression:
            writer_prompts.append(prompt)
            return _expression()
        if model is ObservationReview:
            return reviews.pop(0)
        raise AssertionError(model)

    result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
        state_root=tmp_path, cycle="genre-rewrite", now=_NOW, seed_evidence=[dict(_SOURCE)],
        structured=structured))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert len(writer_prompts) == 2
    assert "argument lacks counter-case" in writer_prompts[1]
    row = recent_decisions(tmp_path)[0]
    assert row["review"]["verdict"] == "pass" and row["review"]["review_calls"] == 2


def test_genre_pipeline_discards_and_warns_on_streak(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    for i in range(2):  # 预置两连弃，本帖是第三连
        append_decision(tmp_path, {"review": {"verdict": "discarded"}, "i": i})

    async def structured(runtime, prompt, model):
        if model is EditorialBrief:
            return model(topic="Solana 质押流入翻倍", angle="数据解释", evidence_ids=["sol-news"],
                         callback="", structure_plan="四段论证")
        if model is ObservationExpression:
            return _expression()
        if model is ObservationReview:
            return ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                     adds_value=True, timely=True, genre_fit=False,
                                     template_repeat=True, rationale="same mold as recent posts")
        raise AssertionError(model)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
            state_root=tmp_path, cycle="genre-discard", now=_NOW,
            seed_evidence=[dict(_SOURCE)], structured=structured))
    assert result["status"] == "COGNITION_ONLY"
    assert "discarded" in result["rationale"]
    assert discard_streak(tmp_path) == 3
    assert any("GENRE_PIPELINE_DISCARD_STREAK" in r.message for r in caplog.records)
    rows = recent_decisions(tmp_path)
    assert rows[-1]["thesis"] == "网络升级与质押流入"
    assert rows[-1]["symbols"] == ["SOL"]


def test_disabled_pipeline_keeps_legacy_prompts(monkeypatch, tmp_path):
    monkeypatch.delenv("CONTENT_GENRE_PIPELINE_ENABLED", raising=False)

    async def structured(runtime, prompt, model):
        assert model is not EditorialBrief
        if model is ObservationExpression:
            assert "declared genre" not in prompt
            assert "story_so_far" not in prompt
            return _expression()
        if model is ObservationReview:
            assert "recent_openings" not in prompt
            return ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                     adds_value=True, timely=True, rationale="legacy pass")
        raise AssertionError(model)

    result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
        state_root=tmp_path, cycle="genre-off", now=_NOW, seed_evidence=[dict(_SOURCE)],
        structured=structured))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert recent_decisions(tmp_path) == []


def test_genre_hold_writes_no_decision_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    writer_prompts = []
    review_prompts = []

    async def structured(runtime, prompt, model):
        if model is EditorialBrief:
            return model(topic="Solana 质押流入翻倍", angle="数据解释", evidence_ids=["sol-news"],
                         callback="", structure_plan="四段论证")
        if model is ObservationExpression:
            writer_prompts.append(prompt)
            return _expression(action="HOLD")
        if model is ObservationReview:
            review_prompts.append(prompt)
            raise AssertionError("HOLD must not reach review")
        raise AssertionError(model)

    result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
        state_root=tmp_path, cycle="genre-hold", now=_NOW, seed_evidence=[dict(_SOURCE)],
        structured=structured))
    assert len(writer_prompts) == 1  # HOLD 短路：体裁模式外层循环不发起第二次尝试
    assert review_prompts == []
    assert recent_decisions(tmp_path) == []
    assert result["status"] == "COGNITION_ONLY"


def test_scheduler_crash_falls_back_to_legacy(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    policy_file = tmp_path / "genre_policy.json"
    policy_file.write_text(json.dumps({"genres": {
        "quick_take": {"weight": "abc"}, "data_digest": {"weight": "abc"},
        "engagement": {"weight": "abc"}, "deep_take": {"weight": "abc"},
        "explainer": {"weight": "abc"}, "story_review": {"weight": "abc"}}}), encoding="utf-8")
    monkeypatch.setenv("CONTENT_GENRE_POLICY_PATH", str(policy_file))
    prompts = []

    async def structured(runtime, prompt, model):
        prompts.append(model)
        if model is ObservationExpression:
            return _expression()
        if model is ObservationReview:
            return ObservationReview(facts_specific=True, grounded=True, has_position=True,
                                     adds_value=True, timely=True, rationale="legacy pass")
        raise AssertionError(model)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(prepare_observation(runtime=None, explorer=_explorer(),
            state_root=tmp_path, cycle="genre-scheduler-crash", now=_NOW,
            seed_evidence=[dict(_SOURCE)], structured=structured))
    assert any("GENRE_SCHEDULER_FAILED" in r.message for r in caplog.records)
    assert result["status"] == "SQUARE_EXPRESSION_READY"  # 旧路径完成发帖，不被阻断
    assert not any(m is EditorialBrief for m in prompts)  # decision=None → 无编辑调用
    assert recent_decisions(tmp_path) == []
