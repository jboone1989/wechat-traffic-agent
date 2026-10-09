import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from wechat_traffic_agent.square_observation import prepare_observation, ObservationExpression, ObservationReview
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from wechat_traffic_agent.behavioral_state import build_behavioral_state, compact_behavioral_state_for_prompt
from wechat_traffic_agent.runtime_backend import _workspace_enabled_for

def test_observation_review_and_tamper(tmp_path):
    now = datetime.now(timezone.utc)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": [{"news_id": "source1", "title": "Solana service interruption", "summary": "The service reports an interruption.", "url": "https://example.org/status"}]}
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="A material reliability observation.",
                body="$SOL 服务中断。我的判断是可用性比短期价格更值得关注。",
                symbols=[" $sol "], source_ids=["source1"], thesis="可用性更值得关注",
                reader_question="可用性如何影响用户", invalidation="服务公告被更正", value_focus="resilience")
        return model(grounded=True, has_position=True, adds_value=True, timely=True, rationale="Supported observation and explicit judgment.")
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="observation1", now=now, structured=structured))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    store = SquareDraftStore(tmp_path)
    draft = store.get(result["draft_id"])
    assert draft["status"] == "REVIEWED"
    assert not (tmp_path / "content/continuous/cycles/observation1/learning_result.json").exists()
    store.validate(draft, datetime.now(timezone.utc))
    draft["body"] += "价格一定上涨"
    with pytest.raises(ValueError):
        store.validate(draft, datetime.now(timezone.utc))

def test_loop_direct_branch(monkeypatch, tmp_path):
    import wechat_traffic_agent.square_observation as module
    async def prepare(**kwargs):
        assert kwargs["state_root"] == tmp_path
        return {"status": "SQUARE_EXPRESSION_READY"}
    monkeypatch.setattr(module, "prepare_observation", prepare)
    loop = object.__new__(ContinuousContentLoop)
    loop._publication_surface = "BINANCE_SQUARE"
    loop.state, loop.runtime, loop.explorer = tmp_path, None, None
    assert asyncio.run(loop._run(tmp_path, "observation2", datetime.now(timezone.utc)))["status"] == "SQUARE_EXPRESSION_READY"

def test_neural_values_and_bounded_memory():
    low = build_behavioral_state({"neural_life": {"drives": {"survival": 0, "evolution": 1}, "affect": {"curiosity": 1}}})
    high = build_behavioral_state({"neural_life": {"drives": {"survival": 1, "evolution": 0}, "affect": {"curiosity": 0}}})
    assert low["editorial_values"]["explanatory_progress"] > high["editorial_values"]["explanatory_progress"]
    assert high["editorial_values"]["resilience"] > low["editorial_values"]["resilience"]
    assert compact_behavioral_state_for_prompt(low)["editorial_values"] == low["editorial_values"]
    assert not _workspace_enabled_for("FERRO_SQUARE_OBSERVATION: supplied facts", force=True)
    assert _workspace_enabled_for("RESEARCH: deeper evidence", force=True)

def test_normalize_altcoin_symbols():
    from wechat_traffic_agent.platforms.binance_square_editorial import normalize_symbols, validate_mining_tags
    assert normalize_symbols([" $doge ", "DOGE", "$1000SATS"]) == ["DOGE", "1000SATS"]
    validate_mining_tags("$DOGE $1000SATS", ["DOGE", "1000SATS"])
    with pytest.raises(ValueError):
        normalize_symbols(["BTC;drop"])

def test_altcoin_market_candidates():
    from wechat_traffic_agent.sources.binance_alt_market import market_candidates
    now = datetime.now(timezone.utc)
    stamp = int(now.timestamp() * 1000)
    def row(symbol, change, volume="2000000", close=stamp):
        return {"symbol": symbol, "lastPrice": "1", "priceChangePercent": change,
                "quoteVolume": volume, "openTime": close-86400000, "closeTime": close}
    rows = [row("DOGEUSDT", "12"), row("SOLUSDT", "-8"), row("1000SATSUSDT", "5"),
            row("ETHUSDT", "2"), row("USDCUSDT", "0"), row("LOWUSDT", "99", "10"),
            row("OLDUSDT", "10", close=stamp-180000)]
    candidates = market_candidates(rows, now)
    assert {r["title"].split()[0] for r in candidates} == {"DOGEUSDT", "SOLUSDT", "1000SATSUSDT"}
    assert all("rolling 24h" in r["summary"] for r in candidates)

def test_missing_tag_fixed_before_review_and_duplicate_held(tmp_path):
    now = datetime.now(timezone.utc)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": [{"news_id": "doge-news", "title": "Dogecoin service change",
            "summary": "Dogecoin service changed.", "url": "https://example.org/doge"}]}
    review_calls = []
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="A new service consequence.",
                body="DOGE 服务变化，我认为可用性更值得关注。", symbols=["$DOGE"], source_ids=["doge-news"],
                thesis="服务可用性值得关注", reader_question="服务怎样变化", invalidation="公告更正",
                value_focus="resilience")
        assert "$DOGE" in prompt
        review_calls.append(prompt)
        return model(grounded=True, has_position=True, adds_value=True, timely=True, rationale="Supported explicit view.")
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="doge1", now=now, structured=structured))
    store = SquareDraftStore(tmp_path)
    draft = store.get(result["draft_id"])
    assert draft["symbols"] == ["DOGE"]
    assert draft["body"].endswith("$DOGE")
    draft.update(status="SUCCESS", published_at=now.isoformat())
    store.save(draft)
    second = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="doge2", now=now, structured=structured))
    assert second["status"] == "COGNITION_ONLY"
    assert "repeats" in second["rationale"]
    assert len(review_calls) == 1

def test_searched_altcoin_evidence_reaches_expression(tmp_path):
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    async def structured(runtime, prompt, model):
        assert "owner-search:altcoin" in prompt
        assert "Altcoin upgrade announcement" in prompt
        return ObservationExpression(action="HOLD", rationale="Need one more fact.")
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="search-alt", now=datetime.now(timezone.utc), structured=structured,
        seed_evidence=[{"evidence_id": "owner-search:altcoin", "title": "Altcoin upgrade announcement",
                        "summary": "An observed protocol upgrade."}]))
    assert result["rationale"] == "Need one more fact."
