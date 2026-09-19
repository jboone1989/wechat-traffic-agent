from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import wechat_traffic_agent.browser_runtime as browser_runtime
from wechat_traffic_agent.browser_runtime import AccountJevExecutor


class FakeProfiles:
    def ensure(self, account_id):
        return SimpleNamespace(
            account_id=account_id,
            cdp_url="http://127.0.0.1:9444",
        )


def test_account_jev_runs_in_isolated_worker_with_account_cdp(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "COMPLETED",
                "elapsed_ms": 17,
                "action_count": 2,
                "final_url": "https://example.com/",
                "failure_reason": None,
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(browser_runtime.subprocess, "run", fake_run)
    executor = AccountJevExecutor(
        state_root=tmp_path,
        profile_manager=FakeProfiles(),
    )
    result = asyncio.run(executor.run(
        account_id="acct_x_123456789abc",
        url="https://example.com",
        goal="Observe Example Domain and finish.",
        task_type="ACCOUNT_TEST",
    ))
    assert result.status == "COMPLETED"
    assert result.provider == "JEV_ACCOUNT"
    command, kwargs = calls[0]
    assert command[-1] == "wechat_traffic_agent.account_jev_worker"
    assert kwargs["env"]["BU_CDP_URL"] == "http://127.0.0.1:9444"
    assert kwargs["env"]["BU_NAME"].startswith("acct_")
    assert "Observe Example Domain" in kwargs["input"]

    raw = (tmp_path / "browser" / "runtime-events.jsonl").read_text()
    row = json.loads(raw)
    assert row["account_id"] == "acct_x_123456789abc"
    assert row["provider"] == "JEV_ACCOUNT"
    assert "Observe Example Domain" not in raw
