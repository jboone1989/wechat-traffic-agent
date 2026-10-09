from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from wechat_traffic_agent.platforms.binance_square import BinanceSquareClient
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore, SquareDraftWorker
from test_binance_market_structure import market_report

NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def prepared(root):
    store = SquareDraftStore(root)
    evidence = market_report()
    doc = store.create(body="ETH 下跌是否代表空头加仓？需要同时核对价格和 OI。$BTC $ETH",
                       evidence=evidence, symbols=["BTC", "ETH"],
                       reader_question="是否加仓", thesis="证据不足", invalidation="同步价跌OI升")
    return store, doc


def worker(root, monkeypatch, response):
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_ENABLED", "1")
    monkeypatch.setenv("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "1")
    sent = []
    def handle(request):
        sent.append(json.loads(request.content)["bodyTextOnly"])
        return response
    client = BinanceSquareClient(key="test", client=httpx.Client(transport=httpx.MockTransport(handle)))
    return SquareDraftWorker(root, client=client), sent


def review(store, doc):
    return store.review(doc["draft_id"], reviewer="Ferro editor",
                        evidence_check="字段、币种与时间一致；没有把OI当空头量", now=NOW)


def test_unreviewed_draft_never_posts(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    run, sent = worker(tmp_path, monkeypatch, httpx.Response(200))
    assert run.run_once(now=NOW) is None
    assert sent == []


def test_reviewed_copy_posts_exactly_once(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, sent = worker(tmp_path, monkeypatch, httpx.Response(200, json={"code": "000000", "data": {"id": "123"}}))
    result = run.run_once(now=NOW)
    assert result["status"] == "SUCCESS" and result["post_id"] == "123"
    assert sent == [doc["body"]]
    assert run.run_once(now=NOW) is None


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
    run, sent = worker(tmp_path, monkeypatch, httpx.Response(200))
    assert run.run_once(now=now) is None
    assert sent == []
    assert json.loads(store.path(doc["draft_id"]).read_text())["status"] == "NEEDS_REVIEW"


def test_unknown_receipt_and_crash_claim_are_never_retried(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    doc = review(store, doc)
    run, sent = worker(tmp_path, monkeypatch, httpx.Response(504, text="unknown"))
    assert run.run_once(now=NOW)["status"] == "UNVERIFIED"
    assert run.run_once(now=NOW) is None
    assert len(sent) == 1
    doc["status"] = "PUBLISHING"
    store.save(doc)
    assert run.run_once(now=NOW) is None


def test_missing_key_does_not_claim_reviewed_copy(tmp_path, monkeypatch):
    store, doc = prepared(tmp_path)
    review(store, doc)
    run, sent = worker(tmp_path, monkeypatch, httpx.Response(200))
    run.client.key = ""
    assert run.run_once(now=NOW) is None
    assert sent == []
    assert json.loads(store.path(doc["draft_id"]).read_text())["status"] == "REVIEWED"
