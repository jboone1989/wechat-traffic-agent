from __future__ import annotations

from wechat_traffic_agent.x_mcp import (
    BLOCKED_TOOL_NAMES,
    READ_CAPABILITIES,
    WRITE_CAPABILITIES,
    XCapabilityProvider,
    XMcpClient,
    XMcpError,
    XurlCommandClient,
)


class _Mcp:
    def __init__(self, *, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    def call_tool(self, name, payload, *, capability_id):
        self.calls.append((name, dict(payload), capability_id))
        if self.fail:
            raise self.fail
        return {"tool": name, "arguments": dict(payload), "data": {"data": [{"id": "1", "text": "x"}]}}


class _Xurl:
    def __init__(self):
        self.calls = []

    def call(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        return {"transport": "official-xurl", "command": capability_id, "data": {"data": {"id": "42"}}}


def test_read_capabilities_match_real_hosted_xmcp_names(monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    assert READ_CAPABILITIES["x.identity"] == "get_users_me"
    assert READ_CAPABILITIES["x.search"] == "search_posts_all"
    assert READ_CAPABILITIES["x.mentions"] == "get_users_mentions"
    assert READ_CAPABILITIES["x.bookmarks"] == "get_users_bookmarks"
    manifest = XCapabilityProvider(_Mcp(), _Xurl()).manifest()
    ids = {row["capability_id"] for row in manifest["capabilities"]}
    assert set(READ_CAPABILITIES) <= ids
    assert not (set(WRITE_CAPABILITIES) & ids)


def test_publication_only_mode_exposes_post_and_reply_but_not_social_interactions(monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    mcp, xurl = _Mcp(), _Xurl()
    provider = XCapabilityProvider(mcp, xurl)

    ids = {row["capability_id"] for row in provider.manifest()["capabilities"]}
    assert {"x.post", "x.reply"} <= ids
    assert not ({"x.bookmark", "x.like", "x.repost", "x.follow"} & ids)

    post = provider.invoke("x.post", {"text": "bounded publication"})
    reply = provider.invoke("x.reply", {"post_id": "9", "text": "thread follow-up"})
    bookmark = provider.invoke("x.bookmark", {"user_id": "u1", "post_id": "9"})

    assert post["ok"] is True
    assert reply["ok"] is True
    assert bookmark["ok"] is False
    assert bookmark["status"] == "WRITE_DISABLED"
    assert [row[0] for row in xurl.calls] == ["x.post", "x.reply"]
    assert mcp.calls == []


def test_write_surface_is_explicit_and_destructive_actions_absent(monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    manifest = XCapabilityProvider(_Mcp(), _Xurl()).manifest()
    ids = {row["capability_id"] for row in manifest["capabilities"]}
    assert set(WRITE_CAPABILITIES) <= ids
    assert "x.delete" not in ids
    assert "x.dm" not in ids
    assert "x.block" not in ids
    assert "x.mute" not in ids
    assert "x.unfollow" not in ids
    assert "send_chat_message" in BLOCKED_TOOL_NAMES
    assert "delete_users_bookmark" in BLOCKED_TOOL_NAMES


def test_post_and_reply_use_official_xurl_when_hosted_mcp_lacks_them(monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "true")
    mcp, xurl = _Mcp(), _Xurl()
    provider = XCapabilityProvider(mcp, xurl)
    post = provider.invoke("x.post", {"text": "hello"})
    reply = provider.invoke("x.reply", {"post_id": "9", "text": "reply"})
    assert post["ok"] and reply["ok"]
    assert [row[0] for row in xurl.calls] == ["x.post", "x.reply"]
    assert mcp.calls == []


def test_bookmark_prefers_hosted_mcp(monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    mcp, xurl = _Mcp(), _Xurl()
    provider = XCapabilityProvider(mcp, xurl)
    result = provider.invoke("x.bookmark", {"user_id": "u1", "post_id": "p1"})
    assert result["ok"] is True
    assert mcp.calls[0][0] == "create_users_bookmark"
    assert xurl.calls == []


def test_credits_depleted_is_a_receiptable_structured_failure(monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    provider = XCapabilityProvider(_Mcp(fail=XMcpError('credits depleted')), _Xurl())
    result = provider.invoke("x.mentions", {"user_id": "u1"})
    assert result["ok"] is False
    assert result["status"] == "X_CREDITS_DEPLETED"
    assert result["error_type"] == "XMcpError"


def test_search_uses_real_mcp_tool_and_recency_defaults(monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    mcp = _Mcp()
    result = XCapabilityProvider(mcp, _Xurl()).invoke("x.search", {"query": "bitcoin", "max_results": 10})
    assert result["ok"] is True
    name, payload, capability = mcp.calls[0]
    assert name == "search_posts_all"
    assert capability == "x.search"
    assert payload["query"] == "bitcoin"
    assert payload["sort_order"] == "recency"


def test_official_x_clients_bind_explicit_oauth_user_per_account(monkeypatch):
    monkeypatch.delenv("CONTENT_X_MCP_COMMAND", raising=False)
    mcp = XMcpClient(
        endpoint="https://api.x.com/mcp",
        app="content-agent",
        username="@second_x",
    )
    assert mcp._command() == [
        "xurl",
        "mcp",
        "https://api.x.com/mcp",
        "--app",
        "content-agent",
        "--username",
        "second_x",
    ]

    writer = XurlCommandClient(app="content-agent", username="@second_x")
    assert writer.app == "content-agent"
    assert writer.username == "second_x"


def test_x_capability_provider_can_be_bound_from_account_record():
    from wechat_traffic_agent.account_registry import AccountRecord

    account = AccountRecord(
        account_id="acct_x_123456789abc",
        provider_id="x",
        identity="second_x",
        display_label="@second_x",
        oauth_app="content-agent",
        oauth_user="second_x",
        oauth_status="CONNECTED",
    )
    provider = XCapabilityProvider.for_account(account)

    assert provider.client.app == "content-agent"
    assert provider.client.username == "second_x"
    assert provider.write_client.username == "second_x"
