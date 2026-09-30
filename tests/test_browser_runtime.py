from __future__ import annotations

import asyncio
import json

import pytest

from wechat_traffic_agent.browser_runtime import (
    JevBrowserExecutor,
    browser_runtime_status,
    jev_readiness,
)


class _FakeAgent:
    def __init__(self, url: str, goal: str) -> None:
        self.url = url
        self.goal = goal

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self):
        yield {
            "status": "ready",
            "elapsed_ms": 12,
            "page": {"url": self.url},
            "history": [{"kind": "click"}],
        }
        yield {
            "status": "done",
            "elapsed_ms": 19,
            "page": {"url": "https://example.test/done"},
            "history": [{"kind": "click"}, {"kind": "wait"}],
        }


def test_jev_readiness_is_disabled_under_chatgpt_only_policy(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy-value-that-must-not-be-used")
    monkeypatch.setenv("CONTENT_JEV_BROWSER_ENABLED", "true")
    row = jev_readiness()
    assert row["provider"] == "JEV"
    assert row["status"] == "DISABLED"
    assert row["chatgpt_exclusive"] is True


def test_chatgpt_exclusive_disables_external_jev_text_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("VIRTUALBRAIN_LLM_API_KEY", "deepseek-should-not-be-used")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-should-not-be-used")
    monkeypatch.setenv("CONTENT_JEV_TEXT_MODEL", "free-model")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe")

    row = jev_readiness()

    assert row["status"] == "DISABLED"
    assert row["chatgpt_exclusive"] is True
    assert "ChatGPT-exclusive" in row["reason"]


def test_browser_runtime_keeps_tinyfish_as_fallback(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "tinyfish-test-key")
    monkeypatch.setenv("TINYFISH_X_PROFILE_ID", "prof_test")
    status = browser_runtime_status()
    assert status["adaptive_default"] == "BROWSER_USE_CLI"
    assert status["routing_policy"] == "DETERMINISTIC_SKILL_OR_BROWSER_USE"
    assert status["x_fallback_policy"] == "OFFICIAL_API_THEN_ACCOUNT_JEV_THEN_EXISTING_PROFILE_JEV_THEN_TINYFISH_THEN_LEGACY_LOCAL_CDP"
    browser_use = next(
        row for row in status["providers"] if row["provider"] == "BROWSER_USE_CLI"
    )
    assert browser_use["browser_owner"] == "FERRO_NATIVE_CHROMIUM"
    tinyfish = next(row for row in status["providers"] if row["provider"] == "TINYFISH")
    assert tinyfish == {"provider": "TINYFISH", "status": "READY", "fallback_only": True}


def test_jev_executor_records_only_bounded_telemetry(tmp_path):
    secret_goal = "Open the page and type TOP-SECRET-DRAFT into the editor."
    executor = JevBrowserExecutor(state_root=tmp_path, agent_factory=_FakeAgent)
    result = asyncio.run(executor.run(
        url="https://example.test/start",
        goal=secret_goal,
        task_type="TEST_BROWSER",
    ))
    assert result.status == "COMPLETED"
    assert result.action_count == 2
    assert result.final_url == "https://example.test/done"

    raw = (tmp_path / "browser/runtime-events.jsonl").read_text()
    row = json.loads(raw)
    assert row["provider"] == "JEV"
    assert row["site"] == "example.test"
    assert row["task_type"] == "TEST_BROWSER"
    assert "TOP-SECRET-DRAFT" not in raw
    assert "goal" not in row
