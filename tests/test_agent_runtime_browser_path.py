from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from wechat_traffic_agent import browser_runtime
from wechat_traffic_agent.agent_runtime_chatgpt import (
    AgentRuntimeChatGPTProvider,
)
from wechat_traffic_agent.local_browser_control import LocalBrowserControl
from wechat_traffic_agent.x_browser import (
    XBrowserPublishResult,
    XBrowserTransportRouter,
)
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
import wechat_traffic_agent.agent_runtime_chatgpt as chatgpt_module
import wechat_traffic_agent.local_browser_control as local_control_module
import wechat_traffic_agent.x_local_browser as x_local_module


class _ReadyBrowser:
    def readiness(self):
        return {"status": "READY"}


class _FakeChatGPTProvider:
    response_timeout_seconds = 90.0

    def __init__(self):
        self.browser = _ReadyBrowser()
        self.last_metadata = None

    async def complete(self, prompt: str, **kwargs):
        assert prompt.startswith("hello\n\nCHATGPT_WEB_JSON_FORMAT:")
        assert "For a prose output contract, respond normally" in prompt
        self.last_metadata = {
            "provider": "chatgpt-browser",
            "model": "chatgpt-web",
            "url": "https://chatgpt.com/",
            "assistant_message_count": 1,
        }
        return "world"

    async def structured(self, prompt: str, schema: dict, **kwargs):
        self.last_metadata = {
            "provider": "chatgpt-browser",
            "model": "chatgpt-web",
            "url": "https://chatgpt.com/",
            "assistant_message_count": 1,
        }
        return {"ok": True}


def test_chatgpt_adapter_uses_agent_runtime_provider_only(monkeypatch):
    fake = _FakeChatGPTProvider()
    monkeypatch.setattr(
        chatgpt_module.ChatGPTBrowserProvider,
        "from_environment",
        classmethod(lambda cls: fake),
    )

    provider = AgentRuntimeChatGPTProvider.from_environment()
    assert provider.available is True
    assert asyncio.run(provider.complete("hello")) == "world"
    assert asyncio.run(provider.structured("json", {"type": "object"})) == {"ok": True}
    assert provider.last_provenance["provider"] == "chatgpt-browser"
    assert provider.last_provenance["model"] == "chatgpt-web"


def test_chatgpt_adapter_rejects_fallback_provider(monkeypatch):
    with pytest.raises(ValueError, match="fallback"):
        AgentRuntimeChatGPTProvider.from_environment(fallback=object())


def test_browser_runtime_status_exposes_agent_runtime_only(monkeypatch):
    monkeypatch.setattr(
        browser_runtime,
        "_profile_status",
        lambda profile_id: {
            "profile_id": profile_id,
            "status": "READY",
            "cdp_url": "http://127.0.0.1:9600" if profile_id == "chatgpt" else "http://127.0.0.1:9601",
        },
    )
    status = browser_runtime.browser_runtime_status()
    assert status["routing_policy"] == "AGENT_RUNTIME_ONLY"
    assert status["provider"] == "AGENT_RUNTIME_BROWSER"
    assert status["backend"] == "browser-harness"
    assert [row["profile_id"] for row in status["profiles"]] == ["chatgpt", "x"]
    assert len(status["providers"]) == 1


class _FakeXBrowser:
    def __init__(self):
        self.invocations = []
        self.scripts = []
        self.adoptions = []

    def adopt_existing_tab(self, session_id, url_prefix, *, open_if_missing=False):
        assert open_if_missing is True
        self.adoptions.append((session_id, url_prefix))
        return {"ok": True}

    def invoke(self, capability_id, payload):
        self.invocations.append((capability_id, dict(payload)))
        return {"ok": True}

    def run_jev_session_script(self, session_id, script, *, timeout_seconds, package_root):
        assert package_root is not None
        self.scripts.append((session_id, script, timeout_seconds))
        return {
            "status": "POSTED",
            "post_id": "1234567890",
            "url": "https://x.com/caini56006962/status/1234567890",
            "handle": "caini56006962",
            "duplicate_found": False,
        }


def test_x_transport_uses_agent_runtime_browser_transaction(tmp_path, monkeypatch):
    fake = _FakeXBrowser()
    monkeypatch.setattr(
        LocalHarnessXTransport,
        "_provider",
        classmethod(lambda cls: fake),
    )
    import importlib.util
    from types import SimpleNamespace
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: SimpleNamespace(origin="/test/site-packages/jev_ultrafast/__init__.py") if name == "jev_ultrafast" else original(name))
    transport = LocalHarnessXTransport(
        state_root=tmp_path,
        expected_handle="caini56006962",
    )
    result = asyncio.run(transport.publish(text="single path"))

    assert result.provider == "JEV_AGENT_RUNTIME"
    assert result.post_id == "1234567890"
    assert fake.invocations == []
    assert fake.adoptions == [("ferro-x-publish", "https://x.com/")]
    assert len(fake.scripts) == 1
    assert "browser.observe" in fake.scripts[0][1]
    assert "browser.act" in fake.scripts[0][1]


