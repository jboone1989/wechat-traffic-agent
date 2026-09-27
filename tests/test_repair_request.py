from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.repair_request import RepairRequest, submit_repair_request
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
    assert manifest["capabilities"] == [{
        "capability_id": "REQUEST_CODE_REPAIR",
        "description": "Submit an evidence-bounded Ferro repair request to Pi Remote.",
        "side_effect_level": "LOCAL_STATE",
        "required_permissions": ["repair.request"],
    }]

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
