from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.network_service import ContentNetworkService, _request_data
from wechat_traffic_agent.runtime_app import CAP_CONTENT_STATUS
from wechat_traffic_agent.network_capabilities import (
    CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW, capability_specs,
)


class _Runtime:
    def __init__(self, *, side_effect="READ_ONLY"):
        self.side_effect = side_effect

    def capability_manifest(self):
        rows = [{
            "capability_id": CAP_CONTENT_STATUS,
            "description": "status",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "side_effect_level": self.side_effect,
        }]
        for row in capability_specs():
            rows.append({**row, "output_schema": {"type": "object"}, "side_effect_level": self.side_effect})
        return tuple(rows)


def test_network_advertises_content_work_capabilities_as_read_only(tmp_path):
    service = ContentNetworkService(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )
    rows = service.advertised_capabilities()
    assert [row["id"] for row in rows] == [
        CAP_CONTENT_STATUS, CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW,
    ]
    assert all(row["side_effects"] == "NONE" for row in rows)
    assert service.endpoint == "http://127.0.0.1:8103/a2a"
    assert service.identity_path == Path(tmp_path) / "application/network/identity.json"


def test_network_rejects_mutating_capability(tmp_path):
    service = ContentNetworkService(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime(side_effect="LOCAL_STATE")),
    )
    try:
        service.advertised_capabilities()
    except ValueError as exc:
        assert "must be READ_ONLY" in str(exc)
    else:
        raise AssertionError("mutating capability should fail closed")


def test_network_rejects_unregistered_requested_capability(tmp_path):
    service = ContentNetworkService(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
        capabilities=("content.missing",),
    )
    try:
        service.advertised_capabilities()
    except ValueError as exc:
        assert "not registered in Runtime" in str(exc)
    else:
        raise AssertionError("missing Runtime capability should fail closed")


def test_network_can_advertise_public_https_without_changing_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_NETWORK_PUBLIC_BASE_URL", "https://content.example.test/")
    service = ContentNetworkService.from_environment(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )

    assert service.bind_host == "127.0.0.1"
    assert service.endpoint == "https://content.example.test/a2a"
    assert service.card_url == "https://content.example.test/.well-known/agent-card.json"


def test_network_accepts_multiple_seed_urls_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CONTENT_NETWORK_SEED_URLS",
        "https://seed-a.example/a2a,https://seed-b.example/a2a/, https://seed-a.example/a2a",
    )
    service = ContentNetworkService.from_environment(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )

    assert service.seed_urls == (
        "https://seed-a.example/a2a",
        "https://seed-b.example/a2a",
    )
    assert service.seed_url == "https://seed-a.example/a2a"


def test_network_accepts_startup_timeout_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_NETWORK_STARTUP_TIMEOUT_SECONDS", "30")
    service = ContentNetworkService.from_environment(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )
    assert service.startup_timeout_seconds == 30.0


def test_network_accepts_membership_lease_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_NETWORK_LEASE_SECONDS", "60")
    service = ContentNetworkService.from_environment(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )
    assert service.lease_seconds == 60


def test_network_rejects_too_short_membership_lease(tmp_path):
    try:
        ContentNetworkService(
            root=tmp_path,
            application=SimpleNamespace(runtime=_Runtime()),
            lease_seconds=59,
        )
    except ValueError as exc:
        assert "at least 60" in str(exc)
    else:
        raise AssertionError("too-short membership lease should fail closed")


def test_signed_contract_capability_input_overrides_unsigned_message_body():
    payload = {
        "metadata": {
            "contract": {
                "contract_id": "contract-1",
                "metadata": {"capability_input": {"topic": "signed topic"}},
            }
        },
        "message": {
            "parts": [{"kind": "data", "data": {"topic": "tampered topic"}}],
        },
    }
    assert _request_data(payload) == {"topic": "signed topic"}


def test_signed_contract_without_capability_input_fails_closed_to_empty_payload():
    payload = {
        "metadata": {"contract": {"contract_id": "contract-1", "metadata": {}}},
        "message": {"parts": [{"kind": "data", "data": {"topic": "unsigned"}}]},
    }
    assert _request_data(payload) == {}