def test_x_router_has_one_agent_runtime_transport(tmp_path, monkeypatch):
    expected = XBrowserPublishResult(
        post_id="42",
        url="https://x.com/u/status/42",
        handle="u",
        provider="AGENT_RUNTIME_BROWSER",
    )

    class _Transport:
        async def publish(self, *, text: str):
            assert text == "hello x"
            return expected

    monkeypatch.setattr(
        x_local_module.LocalHarnessXTransport,
        "from_environment",
        classmethod(lambda cls, *, state_root: _Transport()),
    )

    result = asyncio.run(
        XBrowserTransportRouter(state_root=tmp_path).publish(text="hello x")
    )
    assert result == expected


def test_live_bound_x_target_avoids_stale_legacy_page_probe(tmp_path, monkeypatch):
    fake = _FakeXBrowser()
    fake._load_target_id = lambda session: "live-x-target"
    monkeypatch.setattr(LocalHarnessXTransport, "_provider", classmethod(lambda cls: fake))
    monkeypatch.setattr(LocalHarnessXTransport, "_cdp_url", classmethod(lambda cls: "http://127.0.0.1:9601"))
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: io.StringIO(json.dumps([
        {"id": "live-x-target", "type": "page", "url": "https://x.com/caini56006962"}])))
    import importlib.util
    from types import SimpleNamespace
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: SimpleNamespace(origin="/test/site-packages/jev_ultrafast/__init__.py") if name == "jev_ultrafast" else original(name))
    transport = LocalHarnessXTransport(state_root=tmp_path, expected_handle="caini56006962")
    result = asyncio.run(transport.publish(text="resumed work"))
    assert result.post_id == "1234567890"
    assert fake.adoptions == []
    assert len(fake.scripts) == 1


def test_operator_browser_control_does_not_own_browser_process(tmp_path):
    control = LocalBrowserControl(state_root=tmp_path)
    assert not hasattr(control, "browser_factory")
    assert control.stop() == {"ok": True, "running": False}


def test_x_browser_harness_resolves_from_current_python_environment(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("", encoding="utf-8")
    harness = venv_bin / "browser-harness"
    harness.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.delenv("AGENT_RUNTIME_BROWSER_HARNESS", raising=False)
    monkeypatch.setattr(x_local_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(x_local_module.sys, "executable", str(python))

    assert LocalHarnessXTransport._harness_executable() == str(harness)


def test_operator_browser_harness_resolves_from_current_python_environment(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("", encoding="utf-8")
    harness = venv_bin / "browser-harness"
    harness.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.delenv("AGENT_RUNTIME_BROWSER_HARNESS", raising=False)
    monkeypatch.setattr(local_control_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(local_control_module.sys, "executable", str(python))

    assert LocalBrowserControl._harness_executable() == str(harness)


def test_x_focus_setup_timeout_rebinds_once_before_transaction(monkeypatch):
    transport = LocalHarnessXTransport.__new__(LocalHarnessXTransport)
    calls = []
    def run(script):
        calls.append(script)
        if len(calls) == 1:
            raise browser_runtime.BrowserRuntimeUnavailable("Emulation.setFocusEmulationEnabled timed out")
        return {"status": "POSTED"}
    rebounds = []
    monkeypatch.setattr(transport, "_run_jev_once", run)
    monkeypatch.setattr(transport, "_rebind_x_tab", lambda: rebounds.append(True))
    assert transport._run_jev_locked("transaction") == {"status": "POSTED"}
    assert calls == ["transaction", "transaction"] and rebounds == [True]


def test_x_other_transaction_error_does_not_retry(monkeypatch):
    transport = LocalHarnessXTransport.__new__(LocalHarnessXTransport)
    def run(script):
        raise browser_runtime.BrowserRuntimeUnavailable("input acknowledgement timed out")
    rebounds = []
    monkeypatch.setattr(transport, "_run_jev_once", run)
    monkeypatch.setattr(transport, "_rebind_x_tab", lambda: rebounds.append(True))
    with pytest.raises(browser_runtime.BrowserRuntimeUnavailable):
        transport._run_jev_locked("transaction")
    assert rebounds == []


@pytest.mark.parametrize("found", [True, False])
def test_x_uncertain_submit_only_reconciles_after_renderer_refresh(monkeypatch, found, tmp_path):
    transport = LocalHarnessXTransport.__new__(LocalHarnessXTransport)
    transport.expected_handle = "owner"
    transport.account_id = "default"
    transport.state_root = tmp_path
    calls = []
    def run(script, *, refresh_target=False):
        calls.append((script, refresh_target))
        if len(calls) == 1:
            return {"status": "PUBLICATION_UNVERIFIED", "stage": "verify",
                    "detail": "Runtime.evaluate timed out", "attempted": True}
        assert refresh_target is True and "verification_only" in script
        if found:
            return {"status": "POSTED", "post_id": "123", "url": "https://x.com/owner/status/123",
                    "handle": "owner", "duplicate_found": True}
        return {"status": "PUBLICATION_UNVERIFIED", "stage": "reconcile",
                "detail": "No matching public status", "attempted": True}
    monkeypatch.setattr(transport, "_run_jev_sync", run)
    if found:
        result = transport._publish_sync("Hello from Ferro")
        assert result.post_id == "123" and result.duplicate_found
    else:
        with pytest.raises(x_local_module.XBrowserPublicationUnverified):
            transport._publish_sync("Hello from Ferro")
    assert len(calls) == 2 and calls[0][1] is False
