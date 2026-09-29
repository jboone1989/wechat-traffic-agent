from __future__ import annotations

import asyncio
import json
from pathlib import Path

from wechat_traffic_agent.livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
    LivingRuntimeChatGPTProvider,
    cognition_routing_task_class,
    durable_cognition_phase,
    resume_next_durable_cognition,
)
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

def test_explicit_routing_context_survives_large_context_prefix(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({
  "status": "COMPLETED",
  "request_id": "llmreq_self_repair_route",
  "created_at": 100.0,
  "finished_at": 101.0,
  "response": {
    "text": json.dumps({
      "purpose": payload["purpose"],
      "routing_task_class": payload["metadata"]["routing_task_class"],
    }, separators=(",", ":")),
    "provider": "livingruntime-chatgpt",
    "model": "gpt-5.6-sol",
    "session_id": "session-route",
    "sha256": "route123",
    "completed_at": 101.0
  }
}, separators=(",", ":")))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=10,
    )
    prompt = ("runtime-context " * 1200) + "\nGENERATE_BOUNDED_SELF_REPAIR_PLAN"

    async def run():
        with cognition_routing_task_class("self_repair"):
            return await provider.complete(prompt)

    result = asyncio.run(run())

    assert json.loads(result) == {
        "purpose": "self_repair",
        "routing_task_class": "self_repair",
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
        "dispatch_timeout": 60,
        "wait_timeout": 5,
    }


