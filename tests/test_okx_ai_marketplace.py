from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.okx_ai_marketplace import (
    OKXAIMarketplace,
    OnchainOSCLI,
    OnchainOSCommandError,
    ferro_registration_argv,
    ferro_validate_listing_argv,
)
from wechat_traffic_agent.resource_regulation import ESSENTIAL_TASKS


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        response = self.responses.pop(0)
        return SimpleNamespace(
            returncode=response.get("returncode", 0),
            stdout=response.get("stdout", "{}"),
            stderr=response.get("stderr", ""),
        )


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)


def _cli(*payloads):
    return OnchainOSCLI(
        runner=FakeRunner([{"stdout": json.dumps(row)} for row in payloads])
    )


def test_marketplace_sync_is_an_essential_task():
    assert "OKX_AI_MARKETPLACE" in ESSENTIAL_TASKS


def test_disabled_tick_does_not_touch_cli(tmp_path: Path):
    runner = FakeRunner([])
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=OnchainOSCLI(runner=runner),
        enabled=False,
    )
    assert market.tick(now=NOW)["status"] == "DISABLED"
    assert runner.calls == []


def test_tick_reports_not_registered_without_legacy_gate_check(tmp_path: Path):
    cli = _cli({"data": {"list": []}})
    market = OKXAIMarketplace(state_root=tmp_path, cli=cli, enabled=True)
    result = market.tick(now=NOW)
    assert result["status"] == "NOT_REGISTERED"
    assert cli.runner.calls == [
        ["onchainos", "agent", "get-my-agents", "--role", "asp"]
    ]


def test_tick_uses_v463_asp_list_and_status_read_only(tmp_path: Path):
    cli = _cli(
        {
            "data": {
                "list": [
                    {
                        "agentList": [
                            {"agentId": "9967", "name": "Ferro"}
                        ]
                    }
                ]
            }
        },
        {"data": {"payload": {"items": [{"jobId": "job_1"}]}}},
        {"data": {"payload": {"task": {"jobId": "job_1", "status": 1}}}},
    )
    market = OKXAIMarketplace(
        state_root=tmp_path, cli=cli, enabled=True, writes_enabled=False
    )
    result = market.tick(now=NOW)
    assert result["status"] == "OK"
    assert result["active_task_count"] == 1
    assert (
        tmp_path / "application/okx_ai_marketplace/tasks/job_1.json"
    ).exists()
    assert cli.runner.calls[1] == [
        "onchainos", "agent", "asp", "list-tasks",
        "--agent-id", "9967", "--page", "1", "--limit", "20",
    ]
    assert cli.runner.calls[2] == [
        "onchainos", "agent", "asp", "status", "job_1",
        "--agent-id", "9967",
    ]
    assert all("apply" not in call for call in cli.runner.calls)


