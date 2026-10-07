import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider


def test_consecutive_cognition_calls_preserve_bound_conversation(tmp_path):
    state_path = tmp_path / "conversation.json"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "session_id": "ferro-test",
        "pending": None,
        "conversation_url": "conversation:test",
        "page_turns_since_refresh": 3,
        "last_completed": {
            "request_id": "req-1",
            "payload_sha256": "abc123",
        },
    }))
    underlying = SimpleNamespace(
        response_timeout_seconds=180,
        last_metadata={"url": "conversation:test"},
        conversation_state_path=state_path,
        complete=AsyncMock(return_value="done"),
    )
    provider = AgentRuntimeChatGPTProvider(provider=underlying)

    assert asyncio.run(provider.complete("Think about the current task.")) == "done"
    assert asyncio.run(provider.complete("Continue the same investigation.")) == "done"

    durable = json.loads(state_path.read_text())
    assert durable["conversation_url"] == "conversation:test"
    assert durable["page_turns_since_refresh"] == 3
    assert not durable.get("episode_history")
    assert underlying.complete.await_count == 2

    # Constructing a new adapter after a worker restart also preserves the URL.
    restarted = AgentRuntimeChatGPTProvider(provider=underlying)
    assert asyncio.run(restarted.complete("Resume after restart.")) == "done"
    assert json.loads(state_path.read_text())["conversation_url"] == "conversation:test"


def test_structured_cognition_preserves_bound_conversation(tmp_path):
    state_path = tmp_path / "conversation.json"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "session_id": "ferro-test",
        "pending": None,
        "conversation_url": "conversation:test",
        "page_turns_since_refresh": 1,
    }))
    underlying = SimpleNamespace(
        response_timeout_seconds=180,
        last_metadata={},
        conversation_state_path=state_path,
        structured=AsyncMock(return_value={"ok": True}),
    )
    provider = AgentRuntimeChatGPTProvider(provider=underlying)

    assert asyncio.run(provider.structured("Select evidence.", {"type": "object"})) == {"ok": True}

    durable = json.loads(state_path.read_text())
    assert durable["conversation_url"] == "conversation:test"
    assert durable["page_turns_since_refresh"] == 1


def test_explicit_episode_end_detaches_after_success_only(tmp_path):
    state_path = tmp_path / "conversation.json"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "session_id": "ferro-test",
        "pending": None,
        "conversation_url": "conversation:test",
        "page_turns_since_refresh": 3,
        "last_completed": {"request_id": "req-1", "payload_sha256": "abc123"},
    }))
    underlying = SimpleNamespace(
        response_timeout_seconds=180,
        last_metadata={},
        conversation_state_path=state_path,
        complete=AsyncMock(return_value="done"),
    )
    provider = AgentRuntimeChatGPTProvider(provider=underlying)
    assert asyncio.run(provider.complete(
        "Finish the resolved goal.", _end_cognition_episode=True,
    )) == "done"

    journal = json.loads(state_path.read_text())
    assert journal["conversation_url"] is None
    assert journal["page_turns_since_refresh"] == 0
    assert journal["episode_history"][-1]["request_id"] == "req-1"


def test_failed_call_does_not_end_conversation(tmp_path):
    state_path = tmp_path / "conversation.json"
    state = {"schema_version": 1, "session_id": "ferro-test",
             "conversation_url": "conversation:test", "pending": None}
    state_path.write_text(json.dumps(state))
    underlying = SimpleNamespace(
        response_timeout_seconds=180,
        last_metadata={},
        conversation_state_path=state_path,
        complete=AsyncMock(side_effect=RuntimeError("transport failed")),
    )
    provider = AgentRuntimeChatGPTProvider(provider=underlying)
    import pytest
    with pytest.raises(RuntimeError, match="transport failed"):
        asyncio.run(provider.complete("Try task.", _end_cognition_episode=True))
    assert json.loads(state_path.read_text()) == state
