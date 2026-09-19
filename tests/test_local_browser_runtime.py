from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.browser_runtime import BrowserRuntimeUnavailable, browser_runtime_status
from wechat_traffic_agent.x_browser import (
    XBrowserPublicationUnverified,
    XBrowserPublishResult,
    XBrowserTransportRouter,
)
from wechat_traffic_agent.x_browser_mentions import LocalHarnessXMentionsObserver
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
from wechat_traffic_agent.x_outcomes import LocalHarnessXMetricsTransport


NOW = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)


class _MentionsBrowser:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def evaluate(self, expression: str):
        if "AppTabBar_Profile_Link" in expression:
            return "/ferro"
        if "Boolean(document.querySelector" in expression:
            return True
        if "slice(0,50)" in expression:
            return [
                {
                    "text": "  @ferro a useful question  ",
                    "hrefs": ["/other_agent/status/2100486738342764999"],
                },
                {
                    "text": "self post",
                    "hrefs": ["/ferro/status/2100486738342765000"],
                },
            ]
        raise AssertionError(expression)

    def close(self):
        self.closed = True


class _MetricsBrowser:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def evaluate(self, expression: str):
        if ".some(" in expression and "document.querySelectorAll('article')" in expression:
            return True
        if "counter('reply')" in expression:
            return {
                "views": "1.2K",
                "replies": "12 Replies",
                "reposts": "3 reposts",
                "likes": "45 Likes",
            }
        raise AssertionError(expression)

    def close(self):
        self.closed = True


def test_local_mentions_reader_uses_owned_browser_and_records_bounded_telemetry(tmp_path):
    observer = LocalHarnessXMentionsObserver(
        state_root=tmp_path,
        browser_factory=_MentionsBrowser,
        timeout_ms=100,
    )
    rows = asyncio.run(observer.observe(now=NOW, max_results=20))
    assert rows == [{
        "id": "2100486738342764999",
        "text": "@ferro a useful question",
        "username": "other_agent",
        "url": "https://x.com/other_agent/status/2100486738342764999",
    }]
    raw = (tmp_path / "browser/runtime-events.jsonl").read_text()
    event = json.loads(raw)
    assert event["provider"] == "LOCAL_CDP"
    assert event["task_type"] == "X_MENTIONS_READ"
    assert "useful question" not in raw


def test_local_metrics_reader_parses_public_counters_without_tinyfish(tmp_path):
    transport = LocalHarnessXMetricsTransport(
        state_root=tmp_path,
        browser_factory=_MetricsBrowser,
        timeout_ms=100,
    )
    result = asyncio.run(transport.collect(
        url="https://x.com/ferro/status/2100486738342764999"
    ))
    assert result.views == 1200
    assert result.replies == 12
    assert result.reposts == 3
    assert result.likes == 45
    raw = (tmp_path / "browser/runtime-events.jsonl").read_text()
    assert "X_PUBLIC_METRICS_READ" in raw
    assert "1.2K" not in raw


class _LocalUnavailable:
    async def publish(self, *, text: str):
        raise BrowserRuntimeUnavailable("local browser is not ready")


class _LocalUnverified:
    async def publish(self, *, text: str):
        raise XBrowserPublicationUnverified("write may already have reached X")


class _TinyFishSuccess:
    def __init__(self, calls: list[str]):
        self.calls = calls

    async def publish(self, *, text: str):
        self.calls.append(text)
        return XBrowserPublishResult(
            post_id="2100486738342764999",
            url="https://x.com/ferro/status/2100486738342764999",
            handle="ferro",
            provider="TINYFISH",
        )


def test_x_write_router_falls_back_only_before_local_write_is_ambiguous(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishSuccess(calls)),
    )
    result = asyncio.run(XBrowserTransportRouter(state_root=tmp_path).publish(text="hello"))
    assert result.provider == "TINYFISH"
    assert calls == ["hello"]


def test_x_write_router_never_cross_provider_retries_unverified_write(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnverified()),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishSuccess(calls)),
    )
    with pytest.raises(XBrowserPublicationUnverified):
        asyncio.run(XBrowserTransportRouter(state_root=tmp_path).publish(text="hello"))
    assert calls == []


def test_browser_runtime_status_exposes_local_cdp_separately_from_jev(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    status = browser_runtime_status()
    providers = {row["provider"]: row for row in status["providers"]}
    assert "LOCAL_CDP" in providers
    assert "JEV" in providers
    assert providers["JEV"]["status"] == "NOT_CONFIGURED"
