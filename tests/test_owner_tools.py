from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.owner_tools import (
    execute_readonly_tool_requests,
    readonly_capability_rows,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.persisted = 0

    def capability_manifest(self):
        return (
            {
                "capability_id": "web.search",
                "description": "Search the public web.",
                "input_schema": {"type": "object"},
                "side_effect_level": "READ_ONLY",
            },
            {
                "capability_id": "x.post",
                "description": "Publish to X.",
                "input_schema": {"type": "object"},
                "side_effect_level": "EXTERNAL_SIDE_EFFECT",
            },
        )
    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        return SimpleNamespace(
            receipt_id="capability-receipt:test:1",
            payload={"ok": True, "echo": dict(payload)},
        )

    def persist(self):
        self.persisted += 1


def test_readonly_rows_exclude_side_effect_capabilities():
    app = SimpleNamespace(runtime=FakeRuntime())

    rows = readonly_capability_rows(app)

    assert [row["capability_id"] for row in rows] == ["web.search"]


def test_execute_readonly_tool_invokes_runtime_and_returns_receipt():
    runtime = FakeRuntime()
    app = SimpleNamespace(runtime=runtime)

    results = execute_readonly_tool_requests(
        app,
        [{"capability_id": "web.search", "payload": {"query": "Ferro"}}],
    )

    assert runtime.calls == [("web.search", {"query": "Ferro", "limit": 8})]
    assert results[0]["ok"] is True
    assert results[0]["receipt_id"] == "capability-receipt:test:1"
    assert runtime.persisted == 1
def test_execute_readonly_tool_rejects_side_effect_and_secret_payload():
    runtime = FakeRuntime()
    app = SimpleNamespace(runtime=runtime)

    results = execute_readonly_tool_requests(
        app,
        [
            {"capability_id": "x.post", "payload": {"text": "hello"}},
            {"capability_id": "web.search", "payload": {"api_key": "secret-value"}},
        ],
    )

    assert runtime.calls == []
    assert results[0]["status"] == "FORBIDDEN_SIDE_EFFECT"
    assert results[1]["status"] == "SENSITIVE_PAYLOAD_REJECTED"


def test_web_search_payload_is_bounded_to_runtime_contract():
    runtime = FakeRuntime()
    app = SimpleNamespace(runtime=runtime)

    execute_readonly_tool_requests(
        app,
        [{
            "capability_id": "web.search",
            "payload": {"query": "Ferro", "limit": 99, "page": 99, "safesearch": 99},
        }],
    )

    assert runtime.calls == [(
        "web.search",
        {"query": "Ferro", "limit": 8, "page": 10, "safesearch": 2},
    )]
