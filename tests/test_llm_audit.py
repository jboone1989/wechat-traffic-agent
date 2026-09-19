from __future__ import annotations

import asyncio

from wechat_traffic_agent.llm_audit import LLMRequestAuditStore, audited_provider


class FakeProvider:
    model = "fake-model"

    async def complete(self, prompt, **kwargs):
        return "reply:" + prompt

    async def stream(self, prompt, **kwargs):
        yield "reply:"
        yield prompt

    async def structured(self, prompt, schema, **kwargs):
        return {"reply": prompt, "goal_candidate": None, "memory_candidates": []}


def test_audited_provider_preserves_type_and_records_call(tmp_path):
    provider = FakeProvider()
    audited = audited_provider(provider, tmp_path, scope="test")

    assert audited is provider
    assert isinstance(audited, FakeProvider)
    assert asyncio.run(audited.complete("hello", system="owner", max_tokens=20)) == "reply:hello"

    rows = LLMRequestAuditStore(tmp_path).recent()
    assert rows[0]["scope"] == "test"
    assert rows[0]["operation"] == "complete"
    assert rows[0]["prompt"] == "hello"
    assert rows[0]["system"] == "owner"
    assert rows[0]["response"] == "reply:hello"
    assert rows[0]["status"] == "OK"


def test_audit_redacts_common_secret_shapes(tmp_path):
    provider = audited_provider(FakeProvider(), tmp_path)
    asyncio.run(provider.complete("api_key='secret-value'", system="token='other-secret'"))

    row = LLMRequestAuditStore(tmp_path).recent()[0]
    assert "secret-value" not in row["prompt"]
    assert "other-secret" not in row["system"]
    assert "[REDACTED]" in row["prompt"]


def test_audit_rolls_old_rows_when_storage_limit_is_crossed(tmp_path):
    store = LLMRequestAuditStore(tmp_path, max_rows=100, max_bytes=1024 * 1024)
    payload = "x" * 120_000
    for index in range(12):
        store.append({"status": "OK", "prompt": str(index), "response": payload})

    rows = store.recent(limit=100)
    assert rows
    assert rows[0]["prompt"] == "11"
    assert len(rows) < 12
    assert store.path.stat().st_size <= 1024 * 1024


def test_audited_provider_records_native_stream_and_first_token(tmp_path):
    provider = audited_provider(FakeProvider(), tmp_path, scope="owner-chat")

    async def consume():
        return [chunk async for chunk in provider.stream("hello", system="owner")]

    assert asyncio.run(consume()) == ["reply:", "hello"]
    row = LLMRequestAuditStore(tmp_path).recent()[0]
    assert row["scope"] == "owner-chat"
    assert row["operation"] == "stream"
    assert row["response"] == "reply:hello"
    assert row["status"] == "OK"
    assert row["first_token_ms"] is not None


def test_audited_provider_records_structured_repair_scope(tmp_path):
    provider = audited_provider(FakeProvider(), tmp_path, scope="content-agent")

    result = asyncio.run(provider.structured(
        "repair me",
        {"type": "object"},
        system="owner",
        _audit_scope="operator-private-chat-repair",
    ))

    assert result["reply"] == "repair me"
    row = LLMRequestAuditStore(tmp_path).recent()[0]
    assert row["scope"] == "operator-private-chat-repair"
    assert row["operation"] == "structured"
    assert row["status"] == "OK"
    assert "repair me" in row["response"]


def test_owner_audit_redacts_invalid_protocol_stream_body(tmp_path):
    provider = audited_provider(FakeProvider(), tmp_path, scope="content-agent")

    async def consume():
        return [
            chunk async for chunk in provider.stream(
                "hello",
                _audit_scope="operator-private-chat",
                _audit_response_mode="owner_envelope",
            )
        ]

    assert asyncio.run(consume()) == ["reply:", "hello"]
    row = LLMRequestAuditStore(tmp_path).recent()[0]
    assert row["scope"] == "operator-private-chat"
    assert row["response"] == "[INVALID_OWNER_ENVELOPE_REDACTED]"
