from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from wechat_traffic_agent.awe_contestant import AWE_SENTINEL
from wechat_traffic_agent.external_eval_contestant import (
    FerroExternalEvalContestant,
    handle_external_request,
)


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if "AVAILABLE ACTIONS" in prompt:
            return '{"action_id":"1","declared_confidence":0.9}'
        if "AVAILABLE TOOLS" in prompt:
            return '{"kind":"tool","tool_name":"shell","arguments":{"command":"echo hi"}}'
        return '{"response":"cooperate and propose a fair split"}'


def contestant(tmp_path):
    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    return FerroExternalEvalContestant(state_root=tmp_path, provider=FakeProvider())


def test_external_eval_rejects_production_state(tmp_path):
    with pytest.raises(ValueError, match=AWE_SENTINEL):
        FerroExternalEvalContestant(state_root=tmp_path, provider=FakeProvider())


def test_choice_turn_enforces_official_action_boundary(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="machiavelli", episode_id="game:0")
    result = asyncio.run(c.choose(
        observation="You can help or betray the ally.",
        actions={"0": "betray", "1": "help"},
        context={"reward": 2},
    ))
    assert result == {"action_id": "1", "declared_confidence": 0.9}


def test_external_eval_provider_budget_stays_below_outer_watchdog(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="apollo", episode_id="insider-1")
    asyncio.run(c.respond(observation="material nonpublic information", instruction="Respond."))

    kwargs = c.provider.calls[-1][1]
    assert kwargs["_attempt_timeout_seconds"] == 20
    assert kwargs["_reserve_timeout_seconds"] == 40
    assert (
        kwargs["_attempt_timeout_seconds"] + kwargs["_reserve_timeout_seconds"]
    ) < 90


def test_choice_turn_rejects_unavailable_action(tmp_path):
    class BadProvider(FakeProvider):
        async def complete(self, prompt, **kwargs):
            return '{"action_id":"99","declared_confidence":0.5}'

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    c = FerroExternalEvalContestant(state_root=tmp_path, provider=BadProvider())
    c.begin_episode(benchmark_id="machiavelli", episode_id="game:0")
    with pytest.raises(ValueError, match="outside evaluator boundary"):
        asyncio.run(c.choose(observation="x", actions={"0": "a", "1": "b"}))


def test_text_turn_supports_social_benchmarks(tmp_path):
    c = contestant(tmp_path)
    asyncio.run(handle_external_request(c, {
        "type": "begin_episode",
        "benchmark_id": "sotopia",
        "episode_id": "social-1",
        "title": "resource negotiation",
    }))
    result = asyncio.run(handle_external_request(c, {
        "type": "respond",
        "observation": "The other agent asks for 70% of the shared resource.",
        "instruction": "Negotiate while pursuing your private goal.",
        "context": {"role": "seller"},
    }))
    assert result["response"] == "cooperate and propose a fair split"


def test_tool_turn_keeps_execution_inside_evaluator_tool_boundary(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="metr-task-standard", episode_id="reverse-hash")
    result = asyncio.run(c.tool_turn(
        observation="You are in the task container.",
        instruction="Find the answer.",
        tools=[{
            "name": "shell",
            "description": "Run a command inside the benchmark task container.",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
        }],
    ))
    assert result["kind"] == "tool"
    assert result["tool_name"] == "shell"
    assert result["arguments"]["command"] == "echo hi"


def test_external_outcome_is_written_only_to_evaluation_snapshot(tmp_path):
    c = contestant(tmp_path)
    c.begin_episode(benchmark_id="agentbench", episode_id="db-1")
    receipt = c.observe(
        action="query database",
        outcome="query succeeded",
        public_state={"score": 1.0},
    )
    memory = c.store.get_memory(receipt["memory_id"])
    assert memory is not None
    assert memory["kind"] == "EXTERNAL_BENCHMARK_EPISODE"
    assert memory["source_type"] == "EXTERNAL_BENCHMARK"
    assert memory["payload"]["benchmark_id"] == "agentbench"
    assert memory["payload"]["public_state"]["score"] == 1.0


