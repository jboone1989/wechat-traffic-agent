from __future__ import annotations

from types import SimpleNamespace

import pytest

import wechat_traffic_agent.chatgpt_cortex_browser_control as cortex_module
from wechat_traffic_agent.account_registry import AccountRegistry
from wechat_traffic_agent.chatgpt_cortex_browser_control import (
    ChatGPTCortexBrowserControl,
    ChatGPTCortexBrowserUnavailable,
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


def test_cortex_reuses_x_account_profile_runtime(tmp_path):
    record = AccountRegistry(tmp_path).ensure(
        provider_id="x",
        identity="ferro",
        oauth_status="CONNECTED",
    )
    runtime = SimpleNamespace(
        account_id=record.account_id,
        profile_id=f"browser_{record.account_id}",
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

    assert control.account_id == record.account_id
    assert control.cdp_url() == runtime.cdp_url

    shared = control._control()
    assert shared.state_path == (
        tmp_path
        / "accounts"
        / "browser-profiles"
        / record.account_id
        / "chatgpt-cortex-target.json"
    )
    assert "cortex/chatgpt-web" not in str(shared.state_path)
    assert manager.status_calls == [record.account_id]


def test_cortex_requires_explicit_x_account_when_multiple_exist(
    tmp_path,
    monkeypatch,
):
    registry = AccountRegistry(tmp_path)
    first = registry.ensure(
        provider_id="x",
        identity="one",
        oauth_status="CONNECTED",
    )
    registry.ensure(
        provider_id="x",
        identity="two",
        oauth_status="CONNECTED",
    )
    monkeypatch.delenv("CONTENT_X_ACCOUNT_ID", raising=False)

    with pytest.raises(
        ChatGPTCortexBrowserUnavailable,
        match="CONTENT_X_ACCOUNT_ID",
    ):
        ChatGPTCortexBrowserControl(state_root=tmp_path).account_id

    monkeypatch.setenv("CONTENT_X_ACCOUNT_ID", first.account_id)
    assert (
        ChatGPTCortexBrowserControl(state_root=tmp_path).account_id
        == first.account_id
    )


def test_cortex_status_does_not_treat_anonymous_composer_as_authenticated(
    tmp_path,
    monkeypatch,
):
    record = AccountRegistry(tmp_path).ensure(
        provider_id="x",
        identity="ferro",
        oauth_status="CONNECTED",
    )
    runtime = SimpleNamespace(
        account_id=record.account_id,
        profile_id=f"browser_{record.account_id}",
        port=9407,
        pid=123,
        cdp_url="http://127.0.0.1:9407",
        running=True,
    )
    control = cortex_module._SharedProfileChatGPTControl(
        state_root=tmp_path,
        account_id=record.account_id,
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
            '"composer":true,"loginRequired":true}'
        ),
    )
    assert control.status()["authenticated"] is False
