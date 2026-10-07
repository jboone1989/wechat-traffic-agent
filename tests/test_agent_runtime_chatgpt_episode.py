import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider


def test_completed_cognition_detaches_conversation_carrier(tmp_path):
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

    durable = json.loads(state_path.read_text())
    assert durable["conversation_url"] is None
    assert durable["page_turns_since_refresh"] == 0
    assert durable["episode_history"][-1]["request_id"] == "req-1"
    assert underlying.complete.await_count == 1


def test_explicit_continuation_keeps_conversation_carrier(tmp_path):
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
        complete=AsyncMock(return_value="done"),
    )
    provider = AgentRuntimeChatGPTProvider(provider=underlying)

    asyncio.run(provider.complete(
        "Continue this bounded research episode.",
        _continue_cognition_episode=True,
    ))

    durable = json.loads(state_path.read_text())
    assert durable["conversation_url"] == "conversation:test"
    assert durable["page_turns_since_refresh"] == 1