def test_notification_requires_trusted_envelope_and_uses_next_action_once(tmp_path: Path):
    inbox = tmp_path / "application/okx_ai_marketplace/notifications/inbox"
    inbox.mkdir(parents=True)
    (inbox / "wake.json").write_text(
        json.dumps(
            {
                "agentId": "9967",
                "message": {
                    "source": "system",
                    "event": "job_asp_selected",
                    "jobId": "job_9",
                },
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner(
        [
            {"stdout": json.dumps({"data": {"payload": {"items": []}}})},
            {"stdout": "### Provider assignment\nRun the assignment decision flow."},
        ]
    )
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=OnchainOSCLI(runner=runner),
        agent_id="9967",
        enabled=True,
        writes_enabled=True,
    )
    result = market.tick(now=NOW)
    notification = result["notification_results"][0]
    assert notification["status"] == "RESOLVED"
    assert notification["event"] == "job_asp_selected"
    assert "playbook" in notification["next_action"]
    assert not (inbox / "wake.json").exists()
    assert sum("next-action" in call for call in runner.calls) == 1
    assert not any("deliver" in call for call in runner.calls)


def test_invalid_notification_remains_for_recovery(tmp_path: Path):
    inbox = tmp_path / "application/okx_ai_marketplace/notifications/inbox"
    inbox.mkdir(parents=True)
    path = inbox / "bad.json"
    path.write_text('{"message":{"event":"job_asp_selected"}}', encoding="utf-8")
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli({"data": {"payload": {"items": []}}}),
        agent_id="9967",
        enabled=True,
    )
    result = market.tick(now=NOW)
    assert result["notification_results"][0]["status"] == "ERROR"
    assert path.exists()
    assert (
        tmp_path / "application/okx_ai_marketplace/notifications/failed/bad.json"
    ).exists()


def test_accept_job_uses_current_provider_command(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli({"data": {"success": True}}),
        agent_id="9967",
        writes_enabled=True,
    )
    market.accept_job("job_1")
    assert market.cli.runner.calls[-1] == [
        "onchainos", "agent", "accept-job-by-provider", "job_1",
        "--agent-id", "9967",
    ]


def test_provider_mutations_require_write_authority(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli(),
        agent_id="9967",
        writes_enabled=False,
    )
    with pytest.raises(PermissionError):
        market.accept_job("job_1")
    with pytest.raises(PermissionError):
        market.deliver_text("job_1", text="done")


def test_decline_job_requires_bounded_reason(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli({"data": {"success": True}}),
        agent_id="9967",
        writes_enabled=True,
    )
    with pytest.raises(ValueError):
        market.decline_job("job_1", reason="")
    market.decline_job("job_1", reason="Requested work is outside the registered service.")
    assert market.cli.runner.calls[-1][:4] == [
        "onchainos", "agent", "decline-job-by-provider", "job_1"
    ]


def test_subscription_accept_uses_current_provider_command(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli({"data": {"success": True}}),
        agent_id="9967",
        writes_enabled=True,
    )
    market.accept_subscription("sub_1")
    assert market.cli.runner.calls[-1] == [
        "onchainos", "agent", "accept-subscription", "sub_1",
        "--agent-id", "9967",
    ]


def test_delivery_uses_single_authoritative_deliver_command(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli({"data": {"success": True}}),
        agent_id="9967",
        writes_enabled=True,
    )
    market.deliver_text("job_1", text="finished result")
    assert market.cli.runner.calls[-1] == [
        "onchainos", "agent", "deliver", "job_1",
        "--agent-id", "9967", "--deliverable-text", "finished result",
    ]


def test_json_command_fails_loud_on_non_json():
    cli = OnchainOSCLI(runner=FakeRunner([{"stdout": "not-json"}]))
    with pytest.raises(OnchainOSCommandError):
        cli.run("agent", "asp", "list-tasks", "--agent-id", "9967")


def test_next_action_preserves_markdown_playbook(tmp_path: Path):
    runner = FakeRunner([{"stdout": "### Provider assignment\nUse the provider router."}])
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=OnchainOSCLI(runner=runner),
        agent_id="9967",
    )
    result = market.next_action(
        message={
            "source": "system",
            "event": "job_asp_selected",
            "jobId": "job_1",
        }
    )
    assert result["playbook"].startswith("### Provider assignment")
    assert runner.calls[-1][0:3] == ["onchainos", "agent", "next-action"]


def test_next_action_rejects_eventless_message(tmp_path: Path):
    market = OKXAIMarketplace(
        state_root=tmp_path,
        cli=_cli(),
        agent_id="9967",
    )
    with pytest.raises(ValueError):
        market.next_action(message={"source": "system"})


def test_registration_services_match_v463_a2a_contract():
    argv = ferro_registration_argv(
        picture_url="https://static.okx.com/cdn/ferro.png"
    )
    services = json.loads(argv[argv.index("--service") + 1])
    assert [row["serviceType"] for row in services] == ["A2A", "A2A", "A2A"]
    assert [row["fee"] for row in services] == ["8", "12", "20"]
    assert [row["subscription"] for row in services] == [[], [], []]
    assert "validate-listing" in ferro_validate_listing_argv()
