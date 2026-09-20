from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.owner_tools import (
    execute_readonly_tool_requests,
    readonly_capability_rows,
)
from wechat_traffic_agent.memory_procedural import ProceduralMemoryService


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


class FlakyRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("transient read-only failure")
        return SimpleNamespace(
            receipt_id="capability-receipt:test:retry",
            payload={"ok": True, "attempt": self.attempts},
        )


def test_owner_tool_executes_selected_retry_skill_variant(tmp_path):
    service = ProceduralMemoryService(tmp_path)
    direct = service._initial_variant(
        preconditions=[
            "authenticated owner conversation",
            "capability present in owner_chat_readonly_runtime_capabilities",
        ],
        capabilities=["web.search"],
        steps=["invoke web.search directly"],
        success_count=1,
        failure_count=2,
        evidence_refs=["receipt:direct"],
        policy_action="DIRECT",
    )
    retry = service._initial_variant(
        preconditions=[
            "authenticated owner conversation",
            "capability present in owner_chat_readonly_runtime_capabilities",
        ],
        capabilities=["web.search"],
        steps=["invoke web.search", "retry once after a bounded safe failure"],
        success_count=3,
        failure_count=0,
        evidence_refs=["receipt:retry"],
        policy_action="RETRY_ONCE_SAFE",
    )
    procedure = service.learn_procedure(
        name="owner-readonly:web.search",
        description="Use web.search with evidence-backed skill variants.",
        preconditions=[
            "authenticated owner conversation",
            "capability present in owner_chat_readonly_runtime_capabilities",
        ],
        capabilities=["web.search"],
        steps=["invoke web.search directly"],
        verification=["require receipt"],
        source_type="VERIFIED_EXECUTION_OUTCOME",
        source_ref="receipt:seed",
        evidence_refs=["receipt:seed"],
        success_count=4,
        failure_count=2,
        variants=[direct, retry],
        default_variant_id=direct["variant_id"],
    )
    runtime = FlakyRuntime()
    app = SimpleNamespace(runtime=runtime)
    results = execute_readonly_tool_requests(
        app,
        [{"capability_id": "web.search", "payload": {"query": "Ferro"}}],
        state_root=tmp_path,
    )
    assert results[0]["ok"] is True
    assert results[0]["attempt_count"] == 2
    assert results[0]["procedure_id"] == procedure["memory_id"]
    assert results[0]["procedure_variant_id"] == retry["variant_id"]
    assert results[0]["procedure_policy_action"] == "RETRY_ONCE_SAFE"
    assert len(runtime.calls) == 2
