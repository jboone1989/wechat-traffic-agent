from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.owner_reality import build_owner_reality


class FakeRuntime:
    def capability_manifest(self):
        return (
            {
                "capability_id": "web.search",
                "description": "Search the public web.",
                "side_effect_level": "READ_ONLY",
                "required_permissions": ["network.read"],
            },
            {
                "capability_id": "content.publish",
                "description": "Publish content.",
                "side_effect_level": "EXTERNAL_WRITE",
                "required_permissions": ["content.publish"],
            },
        )


class FakeAccountManager:
    def status(self):
        return {
            "accounts": [{
                "provider_id": "x",
                "label": "X",
                "identity": "owner_x",
                "status": "CONNECTED",
                "capabilities": ["READ", "PUBLISH"],
                "bindings": [{
                    "binding_id": "api_oauth",
                    "label": "X API OAuth",
                    "status": "CONNECTED",
                    "purpose": "API_IDENTITY",
                }],
            }],
            "instances": [{
                "account_id": "acct-x-1",
                "provider_id": "x",
                "identity": "owner_x",
                "display_label": "@owner_x",
                "status": "CONNECTED",
                "oauth_status": "CONNECTED",
                "automation": {"primary_engine": "JEV"},
            }],
        }
def test_owner_reality_contains_capabilities_accounts_and_exact_disk(tmp_path):
    application = SimpleNamespace(runtime=FakeRuntime())

    reality = build_owner_reality(
        state_root=tmp_path,
        application=application,
        account_manager=FakeAccountManager(),
    )

    caps = {row["capability_id"]: row for row in reality["runtime_capabilities"]}
    assert caps["web.search"]["side_effect_level"] == "READ_ONLY"
    assert caps["content.publish"]["side_effect_level"] == "EXTERNAL_WRITE"
    assert reality["accounts"]["accounts"][0]["status"] == "CONNECTED"
    assert reality["accounts"]["instances"][0]["oauth_status"] == "CONNECTED"
    assert reality["local_resources"]["filesystem"]["free_bytes"] > 0
    assert reality["execution_boundary"]["owner_chat_side_effect_execution"] is False
    assert "read_only_reality_snapshot" in reality["owner_chat_direct_capabilities"]
