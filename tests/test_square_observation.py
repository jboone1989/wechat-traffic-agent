import asyncio
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from types import SimpleNamespace
import pytest
from wechat_traffic_agent.content_mind import public_mind_context
from wechat_traffic_agent.position_ledger import PositionLedger
from wechat_traffic_agent.square_observation import (
    prepare_observation, ObservationExpression, ObservationReview, _structure_fingerprint,
)


class _FakeMind:
    def __init__(self, projection):
        self._projection = projection

    def public_projection(self):
        return self._projection


def test_structure_fingerprint_ignores_numbers_and_tickers():
    data_a = _structure_fingerprint("币安现货数据显示，SOLUSDT 过去24小时明显上涨。\n\n• 最新价格：100 USDT\n• 24小时涨幅：+12%")
    data_b = _structure_fingerprint("币安现货数据显示，OGNUSDT 过去24小时明显上涨。\n\n• 最新价格：0.03 USDT\n• 24小时涨幅：+11%")
    prose = _structure_fingerprint("我认为服务可用性比短期价格更值得关注，这是判断的关键所在。")
    assert SequenceMatcher(None, data_a, data_b).ratio() > 0.7
    assert SequenceMatcher(None, data_a, prose).ratio() < 0.5


def test_structural_repeat_of_recent_data_post_held(tmp_path):
    now = datetime.now(timezone.utc)
    store = SquareDraftStore(tmp_path)
    published = store.create(
        body="币安现货数据显示，XYZUSDT 过去24小时明显上涨。\n\n截至 2026-10-10 13:20 UTC 观察（滚动24小时窗口）：\n\n"
             "• XYZUSDT 最新价格：1.23 USDT\n• 24小时涨幅：+12.3%\n• 24小时成交额：5,000,000 USDT\n\n"
             "放量配合价格上行，属于典型的资金参与形态，这类标的的波动往往在数据窗口结束后仍会延续一段时间，"
             "值得继续观察后续成交变化，看看参与体量是否能维持今天的规模。\n\n$XYZ",
        evidence={"kind": "SOURCE_OBSERVATIONS", "observed_at": now.isoformat(), "sources": []},
        symbols=["XYZ"], reader_question="", thesis="异动观察", invalidation="",
        observation_provenance={"format": "DATA", "cycle": "old-cycle"})
    draft = store.get(published["draft_id"])
    draft.update(status="SUCCESS", published_at=now.isoformat())
    store.save(draft)
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "recent_formats" in prompt
            assert "DATA" in prompt
            return model(action="SHARE", format="DATA", rationale="Another uncovered mover.",
                body="币安现货数据显示，ABCUSDT 过去24小时明显上涨。\n\n截至 2026-10-10 14:25 UTC 观察（滚动24小时窗口）：\n\n"
                     "• ABCUSDT 最新价格：2.34 USDT\n• 24小时涨幅：+11.5%\n• 24小时成交额：6,000,000 USDT\n\n"
                     "这一波动此前没有被今日的帖子覆盖，涨幅与成交额的配合意味着并非无量冲高，"
                     "后续值得跟踪价格能否在窗口结束后站稳，以及成交是否会快速萎缩。\n\n$ABC",
                symbols=["ABC"], source_ids=["abc-data"], thesis="异动观察", value_focus="reader agency")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True, timely=True,
                     rationale="Concrete new asset data.")
    source = {"evidence_id": "abc-data", "source_kind": "public-api",
              "summary": "ABC rolling 24h +11.5%, turnover 6000000 USDT", "observed_at": now.isoformat()}
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="struct-repeat", now=now, seed_evidence=[source], structured=structured))
    assert result["status"] == "COGNITION_ONLY"
    assert "structure" in result["rationale"]


