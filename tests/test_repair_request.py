from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.repair_request import (
    RepairRequest,
    query_repair_status,
    submit_repair_request,
)
from wechat_traffic_agent.pi_remote_repair import (
    PiRemoteRepairProvider,
    configured_pi_remote_repair_provider,
)


def sample_request() -> RepairRequest:
    return RepairRequest(
        idempotency_key="self-problem:abc:proposal-1",
        source_problem_id="self-problem:abc",
        symptom="A bounded cognition cycle failed to parse evidence.",
        failure_site="content_loop.py:_structured",
        evidence=[{"type": "content-cycle", "id": "cycle:123"}],
        failed_tests=["tests/test_content_loop.py::test_invalid_output"],
        severity="LOW",
        risk="LOW",
        observed_world_impact="One cycle did not complete; broader impact is unknown.",
        target_files=["src/wechat_traffic_agent/content_loop.py"],
    )


def test_payload_uses_stable_pi_remote_field_names_and_no_authority():
    payload = sample_request().to_pi_remote_payload()
    assert payload["idempotencyKey"] == "self-problem:abc:proposal-1"
    assert payload["sourceProblemId"] == "self-problem:abc"
    assert payload["risk"] == "LOW"
    assert "credential" not in payload
    assert "git" not in payload
    assert "merge" not in payload
    assert "deploy" not in payload


def test_submit_invokes_only_request_code_repair():
    class Runtime:
        def __init__(self):
            self.calls = []

        def available_capabilities(self):
            return {"REQUEST_CODE_REPAIR"}

        def invoke(self, capability, payload):
            self.calls.append((capability, payload))
            return {"status": "RECEIVED", "requestId": "123", "ignored": {"raw": "not stored"}}

    runtime = Runtime()
    receipt = submit_repair_request(runtime, sample_request())
    assert [call[0] for call in runtime.calls] == ["REQUEST_CODE_REPAIR"]
    assert receipt == {"status": "RECEIVED", "requestId": "123"}


def test_missing_capability_fails_closed():
    class Runtime:
        def available_capabilities(self):
            return {"web.search"}

        def invoke(self, *_args):
            raise AssertionError("must not invoke an unavailable capability")

    assert submit_repair_request(Runtime(), sample_request()) == {"status": "CAPABILITY_UNAVAILABLE"}


def test_request_rejects_extra_authority_fields():
    payload = sample_request().model_dump()
    payload["canEdit"] = True
    with pytest.raises(Exception):
        RepairRequest.model_validate(payload)



def test_pi_remote_provider_invokes_only_fixed_request_cli_without_shell(tmp_path, monkeypatch):
    (tmp_path / "cli.js").write_text("// fixture")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "created": True,
                "request": {
                    "schema": "pi-remote.repair-request.v1",
                    "id": "00000000-0000-4000-8000-000000000001",
                    "status": "RECEIVED",
                },
            }),
            stderr="",
        )

    monkeypatch.setattr("wechat_traffic_agent.pi_remote_repair.subprocess.run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-pi-remote-request-process")
    provider = PiRemoteRepairProvider(root=tmp_path)
    manifest = provider.manifest()
    assert [row["capability_id"] for row in manifest["capabilities"]] == [
        "REQUEST_CODE_REPAIR",
        "QUERY_CODE_REPAIR_STATUS",
    ]
    assert manifest["capabilities"][1]["side_effect_level"] == "READ_ONLY"

    result = provider.invoke("REQUEST_CODE_REPAIR", sample_request().to_pi_remote_payload())

    assert result["status"] == "RECEIVED"
    assert result["created"] is True
    assert seen["argv"][2] == "repair-request"
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["cwd"] == provider.root
    assert seen["kwargs"]["timeout"] <= 30
    assert "git" not in " ".join(seen["argv"])
    assert "deploy" not in " ".join(seen["argv"])
    assert seen["kwargs"]["env"]["PI_REMOTE_REPAIR_REQUEST_ONLY"] == "1"
    assert "OPENAI_API_KEY" not in seen["kwargs"]["env"]


