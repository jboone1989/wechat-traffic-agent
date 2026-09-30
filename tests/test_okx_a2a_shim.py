from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ferro_okx_a2a_codex_shim import main


def _env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("CONTENT_APPLICATION_ROOT", str(root))
    monkeypatch.setenv("OKX_A2A_CURRENT_SESSION_KEY", "job:job_9:session-agent:9967")
    monkeypatch.setenv("OKX_A2A_CURRENT_JOB_ID", "job_9")
    monkeypatch.setenv("OKX_A2A_CURRENT_AGENT_ID", "9967")
    monkeypatch.setenv("OKX_A2A_CURRENT_MESSAGE_ID", "msg_system_1")


def _system_payload() -> dict:
    return {
        "agentId": "9967",
        "message": {
            "source": "system",
            "event": "job_asp_selected",
            "jobId": "job_9",
            "providerAgentId": "9967",
            "clientAgentId": "5512",
        },
    }


def test_system_message_is_durably_enqueued_and_wakes_ferro(
    tmp_path: Path, monkeypatch, capsys
):
    _env(monkeypatch, tmp_path)
    prompt = json.dumps(_system_payload(), separators=(",", ":"))

    assert main(["exec", "--json", "--skip-git-repo-check", prompt]) == 0

    inbox = list(
        (
            tmp_path
            / "application"
            / "okx_ai_marketplace"
            / "notifications"
            / "inbox"
        ).glob("*.json")
    )
    assert len(inbox) == 1
    assert json.loads(inbox[0].read_text()) == _system_payload()

    wakes = list((tmp_path / "application" / "wake_requests").glob("okx-a2a-*.json"))
    assert len(wakes) == 1
    wake = json.loads(wakes[0].read_text())
    assert wake["wake_id"] == "content:OKX_AI_MARKETPLACE"
    assert wake["task"] == "OKX_AI_MARKETPLACE"
    assert wake["preemptible"] is False
    assert "okx-job:job_9" in wake["relevant_refs"]

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["type"] for row in output] == [
        "thread.started",
        "item.completed",
        "turn.completed",
    ]
    assert output[0]["thread_id"].startswith("ferro-okx-")


def test_duplicate_daemon_delivery_does_not_create_duplicate_work(
    tmp_path: Path, monkeypatch, capsys
):
    _env(monkeypatch, tmp_path)
    prompt = json.dumps(_system_payload(), separators=(",", ":"))

    main(["exec", "--json", prompt])
    capsys.readouterr()
    main(["exec", "--json", prompt])
    capsys.readouterr()

    assert len(
        list(
            (
                tmp_path
                / "application"
                / "okx_ai_marketplace"
                / "notifications"
                / "inbox"
            ).glob("*.json")
        )
    ) == 1
    assert len(list((tmp_path / "application" / "wake_requests").glob("*.json"))) == 1


def test_existing_codex_session_id_is_preserved(
    tmp_path: Path, monkeypatch, capsys
):
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("OKX_AGENT_TASK_CODEX_SESSION_ID", "existing-ferro-session")
    prompt = json.dumps(_system_payload())

    main(
        [
            "exec",
            "resume",
            "--json",
            "--skip-git-repo-check",
            "existing-ferro-session",
            prompt,
        ]
    )

    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first == {
        "type": "thread.started",
        "thread_id": "existing-ferro-session",
    }


def test_mismatched_agent_is_rejected(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    payload = _system_payload()
    payload["agentId"] = "other"

    with pytest.raises(ValueError, match="agentId"):
        main(["exec", "--json", json.dumps(payload)])


def test_mismatched_job_is_rejected(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    payload = _system_payload()
    payload["message"]["jobId"] = "other-job"

    with pytest.raises(ValueError, match="jobId"):
        main(["exec", "--json", json.dumps(payload)])


def test_peer_message_uses_separate_durable_inbox(
    tmp_path: Path, monkeypatch, capsys
):
    _env(monkeypatch, tmp_path)
    payload = {
        "agentId": "9967",
        "message": {
            "isDirectCommunication": True,
            "jobId": "job_9",
            "providerAgentId": "9967",
            "clientAgentId": "5512",
            "content": "Need one missing input.",
        },
    }

    main(["exec", "--json", json.dumps(payload)])
    capsys.readouterr()

    peer = list(
        (
            tmp_path
            / "application"
            / "okx_ai_marketplace"
            / "peer_messages"
            / "inbox"
        ).glob("*.json")
    )
    assert len(peer) == 1
    assert json.loads(peer[0].read_text()) == payload
