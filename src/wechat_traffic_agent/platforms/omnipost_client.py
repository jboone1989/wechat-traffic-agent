"""ferro → omnipost 的最小 MCP 客户端（streamable-http，零新依赖）。

对端是同机 docker 容器 omnipost（我们自己的服务，协议面窄且版本自持）：
initialize → notifications/initialized → tools/call，响应实测为 SSE 单帧。
刻意不做连接/会话保活——每次发布独立短连接（教训：跨调用复用连接是
omnipost 侧 "Event loop is closed" 故障的根源；localhost 上新建连接成本可忽略）。
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8399/mcp"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class OmnipostError(RuntimeError):
    """MCP 传输层/协议层失败。"""


def _sse_or_json(response: httpx.Response) -> dict:
    if not response.content.strip():
        return {}
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        return response.json()
    payload = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
    if payload is None:
        raise OmnipostError(f"omnipost 响应缺少 data 帧：HTTP {response.status_code}")
    return payload


def _post(client: httpx.Client, url: str, session: str | None,
          message: dict) -> tuple[dict, str | None]:
    headers = dict(_HEADERS)
    if session:
        headers["Mcp-Session-Id"] = session
    response = client.post(url, json=message, headers=headers)
    if response.status_code >= 400:
        raise OmnipostError(
            f"omnipost HTTP {response.status_code}: {response.text[:200]}")
    return _sse_or_json(response), response.headers.get("mcp-session-id")


def call_tool(tool: str, arguments: dict, *, url: str | None = None,
              timeout: float = 25.0,
              transport: httpx.BaseTransport | None = None) -> Any:
    """调一次 MCP 工具，返回 content[0].text 的 JSON 解析结果（非 JSON 则原文）。"""
    endpoint = url or os.environ.get("OMNIPOST_MCP_URL", DEFAULT_URL)
    with httpx.Client(timeout=timeout, transport=transport) as client:
        init, session = _post(client, endpoint, None, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "ferro-square-worker",
                                      "version": "1.0"}}})
        if "result" not in init:
            raise OmnipostError(f"omnipost initialize 失败：{init}")
        _post(client, endpoint, session,
              {"jsonrpc": "2.0", "method": "notifications/initialized"})
        payload, _ = _post(client, endpoint, session, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}})
    if "error" in payload:
        raise OmnipostError(f"omnipost 工具调用失败：{payload['error']}")
    result = payload.get("result") or {}
    if result.get("isError"):
        raise OmnipostError(f"omnipost 工具报错：{result.get('content')}")
    # mcp 2.x 把 list 返回值拆成多个 content 块，完整 list 只在 structuredContent 里
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    content = (result.get("content") or [{}])[0]
    text = content.get("text")
    if text is None:
        return result.get("structuredContent")
    try:
        return json.loads(text)
    except ValueError:
        return text


def omnipost_publish(*, title: str, markdown: str, platforms: list[str],
                     url: str | None = None,
                     min_interval_minutes: dict[str, float] | None = None,
                     dry_run: bool = False,
                     transport: httpx.BaseTransport | None = None) -> dict:
    """发布并返回 publish 工具的完整结果（{job_id, results: {平台: {...}}}）。

    min_interval_minutes 形如 {"x": 10}：该平台两次真实成功至少隔 N 分钟（平台侧
    限速），间隔不足的平台返回 skipped。dry_run=True 只出计划不真发（冒烟必带，
    教训：这层漏传过一次导致冒烟文本真发到生产 X 号）。传输层失败抛
    OmnipostError——调用方必须当作"结果未知"处理，禁自动重发。
    """
    arguments = {"title": title, "markdown": markdown, "platforms": platforms}
    if min_interval_minutes:
        arguments["min_interval_minutes"] = min_interval_minutes
    if dry_run:
        arguments["dry_run"] = True
    return call_tool("publish", arguments, url=url, transport=transport)


def map_omnipost_result(platform_result: dict) -> dict:
    """把 omnipost 单平台结果映射进 SquareDraftWorker 的 doc 字段。

    unverified 类失败一律 UNVERIFIED（可能已发出，禁自动重发）——与
    BinanceSquareClient 直连时代的语义逐条对应。
    """
    status = platform_result.get("status")
    message = platform_result.get("message") or ""
    if status == "ok":
        return {"status": "SUCCESS", "post_id": platform_result.get("ref"),
                "url": platform_result.get("url")}
    if status == "skipped":
        return {"status": "UNVERIFIED",
                "error": f"omnipost 24h 防重拦截（{message}）；内容可能已发布，人工核对"}
    if "unverified" in message.lower():
        return {"status": "UNVERIFIED", "error": message}
    return {"status": "REJECTED", "error": message or "omnipost failed"}
