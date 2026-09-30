from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.okx_ai_marketplace import (
    OKXAIMarketplace,
    OnchainOSCLI,
    OnchainOSCommandError,
    ferro_registration_argv,
)
from wechat_traffic_agent.resource_regulation import ESSENTIAL_TASKS


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            returncode=response.get("returncode", 0),
            stdout=response.get("stdout", "{}"),
            stderr=response.get("stderr", ""),
        )


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)


def test_marketplace_sync_is_an_essential_task():
    assert "OKX_AI_MARKETPLACE" in ESSENTIAL_TASKS


def _cli(*payloads):
    import json

    return OnchainOSCLI(
        runner=FakeRunner([{"stdout": json.dumps(row)} for row in payloads])
    )


def test_disabled_tick_does_not_touch_cli(tmp_path: Path):
    runner = FakeRunner([])
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=OnchainOSCLI(runner=runner),
        enabled=False,
    )
    assert market.tick(now=NOW)["status"] == "DISABLED"
    assert runner.calls == []


def test_tick_mirrors_active_task_without_mutating_it(tmp_path: Path):
    cli = _cli(
        {"data": {"ready": True}},
        {"data": {"list": [{"agentList": [{"agentId": "asp_ferro", "name": "Ferro"}]}]}},
        {"data": {"tasks": [{"jobId": "job_1"}]}},
        {"data": {"jobId": "job_1", "status": "IN_PROGRESS"}},
    )
    runner = cli.runner
    market = OKXAIMarketplace(
        state_root=tmp_path, cli=cli, enabled=True, writes_enabled=False
    )
    result = market.tick(now=NOW)
    assert result["status"] == "OK"
    assert result["active_task_count"] == 1
    assert (tmp_path / "application/okx_ai_marketplace/tasks/job_1.json").exists()
    commands = [" ".join(call) for call in runner.calls]
    assert not any(" agent apply " in f" {command} " for command in commands)
    assert not any(" agent deliver " in f" {command} " for command in commands)


def test_notification_is_resolved_only_through_next_action(tmp_path: Path):
    inbox = tmp_path / "application/okx_ai_marketplace/notifications/inbox"
    inbox.mkdir(parents=True)
    (inbox / "wake.json").write_text(
        '{"message":{"event":"TASK_ASSIGNED","jobId":"job_9"}}', encoding="utf-8"
    )
    cli = _cli(
        {"data": {"ready": True}},
        {"data": {"tasks": []}},
        {"data": {"action": "WAIT_FOR_ESCROW", "jobId": "job_9"}},
    )
    runner = cli.runner
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=cli,
        agent_id="asp_ferro",
        enabled=True,
        writes_enabled=True,
    )
    result = market.tick(now=NOW)
    assert result["notification_results"][0]["status"] == "RESOLVED"
    assert not (inbox / "wake.json").exists()
    assert (
        tmp_path / "application/okx_ai_marketplace/notifications/processed/wake.json"
    ).exists()
    assert any("next-action" in call for call in runner.calls)
    assert not any("deliver" in call for call in runner.calls)


def test_quote_requires_write_authority(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli(),
        agent_id="asp_ferro",
        enabled=True,
        writes_enabled=False,
    )
    with pytest.raises(PermissionError):
        market.quote("job_1", amount="8")


def test_quote_uses_only_supported_settlement_tokens(tmp_path: Path):
    cli = _cli({"data": {"ok": True}})
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=cli,
        agent_id="asp_ferro",
        enabled=True,
        writes_enabled=True,
    )
    with pytest.raises(ValueError):
        market.quote("job_1", amount="8", symbol="BTC")
    result = market.quote("job_1", amount="8", symbol="USDT")
    assert result["data"]["ok"] is True


def test_non_json_cli_output_is_fail_loud():
    cli = OnchainOSCLI(runner=FakeRunner([{"stdout": "not-json"}]))
    with pytest.raises(OnchainOSCommandError):
        cli.run("agent", "gate-check", "--role", "asp")


def test_next_action_preserves_script_output(tmp_path: Path):
    runner = FakeRunner([{"stdout": "onchainos agent status job_1 --agent-id asp_ferro"}])
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=OnchainOSCLI(runner=runner),
        agent_id="asp_ferro",
        enabled=True,
    )
    result = market.next_action(
        message={"source": "system", "event": "job_created", "jobId": "job_1"}
    )
    assert result["script"].startswith("onchainos agent status job_1")


def test_claim_rewards_is_account_level(tmp_path: Path):
    cli = _cli({"data": {"claimed": "4.2"}})
    runner = cli.runner
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=cli,
        agent_id="asp_ferro",
        enabled=True,
        writes_enabled=True,
    )
    market.claim_rewards()
    assert runner.calls[-1] == [
        "onchainos", "agent", "asp-claim-rewards", "--agent-id", "asp_ferro"
    ]


def test_registration_profile_contains_three_a2a_services():
    argv = ferro_registration_argv(picture_url="https://example.com/ferro.png")
    service_json = argv[argv.index("--service") + 1]
    import json

    services = json.loads(service_json)
    assert [row["serviceType"] for row in services] == ["A2A", "A2A", "A2A"]
    assert [row["fee"] for row in services] == ["8", "12", "20"]
