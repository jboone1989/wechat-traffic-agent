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
    manager = AccountManager(tmp_path)
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

    manager = AccountManager(tmp_path, browser_factory=factory)
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
    )
    result = manager.verify("x")
    assert result["status"] == "NEEDS_LOGIN"
    assert result["identity"] == ""
    assert "authenticated X profile was not visible" in result["last_error"]
