from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.browser_runtime import BrowserRuntimeUnavailable, browser_runtime_status
from wechat_traffic_agent.x_browser import (
    TinyFishProfileUnavailable,
    XBrowserPublicationUnverified,
    XBrowserPublishResult,
    XBrowserTransportRouter,
)
from wechat_traffic_agent.x_browser_mentions import LocalHarnessXMentionsObserver
from wechat_traffic_agent.x_local_browser import AccountJevXTransport, ExistingProfileJevXTransport, LocalHarnessXTransport
from wechat_traffic_agent.x_outcomes import LocalHarnessXMetricsTransport


NOW = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _legacy_llm_mode(monkeypatch):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")


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


class _ExistingSuccess:
    def __init__(self, calls: list[str]):
        self.calls = calls

    async def publish(self, *, text: str):
        self.calls.append(text)
        return XBrowserPublishResult(
            post_id="2100486738342765003",
            url="https://x.com/ferro/status/2100486738342765003",
            handle="ferro",
            provider="JEV_EXISTING_PROFILE",
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


def test_x_write_router_prefers_account_jev_before_tinyfish(tmp_path, monkeypatch):
    jev_calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalSuccess(jev_calls)),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: (_ for _ in ()).throw(
            AssertionError("TinyFish should not be touched")
        )),
    )
    result = asyncio.run(
        XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
    )
    assert result.provider == "LOCAL_CDP"
    assert jev_calls == ["hello"]


def test_x_write_router_prefers_existing_session_jev_before_tinyfish(
    tmp_path, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    monkeypatch.setattr(
        ExistingProfileJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _ExistingSuccess(calls)),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: (_ for _ in ()).throw(
            AssertionError("TinyFish Playwright fallback should not run")
        )),
    )
    result = asyncio.run(
        XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
    )
    assert result.provider == "JEV_EXISTING_PROFILE"
    assert calls == ["hello"]




def test_x_write_router_never_retries_unverified_existing_session_jev(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    monkeypatch.setattr(
        ExistingProfileJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnverified()),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: (_ for _ in ()).throw(
            AssertionError("TinyFish retry would risk a duplicate JEV write")
        )),
    )
    with pytest.raises(XBrowserPublicationUnverified):
        asyncio.run(
            XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
        )

def test_x_write_router_falls_back_to_tinyfish_only_when_both_jev_paths_cannot_start(
    tmp_path, monkeypatch
):
    tinyfish_calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    monkeypatch.setattr(
        ExistingProfileJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: _TinyFishSuccess(tinyfish_calls)),
    )
    result = asyncio.run(
        XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
    )
    assert result.provider == "TINYFISH"
    assert tinyfish_calls == ["hello"]


def test_x_write_router_never_cross_provider_retries_unverified_account_jev_write(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnverified()),
    )
    from wechat_traffic_agent.x_browser import TinyFishXTransport
    monkeypatch.setattr(
        TinyFishXTransport,
        "from_environment",
        classmethod(lambda cls: (_ for _ in ()).throw(
            AssertionError("TinyFish retry would risk a duplicate write")
        )),
    )
    with pytest.raises(XBrowserPublicationUnverified):
        asyncio.run(
            XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
        )