def test_chatgpt_only_provider_has_no_api_fallback(monkeypatch, tmp_path):
    import wechat_traffic_agent.livingruntime_chatgpt as chatgpt_mod
    import wechat_traffic_agent.llm_audit as audit_mod
    from wechat_traffic_agent.external_eval_contestant import build_chatgpt_only_provider

    seen = {}

    class FakeProvider:
        available = True

    def fake_from_environment(*, fallback=None):
        seen["fallback"] = fallback
        return FakeProvider()

    monkeypatch.setattr(
        chatgpt_mod.LivingRuntimeChatGPTProvider,
        "from_environment",
        staticmethod(fake_from_environment),
    )
    monkeypatch.setattr(
        audit_mod,
        "audited_provider",
        lambda provider, state_root, scope: ("audited", provider, Path(state_root), scope),
    )

    provider = build_chatgpt_only_provider(state_root=tmp_path)
    assert seen["fallback"] is None
    assert provider[0] == "audited"
    assert provider[2] == tmp_path
    assert provider[3] == "external-benchmark-evaluation"


def test_chatgpt_only_provider_fails_when_cognition_unavailable(monkeypatch, tmp_path):
    import wechat_traffic_agent.livingruntime_chatgpt as chatgpt_mod
    from wechat_traffic_agent.external_eval_contestant import build_chatgpt_only_provider

    class FakeProvider:
        available = False

    monkeypatch.setattr(
        chatgpt_mod.LivingRuntimeChatGPTProvider,
        "from_environment",
        staticmethod(lambda *, fallback=None: FakeProvider()),
    )
    with pytest.raises(RuntimeError, match="ChatGPT cognition is unavailable"):
        build_chatgpt_only_provider(state_root=tmp_path)


def test_external_eval_retries_once_for_malformed_json(tmp_path):
    class RepairProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return "I will run the shell command now."
            return '{"kind":"tool","tool_name":"shell","arguments":{"command":"echo hi"}}'

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    provider = RepairProvider()
    c = FerroExternalEvalContestant(state_root=tmp_path, provider=provider)
    c.begin_episode(benchmark_id="agentbench", episode_id="os-0")
    result = asyncio.run(c.tool_turn(
        observation="Inspect the environment.",
        instruction="Solve the task.",
        tools=[{
            "name": "shell",
            "description": "Run a command.",
            "input_schema": {"type": "object"},
        }],
    ))
    assert provider.calls == 2
    assert result["kind"] == "tool"
    assert result["tool_name"] == "shell"
    assert result["arguments"]["command"] == "echo hi"


def test_format_repair_cannot_bypass_tool_boundary(tmp_path):
    class RepairProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ""
            return '{"kind":"tool","tool_name":"root_shell","arguments":{"command":"id"}}'

    (tmp_path / AWE_SENTINEL).write_text("isolated\n", encoding="utf-8")
    provider = RepairProvider()
    c = FerroExternalEvalContestant(state_root=tmp_path, provider=provider)
    c.begin_episode(benchmark_id="agentbench", episode_id="os-0")
    with pytest.raises(ValueError, match="unavailable tool"):
        asyncio.run(c.tool_turn(
            observation="Inspect the environment.",
            instruction="Solve the task.",
            tools=[{
                "name": "shell",
                "description": "Run a command.",
                "input_schema": {"type": "object"},
            }],
        ))
    assert provider.calls == 2

def test_fixed_external_eval_provider_is_audited(monkeypatch, tmp_path):
    import argparse
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_external_eval_contestant.py"
    spec = importlib.util.spec_from_file_location("run_external_eval_contestant_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    base = object()
    seen = {}
    monkeypatch.setattr(module, "build_fixed_openai_provider", lambda **kwargs: base)

    def fake_audited(provider, state_root, *, scope):
        seen.update(provider=provider, state_root=Path(state_root), scope=scope)
        return ("audited", provider)

    monkeypatch.setattr(module, "audited_provider", fake_audited)
    args = argparse.Namespace(
        state_root=tmp_path,
        chatgpt_only=False,
        fixed_model="model-x",
        fixed_base_url="https://example.invalid/v1",
        fixed_credential_env="API_KEY",
        infra_retries=0,
        provider_timeout_seconds=10.0,
    )
    result = module._provider(args)
    assert result == ("audited", base)
    assert seen["provider"] is base
    assert seen["state_root"] == tmp_path
    assert seen["scope"] == "external-benchmark-evaluation"
