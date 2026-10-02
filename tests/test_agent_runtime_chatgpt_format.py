import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider
from wechat_traffic_agent.x_discovery import plan_x_search


def test_browser_json_preserves_phrase_operators_backslashes_and_unicode(tmp_path):
    query = '"crypto drawdown" "regime" -filter:replies 资金流'
    choice = {"domain": "CRYPTOCURRENCY", "query": query,
              "question": "Do drawdowns cluster?", "rationale": "Investigate observed market regimes."}
    raw = "```json\n" + json.dumps(choice, ensure_ascii=False) + "\n```"
    browser = SimpleNamespace(response_timeout_seconds=180, last_metadata={},
                              complete=AsyncMock(return_value=raw))
    provider = AgentRuntimeChatGPTProvider(provider=browser)
    runtime = SimpleNamespace(agent_manifest=None)
    async def probe(task, max_tokens, output_schema=None):
        return await provider.complete(task, max_tokens=max_tokens)
    runtime.cognitive_probe = probe
    result = asyncio.run(plan_x_search(runtime=runtime, state_root=tmp_path,
                         folder=tmp_path / "search", mission={}, persona={}, previous={}))
    assert result.query == query
    outbound = browser.complete.await_args.args[0]
    assert "one fenced json code block" in outbound
    assert "overrides instructions to omit code fences" in outbound
    assert browser.complete.await_args.kwargs["max_tokens"] == 2400


def test_browser_structured_schema_and_prose_contract_are_preserved():
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    value = {"text": 'He said "ready"; path C:\\\\state.'}
    browser = SimpleNamespace(response_timeout_seconds=180, last_metadata={},
                              structured=AsyncMock(return_value=value),
                              complete=AsyncMock(return_value="Hello, I'm Ferro."))
    provider = AgentRuntimeChatGPTProvider(provider=browser)
    assert asyncio.run(provider.structured("Return JSON without code fences", schema, max_tokens=100)) == value
    assert browser.structured.await_args.args[1] == schema
    assert "one fenced json code block" in browser.structured.await_args.args[0]
    assert asyncio.run(provider.complete("Say hello normally")) == "Hello, I'm Ferro."
    assert "For a prose output contract, respond normally" in browser.complete.await_args.args[0]