def test_configured_provider_recovers_sibling_pi_remote_when_self_repair_is_enabled(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "content-agent-self-repair-workspace"
    workspace.mkdir()
    pi_root = tmp_path / "src" / "pi-remote"
    pi_root.mkdir(parents=True)
    (pi_root / "cli.js").write_text("// repair cli")
    (pi_root / "repair-requests.js").write_text("// repair requests")
    monkeypatch.delenv("PI_REMOTE_REPAIR_ROOT", raising=False)
    monkeypatch.delenv("PI_REMOTE_ROOT", raising=False)
    monkeypatch.setenv("CONTENT_SELF_REPAIR_WORKSPACE", str(workspace))

    provider = configured_pi_remote_repair_provider()

    assert provider is not None
    assert provider.root == pi_root.resolve()


def test_configured_provider_does_not_autodiscover_without_self_repair_opt_in(monkeypatch):
    monkeypatch.delenv("PI_REMOTE_REPAIR_ROOT", raising=False)
    monkeypatch.delenv("PI_REMOTE_ROOT", raising=False)
    monkeypatch.delenv("CONTENT_SELF_REPAIR_WORKSPACE", raising=False)
    assert configured_pi_remote_repair_provider() is None



def test_query_repair_status_reads_rsi_selection():
    class Runtime:
        def __init__(self):
            self.calls = []

        def available_capabilities(self):
            return {"QUERY_CODE_REPAIR_STATUS"}

        def invoke(self, capability, payload):
            self.calls.append((capability, payload))
            return {
                "status": "RECEIVED",
                "requestId": "repair-123",
                "rsiStatus": "CANDIDATE_SELECTED",
                "rsiRound": 2,
                "selectedChildRequestId": "child-456",
                "selectedCheckpointId": "checkpoint-789",
                "selectedGitHead": "a" * 40,
                "selectedBaseCommit": "b" * 40,
            }

    runtime = Runtime()
    status = query_repair_status(runtime, "repair-123")

    assert runtime.calls == [
        ("QUERY_CODE_REPAIR_STATUS", {"requestId": "repair-123"})
    ]
    assert status["rsiStatus"] == "CANDIDATE_SELECTED"
    assert status["selectedChildRequestId"] == "child-456"
    assert status["selectedCheckpointId"] == "checkpoint-789"
    assert status["selectedBaseCommit"] == "b" * 40


def test_query_repair_status_invokes_real_capability_even_if_inventory_view_is_stale():
    class Runtime:
        def __init__(self):
            self.calls = []

        def available_capabilities(self):
            return set()

        def invoke(self, capability, payload):
            self.calls.append((capability, payload))
            return {
                "status": "RECEIVED",
                "requestId": "repair-stale-view",
                "rsiStatus": "CANDIDATE_SELECTED",
                "rsiRound": 1,
                "selectedChildRequestId": "child-real",
                "selectedCheckpointId": "checkpoint-real",
                "selectedGitHead": "c" * 40,
            }

    runtime = Runtime()
    status = query_repair_status(runtime, "repair-stale-view")

    assert runtime.calls == [
        ("QUERY_CODE_REPAIR_STATUS", {"requestId": "repair-stale-view"})
    ]
    assert status["rsiStatus"] == "CANDIDATE_SELECTED"
    assert status["selectedCheckpointId"] == "checkpoint-real"


def test_pi_remote_provider_reads_durable_rsi_status_without_credentials(
    tmp_path, monkeypatch
):
    (tmp_path / "cli.js").write_text("// fixture")
    (tmp_path / "repair-request-cli.js").write_text("// fixture")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "request": {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "status": "RECEIVED",
                },
                "rsi": {
                    "status": "CANDIDATE_SELECTED",
                    "roundIndex": 1,
                    "selection": {
                        "candidateId": "candidate-1",
                        "childRequestId": "child-1",
                        "checkpointId": "checkpoint-1",
                        "gitHead": "b" * 40,
                    },
                    "candidates": [{
                        "candidateId": "candidate-1",
                        "childRequestId": "child-1",
                        "fingerprint": "d" * 64,
                        "edits": [{
                            "path": "src/wechat_traffic_agent/content_service.py",
                            "newText": "except (ValidationError, ValueError)",
                        }],
                        "testEdits": [{
                            "path": "tests/test_content_agent.py",
                            "newText": "def test_content_agent_repairs_one_non_json_backend_response",
                        }],
                        "testPaths": ["tests/test_content_agent.py"],
                        "checkpoint": {
                            "baseCommit": "c" * 40,
                        },
                    }],
                },
            }),
            stderr="",
        )

    monkeypatch.setattr(
        "wechat_traffic_agent.pi_remote_repair.subprocess.run",
        fake_run,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-status-process")
    provider = PiRemoteRepairProvider(root=tmp_path)

    result = provider.invoke(
        "QUERY_CODE_REPAIR_STATUS",
        {"requestId": "00000000-0000-4000-8000-000000000001"},
    )

    assert result["rsiStatus"] == "CANDIDATE_SELECTED"
    assert result["selectedCheckpointId"] == "checkpoint-1"
    assert result["selectedBaseCommit"] == "c" * 40
    assert result["selectedFingerprint"] == "d" * 64
    assert result["selectedEdits"][0]["path"] == (
        "src/wechat_traffic_agent/content_service.py"
    )
    assert result["selectedTestEdits"][0]["path"] == "tests/test_content_agent.py"
    assert result["selectedTestPaths"] == ["tests/test_content_agent.py"]
    payload = json.loads(seen["argv"][2])
    assert payload == {
        "action": "status",
        "requestId": "00000000-0000-4000-8000-000000000001",
    }
    assert "OPENAI_API_KEY" not in seen["kwargs"]["env"]
