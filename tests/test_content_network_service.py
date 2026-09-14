from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.network_service import ContentNetworkService
from wechat_traffic_agent.runtime_app import CAP_CONTENT_STATUS


class _Runtime:
    def __init__(self, *, side_effect="READ_ONLY"):
        self.side_effect = side_effect

    def capability_manifest(self):
        return ({
            "capability_id": CAP_CONTENT_STATUS,
            "description": "status",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "side_effect_level": self.side_effect,
        },)


def test_network_advertises_only_explicit_read_only_runtime_capability(tmp_path):
    service = ContentNetworkService(
        root=tmp_path,
        application=SimpleNamespace(runtime=_Runtime()),
    )
    rows = service.advertised_capabilities()
    assert [row["id"] for row in rows] == [CAP_CONTENT_STATUS]
    assert rows[0]["side_effects"] == "NONE"
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
