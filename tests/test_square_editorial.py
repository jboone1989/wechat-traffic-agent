from datetime import datetime, timedelta, timezone
import json

import pytest

from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore, SquareDraftWorker
from wechat_traffic_agent.platforms.omnipost_client import OmnipostError
from test_binance_market_structure import market_report

NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def prepared(root):
    store = SquareDraftStore(root)
    evidence = market_report()
    doc = store.create(body="ETH 下跌是否代表空头加仓？需要同时核对价格和 OI。$BTC $ETH",
                       evidence=evidence, symbols=["BTC", "ETH"],
                       reader_question="是否加仓", thesis="证据不足", invalidation="同步价跌OI升")
    return store, doc


def review(store, doc):
    return store.review(doc["draft_id"], reviewer="Ferro editor",
                        evidence_check="字段、币种与时间一致；没有把OI当空头量", now=NOW)


def omnipost_worker(root, monkeypatch, results):
    """替换 omnipost_publish 观察入参，不碰网络。"""
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    calls = []
    def fake_publish(*, title, markdown, platforms, min_interval_minutes=None):
        calls.append({"title": title, "markdown": markdown, "platforms": platforms,
                      "min_interval": min_interval_minutes})
        return {"job_id": "job-1", "results": results}
    monkeypatch.setattr(
        "wechat_traffic_agent.platforms.omnipost_client.omnipost_publish", fake_publish)
    return SquareDraftWorker(root), calls


def test_omnipost_unreviewed_draft_never_posts(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok", "url": "u", "ref": "1", "message": ""}})
    assert run.run_once(now=NOW) is None
    assert calls == []


@pytest.mark.parametrize("mutation", ["copy", "evidence", "expiry", "future"])
def test_changed_or_expired_evidence_requires_new_review(tmp_path, monkeypatch, mutation):
    store, doc = prepared(tmp_path)
    doc = review(store, doc)
    now = NOW
    if mutation == "copy":
        doc["body"] += "空头爆仓"
        store.save(doc)
    elif mutation == "evidence":
        path = store.evidence / (doc["evidence_sha256"] + ".json")
        evidence = json.loads(path.read_text())
        evidence["status"] = "INCOMPLETE"
        path.write_text(json.dumps(evidence))
    elif mutation == "expiry":
        now += timedelta(minutes=21)
    else:
        now -= timedelta(seconds=1)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok", "url": "u", "ref": "1", "message": ""}})
    assert run.run_once(now=now) is None
    assert calls == []
    assert store.get(doc["draft_id"])["status"] == "NEEDS_REVIEW"


def test_omnipost_publishing_claim_never_republished(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    doc["status"] = "PUBLISHING"
    store.save(doc)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok", "url": "u", "ref": "1", "message": ""}})
    assert run.run_once(now=NOW) is None
    assert calls == []


def test_omnipost_via_posts_and_records_receipt(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok",
        "url": "https://www.binance.com/square/post/77", "ref": "77", "message": ""}})
    result = run.run_once(now=NOW)
    assert result["status"] == "SUCCESS"
    assert result["post_id"] == "77"
    assert result["url"] == "https://www.binance.com/square/post/77"
    assert result["published_at"]
    assert calls == [{"title": doc["body"][:40], "markdown": doc["body"],
                      "platforms": ["binance_square"], "min_interval": None}]
    assert run.run_once(now=NOW) is None
    assert len(calls) == 1


def test_omnipost_min_interval_parsed_from_env(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    monkeypatch.setenv("CONTENT_SQUARE_OMNIPOST_MIN_INTERVAL", "x:10,wechat:2.5")
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok",
        "url": "https://www.binance.com/square/post/78", "ref": "78", "message": ""}})
    assert run.omnipost_min_interval == {"x": 10.0, "wechat": 2.5}
    run.run_once(now=NOW)
    assert calls[0]["min_interval"] == {"x": 10.0, "wechat": 2.5}


def test_omnipost_bad_min_interval_env_degrades_to_empty(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    monkeypatch.setenv("CONTENT_SQUARE_OMNIPOST_MIN_INTERVAL", "x:oops")
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "ok",
        "url": "https://www.binance.com/square/post/79", "ref": "79", "message": ""}})
    assert run.omnipost_min_interval == {}


def test_omnipost_dedup_skip_maps_unverified(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "skipped",
        "url": None, "ref": None, "message": "24h 内同内容已发布过；force=true 可强制"}})
    result = run.run_once(now=NOW)
    assert result["status"] == "UNVERIFIED"
    assert "防重" in result["error"]
    assert "post_id" not in result


def test_omnipost_rejected_maps_rejected(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {"binance_square": {
        "platform": "binance_square", "status": "rejected",
        "url": None, "ref": None, "message": "rejected：HTTP 403"}})
    result = run.run_once(now=NOW)
    assert result["status"] == "REJECTED"
    assert "403" in result["error"]


def test_omnipost_transport_failure_is_unverified_never_retried(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    def boom(*, title, markdown, platforms, min_interval_minutes=None):
        raise OmnipostError("omnipost HTTP 500: container restarting")
    monkeypatch.setattr(
        "wechat_traffic_agent.platforms.omnipost_client.omnipost_publish", boom)
    run = SquareDraftWorker(tmp_path)
    result = run.run_once(now=NOW)
    assert result["status"] == "UNVERIFIED"
    assert "500" in result["error"]
    assert run.run_once(now=NOW) is None
    assert store.get(doc["draft_id"])["status"] == "UNVERIFIED"


def test_omnipost_missing_platform_result_maps_rejected(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, calls = omnipost_worker(tmp_path, monkeypatch, {})
    result = run.run_once(now=NOW)
    assert result["status"] == "REJECTED"
    assert result["error"] == "omnipost failed"
