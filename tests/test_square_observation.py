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
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True, timely=True, rationale="Supported observation and explicit judgment.")
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
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True, timely=True, rationale="Supported explicit view.")
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

def test_data_post_admitted_without_body_opinion(tmp_path):
    now = datetime.now(timezone.utc)
    source = {"evidence_id": "sol-data", "source_kind": "public-api", "title": "SOL market",
              "summary": "SOL rolling 24h +12%, turnover 2000000 USDT", "observed_at": now.isoformat()}
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "let concrete numbers stand" in prompt
            return model(action="SHARE", format="DATA", rationale="Large observed move with liquid turnover.",
                body="21:38 币安现货24小时：SOL +12%，成交额200万 USDT。", symbols=["SOL"], source_ids=["sol-data"],
                thesis="显著涨幅与成交规模值得一起看", reader_question="哪个币发生异动",
                invalidation="后续窗口更新", value_focus="reader_agency")
        assert "without commentary" in prompt
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True, timely=True,
                     rationale="Concrete named-asset data is informative.")
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="data-post", now=now, seed_evidence=[source], structured=structured))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    draft = SquareDraftStore(tmp_path).get(result["draft_id"])
    assert draft["observation_provenance"]["format"] == "DATA"
    assert draft["body"].endswith("$SOL")
    assert "我的判断" not in draft["body"]

def test_vague_token_market_commentary_held(tmp_path):
    now = datetime.now(timezone.utc)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    async def structured(runtime, prompt, model):
        assert model is ObservationExpression
        return model(action="SHARE", rationale="Market risk appetite commentary",
            body="部分代币上涨数百甚至数千个百分点，风险也在升温。",
            symbols=[], source_ids=["web3-data"], thesis="风险在升温", reader_question="什么风险",
            invalidation="风险下降", value_focus="resilience")
    source = {"evidence_id": "web3-data", "source_kind": "public-api", "summary": "SOL priceChangePercent 12"}
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="vague-post", now=now, seed_evidence=[source], structured=structured))
    assert result["status"] == "COGNITION_ONLY"
    assert "concrete source data" in result["rationale"]
    assert not SquareDraftStore(tmp_path).list()

def test_reviewed_data_without_research_fields_resumes_and_publishes(tmp_path):
    from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftWorker
    now = datetime.now(timezone.utc)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", format="DATA", rationale="Measured move.",
                body="RLC 现货24小时上涨42%，成交额5240万 USDT。", symbols=["RLC"],
                source_ids=["rlc-data"], thesis="涨幅与成交规模一起观察", value_focus="reader agency")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="Exact measured asset data.")
    source = {"evidence_id": "rlc-data", "source_kind": "public-api",
              "summary": "RLC rolling 24h +42%, turnover 52400000 USDT", "observed_at": now.isoformat()}
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    args = dict(runtime=None, explorer=explorer, state_root=tmp_path, cycle="resume-data",
                now=now, seed_evidence=[source], structured=structured)
    result = asyncio.run(prepare_observation(**args))
    store = SquareDraftStore(tmp_path)
    draft = store.get(result["draft_id"])
    assert not draft["reader_question"] and not draft["invalidation"]
    draft.update(status="DRAFT")
    store.save(draft)
    async def no_generation(*args):
        raise AssertionError("An already reviewed body must not be regenerated")
    result = asyncio.run(prepare_observation(**dict(args, structured=no_generation)))
    assert result["draft_status"] == "REVIEWED"
    calls = []
    client = SimpleNamespace(key="test", post_text=lambda body: calls.append(body) or "123")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
        patch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
        published = SquareDraftWorker(tmp_path, client=client).run_once()
    assert published["status"] == "SUCCESS"
    assert len(calls) == 1

def test_rejected_candidate_changes_asset_in_same_cycle(tmp_path):
    now = datetime.now(timezone.utc)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    choices, reviews = [], []
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "Do not append generic disclaimers" in prompt
            symbol = "RLC" if not choices else "KAIA"
            if choices:
                assert "RLC repeats the already published snapshot" in prompt
                assert "rlc-data" in prompt
            choices.append(symbol)
            return model(action="SHARE", format="DATA", rationale="Observed move",
                body=symbol+" 现货24小时涨12%，成交额200万 USDT。",
                symbols=[symbol], source_ids=[symbol.lower()+"-data"],
                thesis="具体资产的异动", value_focus="reader agency")
        reviews.append(prompt)
        first = len(reviews) == 1
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=not first,
                     timely=True, rationale="RLC repeats the already published snapshot" if first else "New asset data.")
    sources = [{"evidence_id": symbol.lower()+"-data", "source_kind": "public-api",
                "summary": symbol+" 24h +12%, turnover 2000000 USDT", "observed_at": now.isoformat()}
               for symbol in ("RLC", "KAIA")]
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="switch-asset", now=now, seed_evidence=sources, structured=structured))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert choices == ["RLC", "KAIA"]
    assert len(SquareDraftStore(tmp_path).list()) == 1
    assert SquareDraftStore(tmp_path).get(result["draft_id"])["symbols"] == ["KAIA"]
