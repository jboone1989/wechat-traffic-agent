from __future__ import annotations

import json

from wechat_traffic_agent.account_registry import AccountRegistry, account_id_for


def test_registry_supports_multiple_accounts_for_same_provider(tmp_path):
    registry = AccountRegistry(tmp_path)
    first = registry.ensure(
        provider_id="x",
        identity="alice_one",
        oauth_app="content-agent",
        oauth_user="alice_one",
    )
    second = registry.ensure(
        provider_id="x",
        identity="bob_two",
        oauth_app="content-agent",
        oauth_user="bob_two",
    )

    assert first.account_id != second.account_id
    assert first.account_id == account_id_for("x", "alice_one")
    assert second.account_id == account_id_for("x", "@bob_two")
    assert [row.identity for row in registry.list(provider_id="x")] == [
        "alice_one",
        "bob_two",
    ]
def test_registry_is_non_secret_and_account_profiles_are_independent(tmp_path):
    registry = AccountRegistry(tmp_path)
    first = registry.ensure(provider_id="x", identity="alice_one")
    second = registry.ensure(provider_id="x", identity="bob_two")

    registry.set_browser_profile(first.account_id, "browser_alice")
    registry.set_browser_profile(second.account_id, "browser_bob")

    assert registry.get(first.account_id).browser_profile_id == "browser_alice"
    assert registry.get(second.account_id).browser_profile_id == "browser_bob"
    raw = (tmp_path / "accounts" / "instances.json").read_text()
    body = json.loads(raw)
    assert len(body["accounts"]) == 2
    assert "password" not in raw.lower()
    assert "cookie" not in raw.lower()
    assert "access_token" not in raw.lower()
    assert oct((tmp_path / "accounts" / "instances.json").stat().st_mode & 0o777) == "0o600"
