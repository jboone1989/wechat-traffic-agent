from __future__ import annotations

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities import MemoryCapabilityClient

from wechat_traffic_agent.memory_service import MemoryService
from wechat_traffic_agent.runtime_memory_adapter import (
    LegacyFerroMemoryCapabilityProvider,
    PROVIDER_ID,
)


def _runtime(tmp_path, service):
    runtime = ApplicationRuntime.bootstrap(
        tmp_path / "runtime",
        subject_id="ferro:test",
        max_side_effect_level="LOCAL_STATE",
    )
    runtime.register_provider(
        LegacyFerroMemoryCapabilityProvider(service, subject_id="ferro:test")
    )
    return runtime


def test_runtime_memory_capability_matches_direct_legacy_recall(tmp_path):
    service = MemoryService(tmp_path / "memory")
    service.store.append_memory(
        {
            "memory_id": "fact:checkpoint",
            "memory_type": "SEMANTIC",
            "kind": "FACT",
            "text": "A checkpoint restores worker state after restart.",
            "topics": ["recovery"],
        }
    )
    service.sync()

    direct = service.search("checkpoint", topic="recovery", limit=4)
    runtime = _runtime(tmp_path, service)
    through_runtime = MemoryCapabilityClient(runtime).recall(
        "checkpoint", topic="recovery", limit=4
    )

    assert [row["node_id"] for row in through_runtime] == [
        row["node_id"] for row in direct
    ]
    assert runtime.receipts[-1]["provider"] == PROVIDER_ID


def test_runtime_memory_capability_can_remember_and_read_without_ferro_callers(tmp_path):
    service = MemoryService(tmp_path / "memory")
    client = MemoryCapabilityClient(_runtime(tmp_path, service))

    result = client.remember(
        {
            "memory_id": "fact:new",
            "memory_type": "SEMANTIC",
            "kind": "FACT",
            "text": "Capability callers do not know the memory implementation.",
            "topics": ["architecture"],
        }
    )

    assert result == {"created": True, "memory_id": "fact:new"}
    recalled = client.recall("implementation", topic="architecture")
    assert recalled[0]["node_id"] == "fact:new"
    assert client.read("fact:new")["node_id"] == "fact:new"


def test_runtime_memory_provider_rejects_cross_subject_calls(tmp_path):
    service = MemoryService(tmp_path / "memory")
    runtime = ApplicationRuntime.bootstrap(
        tmp_path / "runtime",
        subject_id="another-agent",
        max_side_effect_level="LOCAL_STATE",
    )
    runtime.register_provider(
        LegacyFerroMemoryCapabilityProvider(service, subject_id="ferro:test")
    )

    try:
        MemoryCapabilityClient(runtime).health()
    except Exception as exc:
        assert "SUBJECT_MISMATCH" in str(exc)
    else:
        raise AssertionError("cross-subject memory access must fail")
