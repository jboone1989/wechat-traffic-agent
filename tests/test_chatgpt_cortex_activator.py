from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.chatgpt_cortex_activator import (
    activation_prompt,
    schedule_chatgpt_web_cortex_activation,
)
from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTProvider
import wechat_traffic_agent.chatgpt_cortex_activator as activator_module
import wechat_traffic_agent.chatgpt_cortex_browser_control as cortex_module
import wechat_traffic_agent.livingruntime_chatgpt as provider_module


def test_activation_prompt_is_bounded_to_request_id():
    request_id = "llmreq_12345678abcdef"
    prompt = activation_prompt(request_id)
    assert request_id in prompt
    assert "LivingRuntime Remote Beta 2" in prompt
    assert "Do not treat it as cognition content" in prompt
    with pytest.raises(ValueError):
        activation_prompt("not-a-request")


def test_schedule_is_enabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FERRO_CHATGPT_WEB_CORTEX_ENABLED", raising=False)
    calls = []

    class _Proc:
        pid = 4321

    monkeypatch.setattr(
        activator_module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or _Proc(),
    )
    result = schedule_chatgpt_web_cortex_activation(
        "llmreq_12345678abcdef",
        state_root=tmp_path,
    )
    assert result["status"] == "STARTED"
    assert len(calls) == 1


def test_schedule_can_be_explicitly_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FERRO_CHATGPT_WEB_CORTEX_ENABLED", "0")
    result = schedule_chatgpt_web_cortex_activation(
        "llmreq_12345678abcdef",
        state_root=tmp_path,
    )
    assert result["status"] == "DISABLED"


def test_schedule_spawns_once_then_coalesces(tmp_path, monkeypatch):
    monkeypatch.setenv("FERRO_CHATGPT_WEB_CORTEX_ENABLED", "1")
    monkeypatch.setenv("FERRO_CHATGPT_WEB_CORTEX_COOLDOWN_SECONDS", "120")
    calls = []

    class _Proc:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return _Proc()

    monkeypatch.setattr(activator_module.subprocess, "Popen", fake_popen)

    first = schedule_chatgpt_web_cortex_activation(
        "llmreq_12345678abcdef",
        state_root=tmp_path,
    )
    second = schedule_chatgpt_web_cortex_activation(
        "llmreq_abcdef12345678",
        state_root=tmp_path,
    )

    assert first["status"] == "STARTED"
    assert first["pid"] == 4321
    assert second["status"] == "COALESCED"
    assert len(calls) == 1
    state = json.loads(
        (tmp_path / "cortex" / "chatgpt-web" / "activation-state.json").read_text()
    )
    assert state["request_id"] == "llmreq_12345678abcdef"


def _provider(tmp_path: Path) -> LivingRuntimeChatGPTProvider:
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("# stub\n", encoding="utf-8")
    return LivingRuntimeChatGPTProvider(
        cli_path=cli,
        enabled=True,
        timeout_seconds=30,
        dispatch_grace_seconds=10,
        foreground_wait_seconds=2,
    )


def test_provider_activates_background_cognition_but_not_public_chat(
    tmp_path, monkeypatch
):
    provider = _provider(tmp_path)
    seen = []

    def fake_schedule(request_id):
        seen.append(request_id)
        return {"status": "STARTED", "request_id": request_id, "pid": 99}

    monkeypatch.setattr(
        provider_module,
        "schedule_chatgpt_web_cortex_activation",
        fake_schedule,
    )

    provider._activate_pending_cortex(
        "llmreq_background123",
        task_class="self_repair",
    )
    provider._activate_pending_cortex(
        "llmreq_public123456",
        task_class="simple_public_text",
    )
    assert seen == ["llmreq_background123"]


def test_visible_login_control_takes_precedence_over_composer():
    class _Browser:
        def __init__(self, value):
            self.value = value

        def evaluate(self, expression):
            assert "log in" in expression
            return self.value

    assert activator_module._login_required(_Browser(True)) is True
    assert activator_module._login_required(_Browser(False)) is False


def test_activation_setup_failure_finishes_state_instead_of_staying_running(
    tmp_path,
    monkeypatch,
):
    class _BrokenControl:
        def __init__(self, *, state_root):
            self.account_id = "acct_x_test"

        def cdp_url(self):
            raise RuntimeError("shared browser unavailable")

    monkeypatch.setattr(
        cortex_module,
        "ChatGPTCortexBrowserControl",
        _BrokenControl,
    )
    result = activator_module._send_activation(
        "llmreq_12345678abcdef",
        state_root=tmp_path,
    )
    assert result["status"] == "ACTIVATION_FAILED"
    assert result["detail"] == "shared browser unavailable"