def test_mind_context_reaches_expression_prompt(tmp_path):
    now = datetime.now(timezone.utc)
    source = {"evidence_id": "sol-news", "title": "Solana staking change",
              "summary": "Solana staking inflows doubled after the network upgrade.", "observed_at": now.isoformat()}
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    prompts = []
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            prompts.append(prompt)
            assert "mind_context" in prompt
            assert "staking inflows doubled" in prompt
            assert "Are staking flows sustainable?" in prompt
            return model(action="SHARE", rationale="Connects to a held belief.",
                body="Solana 升级后质押流入翻倍，这支持我此前对网络升级的判断。", symbols=["SOL"],
                source_ids=["sol-news"], thesis="网络升级与质押流入", reader_question="流入能否持续",
                invalidation="流入回落", value_focus="explanatory_progress")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True, timely=True,
                     rationale="Connects the observation to the agent's held belief.")
    mind = _FakeMind({
        "interests": [{"name": "staking", "salience": 0.8}],
        "open_inquiries": [{"inquiry_id": "i1", "question": "Are staking flows sustainable?", "status": "OPEN"}],
        "beliefs": [{"proposition": "staking inflows doubled after the network upgrade",
                     "stance": 0.6, "confidence": 0.5, "revision_count": 1}],
    })
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer, state_root=tmp_path,
        cycle="mind-ctx", now=now, seed_evidence=[source], structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert prompts and "mind_context" in prompts[0]
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
        return model(facts_specific=True, grounded=True, has_position=False, adds_value=True, timely=True,
                     rationale="Concrete named-asset data is informative without an opinion.")
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


def test_mind_context_carries_positions_and_interest_terrain():
    projection = {
        "beliefs": [{"proposition": "B", "stance": 0.4, "confidence": 0.6}],
        "interests": [
            {"name": "sol-staking", "salience": 1.0, "decayed_salience": 0.9, "preference": 0.7},
            {"name": "eth-btc", "salience": 1.0, "decayed_salience": 0.2, "preference": 0.5},
        ],
        "open_inquiries": [{"question": "q?", "origin": "self_question"}],
        "positions": {
            "open": [{"position_id": "pos-1", "topic": "sol-staking",
                      "proposition": "质押流入持续", "falsifier": "流入回落", "age_days": 1.5}],
            "recent_resolutions": [{"position_id": "pos-0", "topic": "t", "proposition": "p",
                                    "status": "REFUTED", "resolution": "错在 X"}],
        },
    }
    ctx = public_mind_context(_FakeMind(projection))
    assert ctx["positions_open"][0]["position_id"] == "pos-1"
    assert ctx["recent_resolutions"][0]["status"] == "REFUTED"
    assert ctx["interest_terrain"][0]["name"] == "sol-staking"
    assert ctx["interest_terrain"][0]["decayed_salience"] == 0.9


def test_mind_context_without_positions_key_is_empty():
    ctx = public_mind_context(_FakeMind({"beliefs": [], "interests": [], "open_inquiries": []}))
    assert ctx["positions_open"] == []
    assert ctx["recent_resolutions"] == []
    assert ctx["interest_terrain"] == []


def test_mind_context_drops_stale_open_positions():
    projection = {
        "beliefs": [], "interests": [], "open_inquiries": [],
        "positions": {
            "open": [
                {"position_id": "pos-stale", "topic": "t", "proposition": "p", "age_days": 6.0},
                {"position_id": "pos-fresh", "topic": "t", "proposition": "p", "age_days": 1.0},
                {"position_id": "pos-unknown", "topic": "t", "proposition": "p", "age_days": None},
            ],
            "recent_resolutions": [],
        },
    }
    ctx = public_mind_context(_FakeMind(projection))
    assert [row["position_id"] for row in ctx["positions_open"]] == ["pos-fresh", "pos-unknown"]


class _ResolvingMind:
    """FakeMind that records learning-interest calls, backed by a real ledger."""

    def __init__(self, projection, ledger):
        self._projection = projection
        self.positions = ledger
        self.learning_calls = []

    def public_projection(self):
        return self._projection

    def observe_learning_interest(self, *, cycle, topic, reward, evidence_refs):
        self.learning_calls.append({"cycle": cycle, "topic": topic,
                                    "utility": reward.get("utility")})
        return {"status": "OBSERVED", "preference_updated": True}


def _projection_with_open_position():
    return {"beliefs": [], "interests": [], "open_inquiries": [],
            "positions": {"open": [{"position_id": "pos-x", "topic": "sol-staking",
                                    "proposition": "质押流入持续", "falsifier": "流入回落",
                                    "age_days": 2.0}],
                          "recent_resolutions": []}}