def test_x_write_router_falls_back_to_legacy_local_when_account_and_tinyfish_unavailable(
    tmp_path, monkeypatch
):
    local_calls: list[str] = []
    monkeypatch.setenv("CONTENT_LOCAL_X_BROWSER_ENABLED", "true")
    monkeypatch.setenv("CONTENT_TINYFISH_X_ENABLED", "true")
    monkeypatch.setattr(
        AccountJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
    monkeypatch.setattr(
        ExistingProfileJevXTransport,
        "from_environment",
        classmethod(lambda cls, state_root: _LocalUnavailable()),
    )
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
    result = asyncio.run(
        XBrowserTransportRouter(state_root=tmp_path).publish(text="hello")
    )
    assert result.provider == "LOCAL_CDP"
    assert local_calls == ["hello"]



def test_account_jev_x_transport_uses_registered_account_profile(tmp_path, monkeypatch):
    record = SimpleNamespace(
        account_id="acct_x_123456789abc",
        provider_id="x",
        identity="ferro",
    )

    class Registry:
        def list(self, *, provider_id=None):
            assert provider_id == "x"
            return [record]

        def get(self, account_id):
            assert account_id == record.account_id
            return record

    class Profiles:
        def __init__(self):
            self.stopped = []

        def ensure(self, account_id):
            assert account_id == record.account_id
            return SimpleNamespace(cdp_ws="ws://127.0.0.1:3000/")

        def stop(self, account_id):
            self.stopped.append(account_id)

    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "POSTED",
                "elapsed_ms": 18,
                "action_count": 1,
                "post_id": "2100486738342765002",
                "url": "https://x.com/ferro/status/2100486738342765002",
                "handle": "ferro",
                "duplicate_found": False,
            }) + "\n",
            stderr="",
        )

    monkeypatch.delenv("CONTENT_X_ACCOUNT_ID", raising=False)
    transport = AccountJevXTransport(
        state_root=tmp_path,
        registry=Registry(),
        profile_manager=Profiles(),
        runner=runner,
    )
    result = asyncio.run(transport.publish(text="hello"))
    assert result.provider == "JEV_STEEL"
    assert result.handle == "ferro"
    command, kwargs = calls[0]
    assert command[-1] == "wechat_traffic_agent.account_x_publish_worker"
    assert kwargs["env"]["BU_CDP_WS"] == "ws://127.0.0.1:3000/"
    assert "BU_CDP_URL" not in kwargs["env"]
    assert kwargs["env"]["BU_NAME"].startswith("x_")
    request = json.loads(kwargs["input"])
    assert request["expected_handle"] == "ferro"
    assert request["text"] == "hello"

    raw = (tmp_path / "browser/runtime-events.jsonl").read_text()
    event = json.loads(raw)
    assert event["provider"] == "JEV_STEEL"
    assert event["account_id"] == record.account_id
    assert "hello" not in raw


def test_account_jev_x_transport_requires_explicit_account_when_multiple_exist(
    tmp_path, monkeypatch
):
    class Registry:
        def list(self, *, provider_id=None):
            return [
                SimpleNamespace(account_id="acct_x_aaaaaaaaaaaa", provider_id="x", identity="a"),
                SimpleNamespace(account_id="acct_x_bbbbbbbbbbbb", provider_id="x", identity="b"),
            ]

    class Profiles:
        def ensure(self, account_id):
            raise AssertionError("profile must not start before account selection")

    monkeypatch.delenv("CONTENT_X_ACCOUNT_ID", raising=False)
    transport = AccountJevXTransport(
        state_root=tmp_path,
        registry=Registry(),
        profile_manager=Profiles(),
    )
    with pytest.raises(BrowserRuntimeUnavailable, match="CONTENT_X_ACCOUNT_ID"):
        asyncio.run(transport.publish(text="hello"))



def test_existing_profile_jev_reconciles_ambiguous_write_in_fresh_session(
    tmp_path, monkeypatch
):
    record = SimpleNamespace(
        account_id="acct_x_existing1234",
        provider_id="x",
        identity="ferro",
    )

    class Registry:
        def list(self, *, provider_id=None):
            assert provider_id == "x"
            return [record]

    class Client:
        def __init__(self):
            self.starts = []
            self.saved = []
            self.cancelled = []

        def start_setup(self, *, url, timeout_seconds):
            self.starts.append((url, timeout_seconds))
            index = len(self.starts)
            return {
                "session_id": f"session-{index}",
                "cdp_url": f"wss://example.invalid/{index}",
            }

        def save(self, *, session_id):
            self.saved.append(session_id)
            return {"status": "SAVED"}

        def cancel(self, *, session_id):
            self.cancelled.append(session_id)
            return {"status": "CANCELLED"}
    client = Client()
    calls = []

    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append((command, kwargs, request))
        if request["mode"] == "publish":
            body = {
                "status": "PUBLICATION_UNVERIFIED",
                "attempted": True,
                "action_count": 1,
                "message": "clicked but URL not visible yet",
            }
        else:
            body = {
                "status": "POSTED",
                "action_count": 0,
                "post_id": "2101310183855333514",
                "url": "https://x.com/ferro/status/2101310183855333514",
                "handle": "ferro",
                "duplicate_found": False,
            }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(body) + "\n",
            stderr="",
        )

    monkeypatch.delenv("CONTENT_X_ACCOUNT_ID", raising=False)
    transport = ExistingProfileJevXTransport(
        state_root=tmp_path,
        client=client,
        registry=Registry(),
        runner=runner,
    )
    result = asyncio.run(transport.publish(text="hello"))
    assert result.provider == "JEV_EXISTING_PROFILE"
    assert result.post_id == "2101310183855333514"
    assert [item[2]["mode"] for item in calls] == ["publish", "reconcile"]
    assert calls[0][1]["env"]["BU_CDP_WS"] == "wss://example.invalid/1"
    assert calls[1][1]["env"]["BU_CDP_WS"] == "wss://example.invalid/2"
    assert "BU_CDP_URL" not in calls[0][1]["env"]
    assert client.saved == ["session-1"]
    assert client.cancelled == ["session-2"]

    event = json.loads(
        (tmp_path / "browser/runtime-events.jsonl").read_text()
    )
    assert event["provider"] == "JEV_EXISTING_PROFILE"
    assert event["status"] == "COMPLETED"
    assert event["action_count"] == 1


