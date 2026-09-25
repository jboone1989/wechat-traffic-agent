from __future__ import annotations

import asyncio
import json
from pathlib import Path

from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTProvider
from wechat_traffic_agent.runtime_backend import load_optional_advisory_backend


class _Fallback:
    def __init__(self) -> None:
        self.complete_calls = 0
        self.structured_calls = 0

    async def complete(self, prompt: str, **kwargs):
        self.complete_calls += 1
        return "fallback:" + prompt

    async def structured(self, prompt: str, schema: dict, **kwargs):
        self.structured_calls += 1
        return {"fallback": True}


def _write_fake_cli(path: Path) -> None:
    path.write_text(
        """from __future__ import annotations
import json, sys, time
payload = json.load(sys.stdin)
fmt = payload.get("response_format") or {"type":"text"}
text = json.dumps({"remote": True}, separators=(",", ":")) if fmt.get("type") == "json_schema" else "remote-answer"
result = {
  "status": "COMPLETED",
  "request_id": "llmreq_fake",
  "created_at": 100.0,
  "finished_at": 101.0,
  "response": {
    "text": text,
    "provider": "livingruntime-chatgpt",
    "model": "gpt-5.6-sol",
    "session_id": "session-fake",
    "sha256": "abc123",
    "completed_at": 101.0
  }
}
print(json.dumps(result, separators=(",", ":")))
""",
        encoding="utf-8",
    )


def test_remote_complete_is_preferred_and_records_provenance(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    fallback = _Fallback()
    provider = LivingRuntimeChatGPTProvider(
        fallback=fallback,
        cli_path=cli,
        enabled=True,
        timeout_seconds=10,
    )

    result = asyncio.run(
        provider.complete(
            "choose next action",
            system="You are Ferro.",
            max_tokens=200,
            _routing_task_class="strategy",
        )
    )

    assert result == "remote-answer"
    assert fallback.complete_calls == 0
    assert provider.last_provenance == {
        "provider": "livingruntime-chatgpt",
        "request_id": "llmreq_fake",
        "session_id": "session-fake",
        "model": "gpt-5.6-sol",
        "started_at": 100.0,
        "completed_at": 101.0,
        "response_sha256": "abc123",
    }


def test_remote_structured_uses_json_schema(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=10,
    )

    result = asyncio.run(
        provider.structured(
            "return an object",
            {"type": "object", "properties": {"remote": {"type": "boolean"}}},
        )
    )

    assert result == {"remote": True}


def test_remote_honors_reserve_budget_with_process_teardown_margin(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
idx = sys.argv.index("--wait-timeout-seconds")
wait_timeout = int(sys.argv[idx + 1])
text = json.dumps({
    "payload_timeout": payload["timeout_seconds"],
    "dispatch_timeout": payload["dispatch_timeout_seconds"],
    "wait_timeout": wait_timeout,
}, separators=(",", ":"))
print(json.dumps({
    "status": "COMPLETED",
    "request_id": "llmreq_budget",
    "created_at": 100.0,
    "finished_at": 101.0,
    "response": {
        "text": text,
        "provider": "livingruntime-chatgpt",
        "model": "gpt-5.6-sol",
        "session_id": "session-budget",
        "sha256": "budget123",
        "completed_at": 101.0
    }
}, separators=(",", ":")))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=120,
    )

    result = asyncio.run(
        provider.complete(
            "bounded cognition",
            _attempt_timeout_seconds=15,
            _reserve_timeout_seconds=100,
        )
    )

    assert json.loads(result) == {
        "payload_timeout": 97,
        "dispatch_timeout": 15,
        "wait_timeout": 97,
    }


def test_disabled_remote_falls_back_without_running_cli(tmp_path):
    fallback = _Fallback()
    provider = LivingRuntimeChatGPTProvider(
        fallback=fallback,
        cli_path=tmp_path / "missing.py",
        enabled=False,
        timeout_seconds=10,
    )

    result = asyncio.run(provider.complete("hello"))

    assert result == "fallback:hello"
    assert fallback.complete_calls == 1


def test_runtime_backend_uses_chatgpt_only_when_enabled_by_default(tmp_path, monkeypatch):
    fallback = _Fallback()
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(
        runtime_llm,
        "load_llm_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("legacy/free provider must not be constructed")
        ),
    )
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS", "120")

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is None
    assert backend.default_duration_seconds == 120.0




def test_runtime_backend_can_opt_in_to_legacy_fallback(tmp_path, monkeypatch):
    fallback = _Fallback()
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_ALLOW_FALLBACK", "1")

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is fallback

def test_runtime_backend_can_use_remote_when_legacy_provider_is_unconfigured(
    tmp_path, monkeypatch
):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(
        runtime_llm,
        "load_llm_provider",
        lambda: (_ for _ in ()).throw(runtime_llm.LLMProviderError("not configured")),
    )
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is None
    assert backend.default_duration_seconds == 300.0


def test_runtime_backend_can_use_local_enable_marker(tmp_path, monkeypatch):
    fallback = _Fallback()
    cli = tmp_path / ".livingruntime" / "plugin" / "scripts" / "cognitionctl.py"
    cli.parent.mkdir(parents=True)
    _write_fake_cli(cli)
    marker = tmp_path / ".livingruntime" / "ferro-chatgpt-provider.enabled"
    marker.write_text("enabled\n", encoding="utf-8")
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", raising=False)
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", raising=False)

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is None
    assert backend.provider.cli_path == cli


def test_default_remote_budget_allows_chatgpt_watcher_round_trip(tmp_path, monkeypatch):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
    )

    assert provider.timeout_seconds == 300.0
    assert provider.dispatch_grace_seconds == 15.0


def test_remote_dispatch_grace_is_configurable_and_bounded(tmp_path, monkeypatch):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_DISPATCH_GRACE_SECONDS", "7")

    provider = LivingRuntimeChatGPTProvider.from_environment()

    assert provider.dispatch_grace_seconds == 7.0


def test_remote_dispatch_grace_never_exceeds_short_caller_budget(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({
  "status":"COMPLETED","request_id":"llmreq_short","created_at":1,"finished_at":2,
  "response":{"text":json.dumps({
    "timeout":payload["timeout_seconds"],
    "dispatch":payload["dispatch_timeout_seconds"]
  }),"provider":"livingruntime-chatgpt","model":"test","session_id":"s","sha256":"x","completed_at":2}
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
        dispatch_grace_seconds=15,
    )

    result = asyncio.run(
        provider.complete("short", _reserve_timeout_seconds=10)
    )

    assert json.loads(result) == {"timeout": 7, "dispatch": 7}


def test_explicit_disable_overrides_local_enable_marker(tmp_path, monkeypatch):
    fallback = _Fallback()
    marker = tmp_path / ".livingruntime" / "ferro-chatgpt-provider.enabled"
    marker.parent.mkdir(parents=True)
    marker.write_text("enabled\n", encoding="utf-8")
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")

    backend = load_optional_advisory_backend()

    assert backend.provider is fallback


def test_runtime_backend_preserves_existing_provider_when_remote_disabled(tmp_path, monkeypatch):
    fallback = _Fallback()
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", raising=False)

    backend = load_optional_advisory_backend()

    assert backend.provider is fallback
