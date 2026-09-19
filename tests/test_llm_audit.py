from __future__ import annotations

import asyncio

from wechat_traffic_agent.llm_audit import LLMRequestAuditStore, audited_provider


class FakeProvider:
    model = "fake-model"

    async def complete(self, prompt, **kwargs):
        return "reply:" + prompt


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