def test_position_update_resolves_open_position_and_rewards_topic(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    row = ledger.register(topic="sol-staking", proposition="质押流入持续",
                          falsifier="流入回落", evidence_refs=(), draft_id="seed", now=now)
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "positions_open" in prompt and "pos-x" in prompt
            return model(action="SHARE", rationale="Testing my own commitment.",
                body="SOL 质押过去24小时流入 120,000 SOL，没有回落，我此前判断的证伪条件没有触发，判断维持。",
                symbols=["SOL"], source_ids=["sol-data"],
                thesis="质押流入持续", invalidation="流入回落", value_focus="accountability",
                position_update=model.PositionUpdate(position_id=row["position_id"],
                                                     verdict="CONFIRMED", resolution="流入未回落"))
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="Direct test of the committed position.")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-resolve", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "CONFIRMED"
    assert mind.learning_calls == [{"cycle": "pos-resolve", "topic": "sol-staking", "utility": 1.0}]


def test_position_update_ignored_for_unknown_or_non_open(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="SOL 质押过去24小时流入 120,000 SOL，规模维持。",
                symbols=["SOL"], source_ids=["sol-data"], thesis="t", invalidation="f",
                value_focus="v",
                position_update=model.PositionUpdate(position_id="pos-gone",
                                                     verdict="REFUTED", resolution="x"))
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-gone-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"  # 未知 position 不拦帖
    assert ledger.get("pos-gone") is None
    assert mind.learning_calls == []


def test_new_invalidation_registers_position(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    mind = _ResolvingMind({"beliefs": [], "interests": [], "open_inquiries": [],
                           "positions": {"open": [], "recent_resolutions": []}}, ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
              "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="SOL 质押过去24小时流入 120,000 SOL，判断：流入将维持。",
                symbols=["SOL"], source_ids=["sol-data"], thesis="流入将维持",
                invalidation="流入回落至 50,000 SOL 以下", value_focus="v")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="pos-reg-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    open_rows = ledger.open_positions(now=now + timedelta(minutes=1))
    assert len(open_rows) == 1
    assert open_rows[0]["topic"] == "sol"
    assert open_rows[0]["falsifier"] == "流入回落至 50,000 SOL 以下"


def test_same_asset_three_posts_in_a_row_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    store = SquareDraftStore(tmp_path)
    for i in range(3):
        draft = store.create(
            body=f"XYZ 第{i}条观察，走势与资金数据持续。",
            evidence={"kind": "SOURCE_OBSERVATIONS", "observed_at": now.isoformat(), "sources": []},
            symbols=["XYZ"], reader_question="", thesis="t", invalidation="",
            observation_provenance={"format": "OBSERVATION", "cycle": f"old-{i}"})
        draft.update(status="SUCCESS", published_at=now.isoformat())
        store.save(draft)
    mind = _ResolvingMind({"beliefs": [], "interests": [], "open_inquiries": [],
                           "positions": {"open": [], "recent_resolutions": []}},
                          PositionLedger(tmp_path / "cm"))
    source = {"evidence_id": "xyz-new", "source_kind": "public-api",
              "summary": "XYZ rolling 24h +2%, turnover 3000000 USDT", "observed_at": now.isoformat()}

    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="XYZ 过去24小时上涨 2%，成交额 3,000,000 USDT。",
                symbols=["XYZ"], source_ids=["xyz-new"], thesis="t", invalidation="",
                value_focus="v")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    result = asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle="streak-cycle", now=now, seed_evidence=[source],
        structured=structured, mind=mind))
    assert result["status"] == "COGNITION_ONLY"
    assert "streak" in result["rationale"]


class _RewardFailureMind(_ResolvingMind):
    """Ledger-backed mind whose interest-reward persistence is down."""

    def observe_learning_interest(self, **kwargs):
        raise ValueError("virtualbrain persistence down")


def _seed_source(now, evidence_id="sol-data"):
    return {"evidence_id": evidence_id, "source_kind": "public-api",
            "summary": "SOL staking inflows 120000 SOL over rolling 24h window",
            "observed_at": now.isoformat()}


