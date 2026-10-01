from __future__ import annotations

from types import SimpleNamespace

import pytest

import wechat_traffic_agent.chatgpt_cortex_browser_control as cortex_module
from wechat_traffic_agent.chatgpt_cortex_browser_control import (
    ChatGPTCortexBrowserControl,
)


class FakeProfileManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.status_calls = []
        self.ensure_calls = []

    def status(self, account_id):
        self.status_calls.append(account_id)
        return self.runtime

    def ensure(self, account_id):
        self.ensure_calls.append(account_id)
        return self.runtime


def test_cortex_uses_dedicated_chatgpt_profile_runtime(tmp_path):
    runtime = SimpleNamespace(
        account_id="cortex_chatgpt",
        profile_id="browser_cortex_chatgpt",
        port=9407,
        pid=123,
        cdp_url="http://127.0.0.1:9407",
        running=True,
    )
    manager = FakeProfileManager(runtime)
    control = ChatGPTCortexBrowserControl(
        state_root=tmp_path,
        profile_manager=manager,
    )

    assert control.account_id == "cortex_chatgpt"
    assert control.cdp_url() == runtime.cdp_url

    shared = control._control()
    assert shared.state_path == (
        tmp_path
        / "accounts"
        / "browser-profiles"
        / "cortex_chatgpt"
        / "chatgpt-cortex-target.json"
    )
    assert manager.status_calls == ["cortex_chatgpt"]


def test_cortex_does_not_require_x_account(tmp_path):
    control = ChatGPTCortexBrowserControl(state_root=tmp_path)
    assert control.account_id == "cortex_chatgpt"


def test_cortex_rejects_foreign_account_id(tmp_path):
    with pytest.raises(ValueError, match="dedicated persistent profile"):
        ChatGPTCortexBrowserControl(
            state_root=tmp_path,
            account_id="acct_x_deadbeef0000",
        )


def test_cortex_status_does_not_treat_anonymous_composer_as_authenticated(
    tmp_path,
    monkeypatch,
):
    runtime = SimpleNamespace(
        account_id="cortex_chatgpt",
        profile_id="browser_cortex_chatgpt",
        port=9407,
        pid=123,
        cdp_url="http://127.0.0.1:9407",
        running=True,
    )
    control = cortex_module._ChatGPTProfileControl(
        state_root=tmp_path,
        profile_manager=FakeProfileManager(runtime),
    )
    control._write_target("target-1")
    monkeypatch.setattr(
        control,
        "_target_info",
        lambda target: {"id": target, "url": "https://chatgpt.com/"},
    )
    monkeypatch.setattr(
        control,
        "_evaluate",
        lambda page, expression: (
            '{"url":"https://chatgpt.com/","title":"ChatGPT",'
            '"composer":true,"loginRequired":true,"accountControl":false}'
        ),
    )
    status = control.status()
    assert status["authenticated"] is False
    assert status["profile"] == "chatgpt-cortex"


def test_cortex_status_recognizes_authenticated_non_composer_page(
    tmp_path,
    monkeypatch,
):
    runtime = SimpleNamespace(
        account_id="cortex_chatgpt",
        profile_id="browser_cortex_chatgpt",
        port=9407,
        pid=123,
        cdp_url="http://127.0.0.1:9407",
        running=True,
    )
    control = cortex_module._ChatGPTProfileControl(
        state_root=tmp_path,
        profile_manager=FakeProfileManager(runtime),
    )
    control._write_target("target-1")
    monkeypatch.setattr(
        control,
        "_target_info",
        lambda target: {"id": target, "url": "https://chatgpt.com/scheduled"},
    )
    monkeypatch.setattr(
        control,
        "_evaluate",
        lambda page, expression: (
            '{"url":"https://chatgpt.com/scheduled","title":"ChatGPT",'
            '"composer":false,"loginRequired":false,"accountControl":true}'
        ),
    )
    status = control.status()
    assert status["authenticated"] is True
    assert status["identity"] == "ChatGPT"
