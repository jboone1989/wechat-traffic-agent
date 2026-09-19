from __future__ import annotations

import json

from wechat_traffic_agent.account_management import AccountManager


class FakeBrowser:
    def __init__(self, url: str, *, authenticated: bool = True):
        self.url = url
        self.authenticated = authenticated
        self.closed = False

    def evaluate(self, expression: str):
        if "AppTabBar_Profile_Link" in expression:
            return "/ferro_test" if self.authenticated else ""
        if expression == "location.href":
            return self.url if self.authenticated else "https://x.com/"
        raise AssertionError(expression)

    def close(self):
        self.closed = True


def test_account_catalog_exposes_x_and_future_adapter_slots(tmp_path):
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: "")
    rows = {row["provider_id"]: row for row in manager.status()["accounts"]}

    assert rows["x"]["implemented"] is True
    assert rows["x"]["status"] == "NEEDS_LOGIN"
    assert rows["youtube"]["implemented"] is False
    assert rows["youtube"]["status"] == "ADAPTER_NOT_INSTALLED"
    assert rows["email"]["implemented"] is False
    assert "PUBLISH" in rows["x"]["capabilities"]
def test_x_account_verification_persists_only_non_secret_metadata(tmp_path):
    made = []

    def factory(url):
        browser = FakeBrowser(url, authenticated=True)
        made.append(browser)
        return browser

    manager = AccountManager(
        tmp_path, browser_factory=factory, xurl_status_provider=lambda: ""
    )
    result = manager.verify("x")

    assert result["status"] == "CONNECTED"
    assert result["identity"] == "ferro_test"
    assert made[-1].closed is True

    raw = (tmp_path / "accounts" / "accounts.json").read_text()
    body = json.loads(raw)
    assert body["accounts"]["x"]["identity"] == "ferro_test"
    assert "password" not in raw.lower()
    assert "cookie" not in raw.lower()
    assert "token" not in raw.lower()


def test_x_account_verification_marks_login_needed_when_session_missing(tmp_path):
    manager = AccountManager(
        tmp_path,
        browser_factory=lambda url: FakeBrowser(url, authenticated=False),
        xurl_status_provider=lambda: "",
    )
    result = manager.verify("x")
    assert result["status"] == "NEEDS_LOGIN"
    assert result["identity"] == ""
    assert "authenticated X profile was not visible" in result["last_error"]


def test_x_account_distinguishes_api_oauth_from_browser_session(tmp_path):
    status = """▸ content-agent  [client_id: example]
      redirect_uri: http://localhost:8080/callback
      oauth2: caini56006962
      oauth1: –
      bearer: –

  default  [(no credentials)]
      oauth2: (none)
"""
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: status)
    row = next(x for x in manager.status()["accounts"] if x["provider_id"] == "x")

    assert row["status"] == "CONNECTED"
    assert row["identity"] == "caini56006962"
    bindings = {x["binding_id"]: x for x in row["bindings"]}
    assert bindings["api_oauth"]["status"] == "CONNECTED"
    assert bindings["api_oauth"]["login_kind"] == "REDIRECT"
    assert bindings["browser_session"]["status"] == "NEEDS_LOGIN"
    assert bindings["browser_session"]["purpose"] == "WEB_AUTOMATION"

def test_connected_x_account_keeps_browser_login_failure_scoped_to_browser_binding(tmp_path):
    oauth_status = """▸ content-agent  [client_id: example]
      oauth2: caini56006962
"""
    manager = AccountManager(
        tmp_path,
        browser_factory=lambda url: FakeBrowser(url, authenticated=False),
        xurl_status_provider=lambda: oauth_status,
    )

    checked = manager.verify("x")
    assert checked["status"] == "CONNECTED"
    assert checked["last_error"] == ""
    browser = next(x for x in checked["bindings"] if x["binding_id"] == "browser_session")
    assert browser["status"] == "NEEDS_LOGIN"
    assert "authenticated X profile was not visible" in browser["last_error"]

    manager.mark_authenticating("x")
    refreshed = next(x for x in manager.status()["accounts"] if x["provider_id"] == "x")
    browser = next(x for x in refreshed["bindings"] if x["binding_id"] == "browser_session")
    assert browser["last_error"] == ""


def test_account_status_exposes_multiple_x_instances_without_global_identity_collision(tmp_path, monkeypatch):
    status = """▸ content-agent  [client_id: example]
      oauth2: first_x
"""
    manager = AccountManager(tmp_path, xurl_status_provider=lambda: status)
    manager.registry.ensure(
        provider_id="x",
        identity="second_x",
        oauth_app="content-agent",
        oauth_user="second_x",
        oauth_status="CONNECTED",
    )
    monkeypatch.setenv("TINYFISH_API_KEY", "tinyfish-test-key")
    monkeypatch.setenv("TINYFISH_X_PROFILE_ID", "prof_first")

    body = manager.status()
    rows = {row["identity"]: row for row in body["instances"]}

    assert body["supports_multiple_accounts"] is True
    assert set(rows) == {"first_x", "second_x"}
    assert rows["first_x"]["oauth_user"] == "first_x"
    assert rows["second_x"]["oauth_user"] == "second_x"
    assert rows["first_x"]["automation"]["primary_engine"] == "JEV"
    assert rows["first_x"]["automation"]["compatibility_fallback"] == "TINYFISH"
    assert rows["second_x"]["automation"]["compatibility_fallback"] == ""
    assert rows["first_x"]["account_id"] != rows["second_x"]["account_id"]