def _resolving_structured(row):
    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            assert "positions_open" in prompt and "pos-x" in prompt
            return model(action="SHARE", rationale="Testing my own commitment.",
                body="SOL 质押过去24小时流入 120,000 SOL，没有回落，我此前判断的证伪条件没有触发，判断维持。",
                symbols=["SOL"], source_ids=["sol-data"],
                thesis="质押流入持续", invalidation="流入回落", value_focus="accountability",
                position_update=model.PositionUpdate(position_id=row["position_id"],
                                                     verdict="CONFIRMED", resolution="流入未回落"))
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="Direct test of the committed position.")
    return structured


def _run_observation(tmp_path, now, cycle, source, structured, mind):
    async def rss(feeds, now):
        return {"observed_at": now.isoformat(), "items": []}
    explorer = SimpleNamespace(binance_reader=None, wallet_reader=None, feeds=(), rss_fetcher=rss)
    return asyncio.run(prepare_observation(runtime=None, explorer=explorer,
        state_root=tmp_path, cycle=cycle, now=now, seed_evidence=[source],
        structured=structured, mind=mind))


def test_abandoned_race_still_posts_and_registers_new_position(tmp_path):
    # A position can cross the 5-day abandon boundary between projection and
    # transition: the transition must fail without dropping the NEW commitment.
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    row = ledger.register(topic="sol-staking", proposition="质押流入持续",
                          falsifier="流入回落", evidence_refs=(), draft_id="seed",
                          now=now - timedelta(days=6))
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    result = _run_observation(tmp_path, now, "pos-abandoned", _seed_source(now),
                              _resolving_structured(row), mind)
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "ABANDONED"
    open_rows = ledger.open_positions(now=now + timedelta(minutes=1))
    assert [row["topic"] for row in open_rows] == ["sol"]
    assert open_rows[0]["falsifier"] == "流入回落"


def test_corrupt_ledger_state_does_not_block_posting(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    ledger.path.write_bytes(b"\x00\x01 not json at all")
    mind = _ResolvingMind(_projection_with_open_position(), ledger)
    result = _run_observation(tmp_path, now, "pos-corrupt", _seed_source(now),
                              _resolving_structured({"position_id": "pos-x"}), mind)
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert ledger.get("pos-x") is None


def test_empty_invalidation_registers_no_position(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    mind = _ResolvingMind({"beliefs": [], "interests": [], "open_inquiries": [],
                           "positions": {"open": [], "recent_resolutions": []}}, ledger)
    source = {"evidence_id": "sol-data", "source_kind": "public-api",
              "summary": "SOL rolling 24h +12%, turnover 2000000 USDT",
              "observed_at": now.isoformat()}

    async def structured(runtime, prompt, model):
        if model is ObservationExpression:
            return model(action="SHARE", rationale="r",
                body="SOL 过去24小时上涨 12%，成交额 2,000,000 USDT。",
                symbols=["SOL"], source_ids=["sol-data"], thesis="t", invalidation="",
                value_focus="v")
        return model(facts_specific=True, grounded=True, has_position=True, adds_value=True,
                     timely=True, rationale="ok")

    result = _run_observation(tmp_path, now, "pos-empty-inval", source, structured, mind)
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert ledger.open_positions(now=now + timedelta(minutes=1)) == []


def test_reward_failure_does_not_lose_the_transition(tmp_path):
    now = datetime.now(timezone.utc)
    ledger = PositionLedger(tmp_path / "cm")
    row = ledger.register(topic="sol-staking", proposition="质押流入持续",
                          falsifier="流入回落", evidence_refs=(), draft_id="seed", now=now)
    mind = _RewardFailureMind(_projection_with_open_position(), ledger)
    result = _run_observation(tmp_path, now, "pos-reward-fail", _seed_source(now),
                              _resolving_structured(row), mind)
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    stored = ledger.get(row["position_id"])
    assert stored["status"] == "CONFIRMED"
    assert stored["resolution"] == "流入未回落"


def test_public_mind_context_is_shared_entrypoint():
    projection = {
        "beliefs": [], "interests": [], "open_inquiries": [],
        "interest_terrain": [],
        "positions": {"open": [{
            "position_id": "pos-x", "topic": "t", "proposition": "p", "falsifier": "f",
            "age_days": 1.0, "source": "x",
        }], "recent_resolutions": []},
    }
    ctx = public_mind_context(_FakeMind(projection))
    assert ctx["positions_open"][0]["source"] == "x"