def test_existing_profile_jev_prewrite_failure_is_safe_to_fallback(
    tmp_path, monkeypatch
):
    record = SimpleNamespace(
        account_id="acct_x_existing1234",
        provider_id="x",
        identity="ferro",
    )

    class Registry:
        def list(self, *, provider_id=None):
            return [record]
    class Client:
        def __init__(self):
            self.cancelled = []

        def start_setup(self, **kwargs):
            return {
                "session_id": "session-1",
                "cdp_url": "wss://example.invalid/1",
            }

        def save(self, **kwargs):
            raise AssertionError("pre-write failure must not save")

        def cancel(self, *, session_id):
            self.cancelled.append(session_id)
            return {}

    client = Client()

    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps({
                "status": "SESSION_UNAVAILABLE",
                "attempted": False,
                "action_count": 0,
                "error_type": "NOT_AUTHENTICATED",
            }) + "\n",
            stderr="",
        )

    monkeypatch.delenv("CONTENT_X_ACCOUNT_ID", raising=False)
    transport = ExistingProfileJevXTransport(
        state_root=tmp_path,
        client=client,
        registry=Registry(),
        runner=runner,
    )
    with pytest.raises(BrowserRuntimeUnavailable):
        asyncio.run(transport.publish(text="hello"))
    assert client.cancelled == ["session-1"]

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



def test_local_x_auth_waits_for_react_profile_link(tmp_path):
    class DelayedProfileBrowser:
        def __init__(self):
            self.selector_checks = 0

        def evaluate(self, expression: str):
            if "Boolean(document.querySelector" in expression:
                self.selector_checks += 1
                return self.selector_checks >= 3
            if "AppTabBar_Profile_Link" in expression:
                return "/ferro"
            raise AssertionError(expression)

    transport = LocalHarnessXTransport(
        state_root=tmp_path,
        timeout_ms=100,
        browser_factory=None,
    )
    browser = DelayedProfileBrowser()

    assert transport._authenticated_handle(browser) == "ferro"
    assert browser.selector_checks >= 3


def test_local_x_fill_exact_uses_browser_context_insertion(tmp_path):
    class EditorBrowser:
        def __init__(self):
            self.expression = ""

        def evaluate(self, expression: str):
            self.expression = expression
            return True

        def call(self, method: str, **params):
            raise AssertionError("remote CDP Input.insertText must not be required")

    browser = EditorBrowser()
    LocalHarnessXTransport._fill_exact(browser, "中文 Ferro")

    assert "execCommand('insertText'" in browser.expression
    assert json.dumps("中文 Ferro") in browser.expression


def test_local_x_click_uses_browser_context_button_click():
    class ButtonBrowser:
        def __init__(self):
            self.expression = ""

        def evaluate(self, expression: str):
            self.expression = expression
            return True

        def call(self, method: str, **params):
            raise AssertionError("remote CDP mouse events must not be required")

    browser = ButtonBrowser()
    LocalHarnessXTransport._click(browser, 1.0, 1.0)

    assert "tweetButton" in browser.expression
    assert ".click()" in browser.expression
