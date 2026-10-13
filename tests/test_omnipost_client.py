"""omnipost MCP 客户端：协议握手、错误语义、结果映射。"""
import json

import httpx
import pytest

from wechat_traffic_agent.platforms.omnipost_client import (
    OmnipostError, call_tool, map_omnipost_result, omnipost_publish,
)


def sse(payload, session="session-1"):
    return httpx.Response(200, headers={
        "content-type": "text/event-stream",
        "mcp-session-id": session,
    }, text=f"event: message\ndata: {json.dumps(payload)}\n\n")


def mcp_app(calls, *, tool_error=False, rpc_error=False, http_error=False):
    def handler(request):
        if http_error:
            return httpx.Response(500, text="container restarting")
        message = json.loads(request.content)
        session = request.headers.get("mcp-session-id")
        method = message.get("method")
        if method == "initialize":
            calls.append((method, session))
            return sse({"jsonrpc": "2.0", "id": 1,
                        "result": {"protocolVersion": "2025-03-26"}})
        if method == "notifications/initialized":
            calls.append((method, session))
            return httpx.Response(202)
        if method == "tools/call":
            calls.append((method, session))
            if rpc_error:
                return sse({"jsonrpc": "2.0", "id": 2,
                            "error": {"code": -32602, "message": "unknown tool"}})
            if tool_error:
                return sse({"jsonrpc": "2.0", "id": 2, "result": {
                    "content": [{"type": "text", "text": "适配器崩了"}],
                    "isError": True}})
            text = json.dumps({"job_id": "job-1", "results": {"binance_square": {
                "platform": "binance_square", "status": "ok",
                "url": "https://www.binance.com/square/post/77",
                "ref": "77", "message": ""}}})
            return sse({"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}})
        return sse({"jsonrpc": "2.0", "id": 99,
                    "error": {"code": -32601, "message": "no such method"}})
    return httpx.MockTransport(handler)


def test_publish_full_handshake_with_session_echo():
    calls = []
    payload = omnipost_publish(title="标题", markdown="正文", platforms=["binance_square"],
                               transport=mcp_app(calls))
    assert payload["job_id"] == "job-1"
    assert payload["results"]["binance_square"]["status"] == "ok"
    assert [method for method, _ in calls] == [
        "initialize", "notifications/initialized", "tools/call"]
    assert calls[0][1] is None
    assert calls[1][1] == "session-1"
    assert calls[2][1] == "session-1"


def test_omnipost_publish_dry_run_passthrough():
    seen = {}
    def handler(request):
        message = json.loads(request.content)
        if message.get("method") == "tools/call":
            seen.update(message["params"]["arguments"])
        return sse({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "{}"}], "isError": False}})
    omnipost_publish(title="t", markdown="m", platforms=["x"], dry_run=True,
                     transport=httpx.MockTransport(handler))
    assert seen["dry_run"] is True
    seen.clear()
    omnipost_publish(title="t", markdown="m", platforms=["x"],
                     transport=httpx.MockTransport(handler))
    assert "dry_run" not in seen


def test_jsonrpc_error_raises():
    with pytest.raises(OmnipostError, match="工具调用失败"):
        call_tool("nope", {}, transport=mcp_app([], rpc_error=True))


def test_tool_iserror_raises():
    with pytest.raises(OmnipostError, match="工具报错"):
        call_tool("boom", {}, transport=mcp_app([], tool_error=True))


def test_http_error_raises():
    with pytest.raises(OmnipostError, match="HTTP 500"):
        call_tool("publish", {}, transport=mcp_app([], http_error=True))


def test_missing_data_frame_raises():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="event: ping\n\n")
    with pytest.raises(OmnipostError, match="data 帧"):
        call_tool("publish", {}, transport=httpx.MockTransport(handler))


def test_plain_json_response_accepted():
    def handler(request):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": json.dumps({"answer": 42})}],
            "isError": False}})
    assert call_tool("anything", {},
                     transport=httpx.MockTransport(handler)) == {"answer": 42}


def test_list_tool_reads_structured_content():
    """mcp 2.x 把 list 返回值拆成多个 content 块，完整 list 只在 structuredContent。"""
    def handler(request):
        return sse({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [
                {"type": "text", "text": json.dumps({"job_id": "a"})},
                {"type": "text", "text": json.dumps({"job_id": "b"})},
            ],
            "isError": False,
            "structuredContent": {"result": [{"job_id": "a"}, {"job_id": "b"}]}}})
    rows = call_tool("publish_history", {"limit": 2},
                     transport=httpx.MockTransport(handler))
    assert rows == [{"job_id": "a"}, {"job_id": "b"}]


def test_map_ok_to_success():
    mapped = map_omnipost_result({"status": "ok", "url": "https://x", "ref": "77",
                                  "message": ""})
    assert mapped == {"status": "SUCCESS", "post_id": "77", "url": "https://x"}


def test_map_skipped_to_unverified():
    mapped = map_omnipost_result({"status": "skipped", "url": None, "ref": None,
                                  "message": "24h 内同内容已发布过"})
    assert mapped["status"] == "UNVERIFIED"
    assert "防重" in mapped["error"]


def test_map_unverified_failure_stays_unverified():
    message = "unverified：网络结果未知，人工核实前勿重发"
    mapped = map_omnipost_result({"status": "failed", "url": None, "ref": None,
                                  "message": message})
    assert mapped == {"status": "UNVERIFIED", "error": message}


def test_map_rejected_and_unknown_failure_to_rejected():
    assert map_omnipost_result({"status": "rejected", "url": None, "ref": None,
                                "message": "HTTP 403"}) == {
        "status": "REJECTED", "error": "HTTP 403"}
    assert map_omnipost_result({}) == {"status": "REJECTED", "error": "omnipost failed"}
