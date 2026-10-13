import json
import logging
from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.genre_scheduler import (
    DEFAULT_POLICY, _posting_zone, decide_genre, genre_pipeline_enabled, load_policy, policy_path,
)
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
from wechat_traffic_agent.square_observation import GENRE_INSTRUCTIONS

ZONE = timezone(timedelta(hours=8))


def test_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("CONTENT_GENRE_PIPELINE_ENABLED", raising=False)
    assert genre_pipeline_enabled() is False
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    assert genre_pipeline_enabled() is True
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "yes")
    assert genre_pipeline_enabled() is True
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "0")
    assert genre_pipeline_enabled() is False


def test_missing_policy_file_returns_builtin_defaults(tmp_path):
    policy = load_policy(tmp_path)
    assert policy == DEFAULT_POLICY
    assert set(DEFAULT_POLICY["genres"]) == {
        "quick_take", "data_digest", "engagement",
        "deep_take", "explainer", "story_review"}


def test_policy_path_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_policy.json"
    custom.write_text(json.dumps({"genres": {"quick_take": {"weight": 1.0}}}), encoding="utf-8")
    monkeypatch.setenv("CONTENT_GENRE_POLICY_PATH", str(custom))
    assert policy_path(tmp_path) == custom
    assert load_policy(tmp_path)["genres"] == {"quick_take": {"weight": 1.0}}


def test_corrupt_policy_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "square_editorial").mkdir()
    (tmp_path / "square_editorial" / "genre_policy.json").write_text("{not json", encoding="utf-8")
    assert load_policy(tmp_path) == DEFAULT_POLICY


def test_disabled_gate_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("CONTENT_GENRE_PIPELINE_ENABLED", raising=False)
    now = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
    assert decide_genre(tmp_path, now, [], zone=ZONE) is None


def test_non_object_policy_json_falls_back_to_defaults(tmp_path):
    (tmp_path / "square_editorial").mkdir()
    (tmp_path / "square_editorial" / "genre_policy.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_policy(tmp_path) == DEFAULT_POLICY


def test_corrupt_policy_warns_but_missing_file_is_silent(tmp_path, caplog):
    import logging
    (tmp_path / "square_editorial").mkdir()
    (tmp_path / "square_editorial" / "genre_policy.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        load_policy(tmp_path)
    assert any("GENRE_POLICY_INVALID" in r.message for r in caplog.records)
    caplog.clear()
    load_policy(tmp_path / "nonexistent-root")
    assert not any("GENRE_POLICY_INVALID" in r.message for r in caplog.records)


def test_posting_zone_falls_back_to_utc8(monkeypatch, tmp_path):
    zone = _posting_zone(tmp_path)
    assert datetime(2026, 10, 13, tzinfo=zone).utcoffset() == timedelta(hours=8)


def _seed_published(tmp_path, *, genre, when):
    store = SquareDraftStore(tmp_path)
    row = store.create(body=f"测试正文 {genre} " + "内容" * 20,
        evidence={"kind": "SOURCE_OBSERVATIONS", "observed_at": when.isoformat(), "sources": []},
        symbols=["SOL"], reader_question="", thesis="测试", invalidation="",
        observation_provenance={"format": "OBSERVATION", "genre": genre, "cycle": "seed"})
    row.update(status="SUCCESS", published_at=when.isoformat())
    store.save(row)


def test_quota_force_after_local_midday_when_deep_unmet(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)  # 北京 21:00
    decision = decide_genre(tmp_path, now, [], zone=ZONE)
    assert decision is not None
    assert decision.genre in ("deep_take", "explainer", "story_review")
    assert decision.depth_tier == "deep"
    assert decision.trace["reason"] == "quota_force"


def test_no_force_before_local_midday(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)  # 北京 11:00
    decision = decide_genre(tmp_path, now, [], zone=ZONE)
    assert decision.trace["reason"] == "weighted_draw"
    assert decision.genre in DEFAULT_POLICY["genres"]


def test_cooldown_excludes_recently_used_genre(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)
    recent = [{"observation_provenance": {"genre": "quick_take"}}]
    decision = decide_genre(tmp_path, now, recent, zone=ZONE)
    assert "quick_take" in decision.trace["rejected_by_cooldown"]
    assert "quick_take" not in [c["genre"] for c in decision.trace["candidates"]]


def test_daily_max_excludes_saturated_genre(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)
    local_noon = now.astimezone(ZONE)
    for i in range(2):  # create 按 body+evidence 去重，两次种子需内容可区分且仍属今天
        _seed_published(tmp_path, genre="engagement", when=local_noon + timedelta(minutes=i))
    decision = decide_genre(tmp_path, now, [], zone=ZONE)
    assert "engagement" not in [c["genre"] for c in decision.trace["candidates"]]
    assert decision.trace["counts"]["engagement"] == 2


def test_vol_boost_amplifies_quick_take_when_market_hot(monkeypatch, tmp_path):
    from wechat_traffic_agent.sources.live_cache import live_cache_root
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)
    baseline = decide_genre(tmp_path, now, [], zone=ZONE)
    base_weight = next(c["effective_weight"] for c in baseline.trace["candidates"]
                       if c["genre"] == "quick_take")
    cache = live_cache_root(tmp_path) / "binance_alt_market" / "latest.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": now.isoformat(), "rows": [
        {"symbol": "DOGEUSDT", "priceChangePercent": "9.5"},
        {"symbol": "PEPEUSDT", "priceChangePercent": "-8.1"},
    ]}), encoding="utf-8")
    hot = decide_genre(tmp_path, now, [], zone=ZONE)
    hot_weight = next(c["effective_weight"] for c in hot.trace["candidates"]
                      if c["genre"] == "quick_take")
    assert abs(hot_weight - base_weight * 2.0) < 1e-6
    assert hot.trace["volatility"] is not None and hot.trace["volatility"] > 5.0


