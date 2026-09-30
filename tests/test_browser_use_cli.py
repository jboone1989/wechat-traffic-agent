from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.browser_use_cli import (
    AccountBrowserUseExecutor,
    BrowserUseUnavailable,
)


class Profiles:
    def __init__(self, cdp_url="http://127.0.0.1:9400"):
        self.cdp_url = cdp_url
        self.calls = []

    def ensure(self, account_id):
        self.calls.append(account_id)
        return SimpleNamespace(
            account_id=account_id,
            cdp_url=self.cdp_url,
            running=True,
        )


def test_browser_use_attaches_to_native_account_chrome(tmp_path):
    cli = tmp_path / "browser-use"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "url": "https://example.com/",
                "title": "Example Domain",
            }) + "\n",
            stderr="",
        )

    profiles = Profiles()
    executor = AccountBrowserUseExecutor(
        state_root=tmp_path,
        profile_manager=profiles,
        cli_path=cli,
        runner=runner,
    )
    page = executor.inspect(
        account_id="acct_x_123",
        url="https://example.com",
    )

    assert page.provider == "BROWSER_USE_CLI"
    assert page.url == "https://example.com/"
    assert page.title == "Example Domain"
    assert profiles.calls == ["acct_x_123"]
    command, kwargs = calls[0]
    assert command == [str(cli)]
    assert kwargs["env"]["BU_CDP_URL"] == "http://127.0.0.1:9400"
    assert kwargs["env"]["ANONYMIZED_TELEMETRY"] == "false"
    assert 'new_tab("https://example.com")' in kwargs["input"]
    assert "page_info()" in kwargs["input"]


def test_browser_use_translates_structured_operations(tmp_path):
    cli = tmp_path / "browser-use"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "url": "https://example.com/form",
                "title": "Form",
            }) + "\n",
            stderr="",
        )

    executor = AccountBrowserUseExecutor(
        state_root=tmp_path,
        profile_manager=Profiles(),
        cli_path=cli,
        runner=runner,
    )
    result = executor.execute_operations(
        account_id="acct_x_123",
        operations=[
            {"action": "open", "url": "https://example.com/form"},
            {"action": "fill", "selector": "#name", "text": "Ferro"},
            {"action": "press", "key": "Tab"},
            {"action": "scroll", "x": 0, "y": 420},
            {"action": "inspect"},
        ],
    )

    assert result.url == "https://example.com/form"
    script = calls[0][1]["input"]
    assert 'new_tab("https://example.com/form")' in script
    assert 'fill_input("#name", "Ferro")' in script
    assert 'press_key("Tab")' in script
    assert "scroll(0, 420)" in script


def test_browser_use_refuses_nonlocal_cdp(tmp_path):
    cli = tmp_path / "browser-use"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    executor = AccountBrowserUseExecutor(
        state_root=tmp_path,
        profile_manager=Profiles("http://example.com:9222"),
        cli_path=cli,
        runner=lambda *_a, **_k: None,
    )
    with pytest.raises(BrowserUseUnavailable, match="unsafe"):
        executor.inspect(
            account_id="acct_x_123",
            url="https://example.com",
        )


def test_browser_use_requires_installed_cli(tmp_path):
    executor = AccountBrowserUseExecutor(
        state_root=tmp_path,
        profile_manager=Profiles(),
        cli_path=tmp_path / "missing-browser-use",
    )
    assert executor.readiness()["status"] == "NOT_INSTALLED"
    with pytest.raises(BrowserUseUnavailable, match="not installed"):
        executor.inspect(
            account_id="acct_x_123",
            url="https://example.com",
        )


@pytest.mark.parametrize(
    "operation",
    [
        {"action": "unknown"},
        {"action": "open", "url": "file:///etc/passwd"},
        {"action": "click", "x": -1, "y": 2},
    ],
)
def test_browser_use_rejects_invalid_operations(tmp_path, operation):
    cli = tmp_path / "browser-use"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    executor = AccountBrowserUseExecutor(
        state_root=tmp_path,
        profile_manager=Profiles(),
        cli_path=cli,
        runner=lambda *_a, **_k: None,
    )
    with pytest.raises(ValueError):
        executor.execute_operations(
            account_id="acct_x_123",
            operations=[operation],
        )
