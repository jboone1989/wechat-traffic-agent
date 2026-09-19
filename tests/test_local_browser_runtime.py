from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.browser_runtime import BrowserRuntimeUnavailable, browser_runtime_status
from wechat_traffic_agent.x_browser import (
    TinyFishProfileUnavailable,
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
        if "counter('reply')" in expression:
            return {
                "views": "1.2K",
                "replies": "12 Replies",
                "reposts": "3 reposts",
                "likes": "45 Likes",
            }
        if ".some(" in expression and "document.querySelectorAll('article')" in expression:
            return True
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


class _LocalSuccess:
    def __init__(self, calls: list[str]):
        self.calls = calls

    async def publish(self, *, text: str):
        self.calls.append(text)
        return XBrowserPublishResult(
            post_id="2100486738342765001",
            url="https://x.com/ferro/status/2100486738342765001",
            handle="ferro",
            provider="LOCAL_CDP",
        )


class _TinyFishUnavailable:
    async def publish(self, *, text: str):
        raise TinyFishProfileUnavailable("TinyFish profile unavailable before write")


class _TinyFishUnverified:
    async def publish(self, *, text: str):
        raise XBrowserPublicationUnverified("TinyFish write may already have reached X")


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


def test_x_write_router_prefers_tinyfish_before_local_browser(tmp_path, monkeypatch):
    tinyfish_calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: (_ for _ in ()).throw(AssertionError("local browser should not be touched"))),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishSuccess(tinyfish_calls)),
    )
    result = asyncio.run(XBrowserTransportRouter(state_root=tmp_path).publish(text="hello"))
    assert result.provider == "TINYFISH"
    assert tinyfish_calls == ["hello"]


def test_x_write_router_falls_back_to_local_only_when_tinyfish_cannot_start(tmp_path, monkeypatch):
    local_calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishUnavailable()),
    )
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalSuccess(local_calls)),
    )
    result = asyncio.run(XBrowserTransportRouter(state_root=tmp_path).publish(text="hello"))
    assert result.provider == "LOCAL_CDP"
    assert local_calls == ["hello"]


def test_x_write_router_never_cross_provider_retries_unverified_tinyfish_write(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishUnverified()),
    )
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: (_ for _ in ()).throw(AssertionError("local retry would risk duplicate write"))),
    )
    with pytest.raises(XBrowserPublicationUnverified):
        asyncio.run(XBrowserTransportRouter(state_root=tmp_path).publish(text="hello"))


def test_browser_runtime_status_exposes_local_cdp_separately_from_jev(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    status = browser_runtime_status()
    providers = {row["provider"]: row for row in status["providers"]}
    assert "LOCAL_CDP" in providers
    assert "JEV" in providers
    assert providers["JEV"]["status"] == "NOT_CONFIGURED"


def test_x_mentions_router_prefers_tinyfish_over_local_browser(tmp_path, monkeypatch):
    from wechat_traffic_agent.x_browser_mentions import (
        TinyFishXMentionsObserver,
        XBrowserMentionsObserverRouter,
    )

    class TinyFishMentions:
        async def observe(self, *, now, max_results=20):
            return [{"id": "1", "text": "ok", "username": "u", "url": "https://x.com/u/status/1"}]

    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        TinyFishXMentionsObserver,
        "from_environment",
        classmethod(lambda cls: TinyFishMentions()),
    )
    monkeypatch.setattr(
        LocalHarnessXMentionsObserver,
        "from_environment",
        classmethod(lambda cls, state_root: (_ for _ in ()).throw(AssertionError("local mentions should not be touched"))),
    )

    rows = asyncio.run(
        XBrowserMentionsObserverRouter(state_root=tmp_path).observe(now=NOW, max_results=1)
    )
    assert rows[0]["id"] == "1"


def test_x_metrics_router_prefers_tinyfish_over_local_browser(tmp_path, monkeypatch):
    from wechat_traffic_agent.x_outcomes import (
        TinyFishXMetricsTransport,
        XMetricsTransportRouter,
        XPublicMetrics,
    )

    class TinyFishMetrics:
        async def collect(self, *, url: str):
            return XPublicMetrics(views=7, replies=1, reposts=0, likes=2)

    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        TinyFishXMetricsTransport,
        "from_environment",
        classmethod(lambda cls: TinyFishMetrics()),
    )
    monkeypatch.setattr(
        LocalHarnessXMetricsTransport,
        "from_environment",
        classmethod(lambda cls, state_root: (_ for _ in ()).throw(AssertionError("local metrics should not be touched"))),
    )

    result = asyncio.run(
        XMetricsTransportRouter(state_root=tmp_path).collect(
            url="https://x.com/ferro/status/1"
        )
    )
    assert result.views == 7