def test_deep_quota_counts_only_today_success_posts(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_GENRE_PIPELINE_ENABLED", "1")
    now = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
    _seed_published(tmp_path, genre="deep_take", when=now.astimezone(ZONE))
    _seed_published(tmp_path, genre="deep_take",
                    when=datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc))  # 昨天，不算
    decision = decide_genre(tmp_path, now, [], zone=ZONE)
    assert decision.trace["counts"]["deep_take"] == 1
    assert decision.trace["deep_done"] == 1


def test_scalar_deep_daily_and_non_dict_genre_fall_back(tmp_path, caplog):
    (tmp_path / "square_editorial").mkdir()
    policy_file = tmp_path / "square_editorial" / "genre_policy.json"
    policy_file.write_text(json.dumps(
        {"deep_daily": 2, "genres": {"quick_take": 3,
                                     "deep_take": {"weight": 2.5, "daily_min": 1,
                                                   "daily_max": 3, "cooldown_posts": 2}}}),
        encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        policy = load_policy(tmp_path)  # 形状废掉的字段回退默认，不崩但告警
    assert policy["deep_daily"] == DEFAULT_POLICY["deep_daily"]  # {"min": 2, "max": 4}
    assert policy["genres"]["quick_take"] == DEFAULT_POLICY["genres"]["quick_take"]
    assert policy["genres"]["deep_take"]["weight"] == 2.5  # 合法条目仍取文件值
    assert any("GENRE_POLICY_INVALID_GENRE_ENTRY" in r.message and "quick_take" in r.message
               and str(policy_file) in r.message for r in caplog.records)
    assert any("GENRE_POLICY_INVALID_DEEP_DAILY" in r.message
               and str(policy_file) in r.message for r in caplog.records)


def test_unknown_genre_key_warns_but_known_genres_loaded(tmp_path, caplog):
    (tmp_path / "square_editorial").mkdir()
    genres = {key: dict(row) for key, row in DEFAULT_POLICY["genres"].items()}
    genres["quick_take"]["weight"] = 9.0
    genres["meme"] = {"weight": 5.0, "daily_max": 1}
    (tmp_path / "square_editorial" / "genre_policy.json").write_text(
        json.dumps({"genres": genres}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        policy = load_policy(tmp_path)
    assert any("GENRE_POLICY_UNKNOWN_GENRES" in r.message and "meme" in r.message
               for r in caplog.records)
    assert policy["genres"]["quick_take"]["weight"] == 9.0  # 已知体裁仍从文件加载
    assert set(DEFAULT_POLICY["genres"]) <= set(policy["genres"])


def test_scheduler_genres_match_prompt_instructions():
    assert set(DEFAULT_POLICY["genres"]) == set(GENRE_INSTRUCTIONS)
