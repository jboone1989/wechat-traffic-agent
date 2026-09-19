from __future__ import annotations

import pytest

from wechat_traffic_agent.local_browser_control import LocalBrowserControl


class FakeBrowser:
    def __init__(self, url: str):
        self.url = url
        self.target = "fake-target"
        self.calls = []
        self.closed = False

    def evaluate(self, expression: str):
        if expression == "location.href":
            return self.url
        if expression == "document.title":
            return "X"
        if "AppTabBar_Profile_Link" in expression:
            return False
        raise AssertionError(expression)

    def observe(self, screenshot=True):
        assert screenshot is True
        return {
            "url": self.url,
            "title": "X",
            "screenshot": "ZmFrZS1qcGVn",
        }

    def call(self, method: str, **kwargs):
        self.calls.append((method, kwargs))
        return {}

    def close(self):
        self.closed = True


def test_local_browser_control_is_restricted_to_x_and_never_persists_typed_text():
    made = []
    def factory(url):
        browser = FakeBrowser(url)
        made.append(browser)
        return browser

    control = LocalBrowserControl(browser_factory=factory)
    with pytest.raises(ValueError, match="restricted to x.com"):
        control.start("https://example.com")

    status = control.start("https://x.com/i/flow/login")
    assert status["running"] is True
    assert status["url"].startswith("https://x.com/")
    browser = made[-1]

    snap = control.snapshot()
    assert snap.screenshot_b64 == "ZmFrZS1qcGVn"
    control.click(x_ratio=0.5, y_ratio=0.25)
    control.type_text("secret-that-must-not-be-stored")
    control.key("Enter")

    inserted = [
        kwargs["text"]
        for method, kwargs in browser.calls
        if method == "Input.insertText"
    ]
    assert inserted == ["secret-that-must-not-be-stored"]
    assert "secret-that-must-not-be-stored" not in repr(control.__dict__)

    result = control.stop()
    assert result == {"ok": True, "running": False}
    assert browser.closed is True


def test_local_browser_control_rejects_bad_coordinates_and_keys():
    control = LocalBrowserControl(browser_factory=FakeBrowser)
    control.start("https://x.com/")
    with pytest.raises(ValueError, match="normalized"):
        control.click(x_ratio=-0.1, y_ratio=0.5)
    with pytest.raises(ValueError, match="unsupported key"):
        control.key("F12")


def test_snapshot_retries_transient_navigation_failures():
    class FlakyBrowser(FakeBrowser):
        def __init__(self, url):
            super().__init__(url)
            self.observe_calls = 0

        def observe(self, screenshot=True):
            self.observe_calls += 1
            if self.observe_calls < 3:
                raise RuntimeError("document changed during navigation")
            return super().observe(screenshot=screenshot)

    made = []
    def factory(url):
        browser = FlakyBrowser(url)
        made.append(browser)
        return browser

    control = LocalBrowserControl(browser_factory=factory)
    control.start("https://x.com/i/flow/login")
    snap = control.snapshot()
    assert snap.screenshot_b64 == "ZmFrZS1qcGVn"
    assert made[-1].observe_calls == 3


def test_local_browser_target_survives_process_object_restart(tmp_path, monkeypatch):
    closed = []
    monkeypatch.setattr(
        LocalBrowserControl,
        "_close_target",
        staticmethod(lambda target_id: closed.append(target_id)),
    )

    first = LocalBrowserControl(browser_factory=FakeBrowser, state_root=tmp_path)
    first.start("https://x.com/i/flow/login")
    state_path = tmp_path / "operator" / "browser-session-target.json"

    assert state_path.exists()
    assert oct(state_path.stat().st_mode & 0o777) == "0o600"
    assert first.target_id() == "fake-target"

    restarted = LocalBrowserControl(browser_factory=FakeBrowser, state_root=tmp_path)
    assert restarted.target_id() == "fake-target"

    restarted.stop()
    assert closed == ["fake-target"]
    assert not state_path.exists()