def test_durable_blocking_wait_keeps_external_contestant_alive(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
idx = sys.argv.index("--wait-timeout-seconds")
wait_timeout = int(sys.argv[idx + 1])
print(json.dumps({
    "status": "COMPLETED",
    "request_id": payload["request_id"],
    "created_at": 100.0,
    "finished_at": 101.0,
    "response": {
        "text": json.dumps({
            "payload_timeout": payload["timeout_seconds"],
            "wait_timeout": wait_timeout,
        }, separators=(",", ":")),
        "provider": "livingruntime-chatgpt",
        "model": "gpt-5.6-sol",
        "session_id": "session-blocking",
        "sha256": "blocking123",
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
        foreground_wait_seconds=5,
    )

    result = asyncio.run(
        provider.complete(
            "external benchmark turn",
            _durable_blocking_wait=True,
            _durable_blocking_wait_seconds=90,
        )
    )

    assert json.loads(result) == {
        "payload_timeout": 120,
        "wait_timeout": 90,
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

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS", "120")

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is None
    assert backend.default_duration_seconds == 120.0




def test_runtime_backend_does_not_construct_legacy_provider_in_chatgpt_only_mode(
    tmp_path, monkeypatch
):
    cli = tmp_path / "cognitionctl.py"
    _write_fake_cli(cli)
    import agent_runtime.llm as runtime_llm

    def forbidden_loader():
        raise AssertionError("legacy provider must not be constructed")

    monkeypatch.setattr(runtime_llm, "load_llm_provider", forbidden_loader)
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_ALLOW_FALLBACK", raising=False)

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.fallback is None


def test_runtime_backend_ignores_legacy_fallback_opt_in(tmp_path, monkeypatch):
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
    assert backend.provider.enabled is True
    assert backend.provider.fallback is None

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
    assert backend.default_duration_seconds == 900.0


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

    assert provider.timeout_seconds == 900.0
    assert provider.dispatch_grace_seconds == 60.0


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


def test_durable_dispatch_is_bounded_even_when_enabled(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({
  "status":"COMPLETED","request_id":"llmreq_bounded_durable","created_at":1,"finished_at":2,
  "response":{"text":json.dumps({"dispatch":payload["dispatch_timeout_seconds"]}),
  "provider":"livingruntime-chatgpt","model":"test","session_id":"s","sha256":"x","completed_at":2}
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
        dispatch_grace_seconds=17,
        durable_dispatch=True,
    )

    result = asyncio.run(provider.complete("bounded durable dispatch"))

    assert json.loads(result) == {"dispatch": 17}


def test_remote_can_opt_out_of_durable_dispatch_and_use_bounded_grace(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({
  "status":"COMPLETED","request_id":"llmreq_bounded","created_at":1,"finished_at":2,
  "response":{"text":json.dumps({"dispatch":payload["dispatch_timeout_seconds"]}),
  "provider":"livingruntime-chatgpt","model":"test","session_id":"s","sha256":"x","completed_at":2}
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
        dispatch_grace_seconds=15,
        durable_dispatch=False,
    )

    result = asyncio.run(provider.complete("bounded dispatch"))

    assert json.loads(result) == {"dispatch": 15}


def test_durable_timeout_is_pending_and_request_identity_is_stable(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
if sys.argv[1] == "get":
    request_id = sys.argv[2]
    print(json.dumps({
      "status":"COMPLETED","request_id":request_id,"created_at":1,"finished_at":2,
      "response":{"text":"resumed-answer","provider":"livingruntime-chatgpt",
      "model":"test","session_id":"s","sha256":"x","completed_at":2}
    }))
else:
    json.load(sys.stdin)
    print(json.dumps({"ok":False,"error":"TimeoutError: waiting for cognition response exceeded timeout"}), file=sys.stderr)
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
        foreground_wait_seconds=1,
    )
    first_prompt = "Runtime state:\nA\n\nTask:\nChoose."
    second_prompt = "Runtime state:\nB changed\n\nTask:\nChoose."

    try:
        with durable_cognition_phase("phase-123"):
            asyncio.run(provider.complete(first_prompt))
        raise AssertionError("expected pending")
    except LivingRuntimeChatGPTPendingError as exc:
        first_id = exc.request_id
    with durable_cognition_phase("phase-123", request_id=first_id):
        resumed = asyncio.run(provider.complete(second_prompt))

    assert resumed == "resumed-answer"
    assert first_id.startswith("llmreq_")


def test_durable_resume_explicitly_rearms_pending_wake(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
assert sys.argv[1] == "get"
request_id = sys.argv[2]
print(json.dumps({
  "status":"COMPLETED","request_id":request_id,"created_at":1,"finished_at":2,
  "response":{"text":json.dumps({"resume_args":sys.argv[3:]}),
  "provider":"livingruntime-chatgpt","model":"test","session_id":"s",
  "sha256":"x","completed_at":2}
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
        dispatch_grace_seconds=17,
        durable_dispatch=True,
    )

    with resume_next_durable_cognition("llmreq-rearm-resume"):
        result = asyncio.run(provider.complete("resume pending request"))

    assert json.loads(result) == {
        "resume_args": [
            "--rearm-pending",
            "--rearm-min-interval-seconds",
            "17",
        ]
    }


def test_legacy_unbounded_pending_resume_falls_back_instead_of_waiting_forever(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
assert sys.argv[1] == "get"
request_id = sys.argv[2]
print(json.dumps({
  "status":"PENDING",
  "request_id":request_id,
  "created_at":1,
  "updated_at":1,
  "deadline_at":None,
  "dispatch_timeout_seconds":0,
  "response":None
}))
""",
        encoding="utf-8",
    )
    fallback = _Fallback()
    provider = LivingRuntimeChatGPTProvider(
        fallback=fallback,
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
    )

    with resume_next_durable_cognition("llmreq-legacy-unbounded"):
        result = asyncio.run(provider.complete("resume old request"))

    assert result == "fallback:resume old request"
    assert fallback.complete_calls == 1


def test_durable_unclaimed_resume_stays_pending_after_dispatch_grace(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
assert sys.argv[1] == "get"
request_id = sys.argv[2]
print(json.dumps({
  "status":"PENDING",
  "request_id":request_id,
  "created_at":1,
  "updated_at":70,
  "deadline_at":None,
  "dispatch_timeout_seconds":60,
  "dispatch_deadline_at":61,
  "dispatch_waiting_since":61,
  "response":None
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
    )

    caught = None
    try:
        with resume_next_durable_cognition("llmreq-durable-waiting"):
            asyncio.run(provider.complete("resume durable pending request"))
    except LivingRuntimeChatGPTPendingError as exc:
        caught = exc

    assert caught is not None
    assert caught.request_id == "llmreq-durable-waiting"


def test_explicit_disable_fails_closed_instead_of_using_legacy_provider(tmp_path, monkeypatch):
    fallback = _Fallback()
    marker = tmp_path / ".livingruntime" / "ferro-chatgpt-provider.enabled"
    marker.parent.mkdir(parents=True)
    marker.write_text("enabled\n", encoding="utf-8")
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.enabled is False
    assert backend.provider.fallback is None


def test_missing_remote_enablement_fails_closed_without_legacy_provider(tmp_path, monkeypatch):
    fallback = _Fallback()
    import agent_runtime.llm as runtime_llm

    monkeypatch.setattr(runtime_llm, "load_llm_provider", lambda: fallback)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", raising=False)

    backend = load_optional_advisory_backend()

    assert isinstance(backend.provider, LivingRuntimeChatGPTProvider)
    assert backend.provider.enabled is False
    assert backend.provider.fallback is None


def test_resume_next_durable_cognition_is_consumed_by_one_provider_call(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import json, sys
if sys.argv[1] == "get":
    request_id = sys.argv[2]
    text = "get:" + request_id
else:
    payload = json.load(sys.stdin)
    request_id = payload["request_id"]
    text = "call:" + request_id
print(json.dumps({
  "status":"COMPLETED","request_id":request_id,"created_at":1,"finished_at":2,
  "response":{"text":text,"provider":"livingruntime-chatgpt","model":"test",
  "session_id":"s","sha256":"x","completed_at":2}
}))
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
    )

    with resume_next_durable_cognition("llmreq-original"):
        first = asyncio.run(provider.complete("changed host prompt"))
        second = asyncio.run(provider.complete("next independent cognition"))

    assert first == "get:llmreq-original"
    assert second.startswith("call:llmreq_")
    assert second != "call:llmreq-original"


def test_signal_terminated_resume_remains_pending(tmp_path):
    cli = tmp_path / "cognitionctl.py"
    cli.write_text(
        """from __future__ import annotations
import os, signal, sys
assert sys.argv[1] == "get"
os.kill(os.getpid(), signal.SIGTERM)
""",
        encoding="utf-8",
    )
    provider = LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=300,
    )

    caught = None
    try:
        with resume_next_durable_cognition("llmreq-existing"):
            asyncio.run(provider.complete("resume after service restart"))
    except LivingRuntimeChatGPTPendingError as exc:
        caught = exc

    assert caught is not None
    assert caught.request_id == "llmreq-existing"


