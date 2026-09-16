"""Official X MCP capability provider for the persistent Content Agent.

The provider talks to X's hosted MCP endpoint through the official ``xurl mcp``
bridge. It never handles passwords, browser cookies, or simulated browser input.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
import select
import shlex
import subprocess
import time
from typing import Any, Mapping

X_MCP_ENDPOINT = "https://api.x.com/mcp"
PROVIDER_ID = "x-official-mcp"
READ_CAPABILITIES = {
    "x.identity": "get_users_me",
    "x.timeline": "get_users_timeline",
    "x.mentions": "get_users_mentions",
    "x.search": "search_posts_all",
    "x.user_search": "search_users",
    "x.bookmarks": "get_users_bookmarks",
    "x.trends": "get_trends_by_woeid",
    "x.news": "search_news",
    "x.user_posts": "get_users_posts",
}
# transport, target, permission. Prefer hosted MCP where it actually exposes the action;
# otherwise use the official xurl OAuth2 command, never browser automation.
WRITE_CAPABILITIES = {
    "x.post": ("xurl", "post", "social.publish"),
    "x.reply": ("xurl", "reply", "social.publish"),
    "x.bookmark": ("mcp", "create_users_bookmark", "social.interact"),
    "x.like": ("xurl", "like", "social.interact"),
    "x.repost": ("xurl", "repost", "social.interact"),
    "x.follow": ("xurl", "follow", "social.interact"),
}

# Deliberately absent: deletePosts, DMs, block, mute, unfollow and their inverses.
BLOCKED_TOOL_NAMES = frozenset({
    "delete_users_bookmark", "send_chat_message", "mark_chat_conversation_read",
    "create_activity_subscription", "update_activity_subscription", "delete_activity_subscription",
    "create_webhooks", "delete_webhooks",
})


def x_enabled() -> bool:
    return os.environ.get("CONTENT_X_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def x_writes_enabled() -> bool:
    return os.environ.get("CONTENT_X_WRITES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class XMcpError(RuntimeError):
    pass
class XMcpClient:
    """Small synchronous MCP stdio client around the official xurl bridge."""

    def __init__(self, *, endpoint: str = X_MCP_ENDPOINT, timeout_seconds: float = 30.0) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = max(2.0, float(timeout_seconds))

    def _command(self) -> list[str]:
        configured = os.environ.get("CONTENT_X_MCP_COMMAND", "").strip()
        if configured:
            return [*shlex.split(configured), self.endpoint]
        return ["xurl", "mcp", self.endpoint]

    def _spawn(self) -> subprocess.Popen[str]:
        try:
            return subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=os.environ.copy(),
            )
        except OSError as exc:
            raise XMcpError(f"unable to start official xurl MCP bridge: {exc}") from exc

    @staticmethod
    def _send(proc: subprocess.Popen[str], payload: Mapping[str, Any]) -> None:
        if proc.stdin is None:
            raise XMcpError("xurl MCP stdin is unavailable")
        proc.stdin.write(json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False) + "\n")
        proc.stdin.flush()
    def _read_response(self, proc: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
        if proc.stdout is None:
            raise XMcpError("xurl MCP stdout is unavailable")
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            ready, _, _ = select.select([proc.stdout], [], [], min(0.25, max(0.0, deadline - time.monotonic())))
            if not ready:
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                if message.get("error"):
                    raise XMcpError("X MCP error: " + json.dumps(message["error"], ensure_ascii=False)[:1200])
                result = message.get("result")
                return result if isinstance(result, dict) else {"value": result}
        detail = ""
        if proc.stderr is not None and proc.poll() is not None:
            detail = proc.stderr.read()[-2000:]
        raise XMcpError(("X MCP bridge stopped before a response" if proc.poll() is not None else "X MCP response timed out") + (": " + detail if detail else ""))

    def _initialize(self, proc: subprocess.Popen[str]) -> None:
        self._send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "livingruntime-content-agent", "version": "1.0"},
        }})
        self._read_response(proc, 1)
        self._send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _list_tools_in_session(self, proc: subprocess.Popen[str]) -> dict[str, dict[str, Any]]:
        self._send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = self._read_response(proc, 2)
        rows = result.get("tools") if isinstance(result, dict) else None
        tools: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                tools[str(row["name"])] = deepcopy(row)
        return tools

    def list_tools(self) -> dict[str, dict[str, Any]]:
        proc = self._spawn()
        try:
            self._initialize(proc)
            return self._list_tools_in_session(proc)
        finally:
            self._close(proc)

    def call_tool(self, name: str, payload: Mapping[str, Any], *, capability_id: str) -> dict[str, Any]:
        if name in BLOCKED_TOOL_NAMES:
            raise PermissionError(f"X MCP tool is outside the approved surface: {name}")
        proc = self._spawn()
        try:
            self._initialize(proc)
            tools = self._list_tools_in_session(proc)
            if name not in tools:
                raise LookupError(f"official X MCP tool unavailable: {name}")
            schema = tools[name].get("inputSchema") if isinstance(tools[name], dict) else {}
            arguments = _shape_arguments(capability_id, dict(payload), schema if isinstance(schema, dict) else {})
            self._send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": name, "arguments": arguments,
            }})
            result = self._read_response(proc, 3)
            if result.get("isError"):
                raise XMcpError("X MCP tool failed: " + json.dumps(result, ensure_ascii=False)[:1600])
            return {
                "tool": name,
                "arguments": _redact_arguments(arguments),
                "data": _decode_tool_result(result),
            }
        finally:
            self._close(proc)

    @staticmethod
    def _close(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass



class XurlCommandClient:
    """Official xurl OAuth2 shortcuts for write actions absent from hosted xMCP."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.binary = os.environ.get("CONTENT_X_XURL_BINARY", "xurl").strip() or "xurl"
        self.app = os.environ.get("CONTENT_X_APP_NAME", "content-agent").strip() or "content-agent"
        self.username = os.environ.get("CONTENT_X_USERNAME", "").strip()

    def call(self, capability_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(payload)
        command: list[str]
        if capability_id == "x.post":
            text = str(args.get("text") or "").strip()
            if not text:
                raise ValueError("x.post requires text")
            command = ["post", text]
        elif capability_id == "x.reply":
            post_id = str(args.get("post_id") or args.get("tweet_id") or "").strip()
            text = str(args.get("text") or "").strip()
            if not post_id or not text:
                raise ValueError("x.reply requires post_id and text")
            command = ["reply", post_id, text]
        elif capability_id in {"x.like", "x.repost"}:
            post_id = str(args.get("post_id") or args.get("tweet_id") or "").strip()
            if not post_id:
                raise ValueError(f"{capability_id} requires post_id")
            command = [capability_id.removeprefix("x."), post_id]
        elif capability_id == "x.follow":
            username = str(args.get("target_username") or args.get("username") or "").strip().lstrip("@")
            if not username:
                raise ValueError("x.follow requires target_username")
            command = ["follow", username]
        else:
            raise LookupError(f"unsupported official xurl write capability: {capability_id}")
        full = [self.binary, *command, "--app", self.app, "--auth", "oauth2"]
        if self.username:
            full.extend(["--username", self.username])
        try:
            proc = subprocess.run(full, capture_output=True, text=True, timeout=self.timeout_seconds, env=os.environ.copy())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise XMcpError(f"official xurl write failed to execute: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-1600:]
            raise XMcpError("official xurl write failed" + ((": " + detail) if detail else ""))
        raw = (proc.stdout or "").strip()
        try:
            data: Any = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"stdout": raw[:4000]}
        return {"transport": "official-xurl", "command": command[0], "data": data}

def _decode_tool_result(result: Mapping[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list):
        return dict(result)
    values: list[Any] = []
    for row in content:
        if not isinstance(row, dict) or row.get("type") != "text":
            continue
        text = str(row.get("text") or "")
        try:
            values.append(json.loads(text))
        except json.JSONDecodeError:
            values.append(text)
    if len(values) == 1:
        return values[0]
    return values if values else dict(result)


def _redact_arguments(arguments: Any) -> Any:
    if isinstance(arguments, dict):
        out = {}
        for key, value in arguments.items():
            if any(token in key.lower() for token in ("token", "secret", "password", "authorization")):
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact_arguments(value)
        return out
    if isinstance(arguments, list):
        return [_redact_arguments(value) for value in arguments]
    return arguments


def _norm(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _candidate_value(name: str, payload: Mapping[str, Any], capability_id: str) -> Any:
    key = _norm(name)
    aliases = {
        "query": ("query",), "maxresults": ("max_results", "limit"), "paginationtoken": ("pagination_token",),
        "username": ("username", "handle"), "woeid": ("woeid",), "text": ("text",),
        "targetuserid": ("target_user_id", "target_id"),
    }
    for source_key in aliases.get(key, (name,)):
        if source_key in payload and payload[source_key] is not None:
            return payload[source_key]
    if key in {"id", "userid"}:
        return payload.get("user_id") or payload.get("id")
    if key in {"tweetid", "postid"}:
        return payload.get("post_id") or payload.get("tweet_id")
    if key in {"inreplytotweetid", "inreplytopostid"}:
        return payload.get("post_id") or payload.get("tweet_id") or payload.get("in_reply_to_id")
    if key == "reply" and capability_id == "x.reply":
        return {"in_reply_to_tweet_id": payload.get("post_id") or payload.get("tweet_id")}
    return None


def _fill_schema(schema: Mapping[str, Any], payload: Mapping[str, Any], capability_id: str) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(props, dict):
        return dict(payload)
    result: dict[str, Any] = {}
    for name, raw_schema in props.items():
        spec = raw_schema if isinstance(raw_schema, dict) else {}
        direct = _candidate_value(str(name), payload, capability_id)
        if direct is not None:
            result[str(name)] = direct
            continue
        if spec.get("type") == "object" or isinstance(spec.get("properties"), dict):
            nested = _fill_schema(spec, payload, capability_id)
            if nested:
                result[str(name)] = nested
    return result


def _shape_arguments(capability_id: str, payload: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("arguments")
    if isinstance(raw, dict):
        return deepcopy(raw)
    shaped = _fill_schema(schema, payload, capability_id)
    if shaped:
        return shaped
    # Older xMCP builds occasionally omit a usable schema. Keep the fallback bounded and explicit.
    allowed = {"query", "max_results", "pagination_token", "username", "woeid", "text",
               "user_id", "post_id", "target_user_id"}
    return {key: deepcopy(value) for key, value in payload.items() if key in allowed and value is not None}


def _capability_row(capability_id: str, *, side_effect: str, permission: str | None = None) -> dict[str, Any]:
    row = {
        "capability_id": capability_id,
        "description": f"Official X MCP capability: {capability_id}",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "side_effect_level": side_effect,
    }
    if permission:
        row["required_permissions"] = [permission]
    return row


class XCapabilityProvider:
    """Agent Runtime provider using only official X MCP/API tooling."""
    def __init__(self, client: XMcpClient | None = None, write_client: XurlCommandClient | None = None) -> None:
        self.client = client or XMcpClient(
            endpoint=os.environ.get("CONTENT_X_MCP_ENDPOINT", X_MCP_ENDPOINT).strip() or X_MCP_ENDPOINT,
            timeout_seconds=float(os.environ.get("CONTENT_X_MCP_TIMEOUT_SECONDS", "30")),
        )
        self.write_client = write_client or XurlCommandClient(
            timeout_seconds=float(os.environ.get("CONTENT_X_MCP_TIMEOUT_SECONDS", "30")),
        )

    def manifest(self) -> dict[str, Any]:
        rows = [_capability_row(capability_id, side_effect="READ_ONLY") for capability_id in READ_CAPABILITIES]
        if x_writes_enabled():
            rows.extend(
                _capability_row(capability_id, side_effect="EXTERNAL_SIDE_EFFECT", permission=permission)
                for capability_id, (_transport, _target, permission) in WRITE_CAPABILITIES.items()
            )
        return {
            "provider": PROVIDER_ID,
            "version": "1.1",
            "description": "Official X hosted MCP for reads plus official xurl/OAuth2 for API writes not exposed by hosted MCP; destructive actions excluded.",
            "capabilities": rows,
        }

    @staticmethod
    def _read_defaults(capability_id: str, args: dict[str, Any]) -> dict[str, Any]:
        out = dict(args)
        if capability_id == "x.identity":
            out.setdefault("arguments", {"user.fields": "id,name,username,profile_image_url,public_metrics,description,created_at"})
        elif capability_id == "x.search":
            out.setdefault("sort_order", "recency")
            out.setdefault("post.fields", "id,text,author_id,created_at,conversation_id,public_metrics")
        elif capability_id in {"x.timeline", "x.mentions", "x.bookmarks", "x.user_posts"}:
            out.setdefault("post.fields", "id,text,author_id,created_at,conversation_id,public_metrics")
        elif capability_id == "x.user_search":
            out.setdefault("user.fields", "id,name,username,profile_image_url,public_metrics,description")
        return out

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(payload or {})
        try:
            if capability_id in READ_CAPABILITIES:
                tool = READ_CAPABILITIES[capability_id]
                result = self.client.call_tool(tool, self._read_defaults(capability_id, args), capability_id=capability_id)
            elif capability_id in WRITE_CAPABILITIES:
                if not x_writes_enabled():
                    return {"ok": False, "status": "WRITE_DISABLED", "capability_id": capability_id}
                transport, target, _permission = WRITE_CAPABILITIES[capability_id]
                if transport == "mcp":
                    result = self.client.call_tool(target, args, capability_id=capability_id)
                    result["transport"] = "official-x-mcp"
                else:
                    result = self.write_client.call(capability_id, args)
            else:
                return {"ok": False, "status": "UNKNOWN_CAPABILITY", "capability_id": capability_id}
        except (XMcpError, LookupError, ValueError, PermissionError) as exc:
            detail = str(exc)[:1200]
            status = "X_CREDITS_DEPLETED" if "credits depleted" in detail.lower() else "X_API_ERROR"
            return {"ok": False, "status": status, "capability_id": capability_id,
                    "error_type": type(exc).__name__, "detail": detail}
        return {"ok": True, "status": "OK", "capability_id": capability_id, **result}


__all__ = [
    "BLOCKED_TOOL_NAMES", "PROVIDER_ID", "READ_CAPABILITIES", "WRITE_CAPABILITIES",
    "XCapabilityProvider", "XMcpClient", "XMcpError", "XurlCommandClient", "x_enabled", "x_writes_enabled",
]
