from __future__ import annotations

import json

import pytest

from wechat_traffic_agent.operator_email import (
    OperatorEmailError,
    OperatorEmailNotifier,
    OperatorEmailSettings,
)


def test_email_settings_are_persistent_non_secret_and_mode_0600(tmp_path):
    settings = OperatorEmailSettings(tmp_path)
    config = settings.save({
        "provider": "RESEND",
        "recipient": "owner@example.com",
        "sender": "login@livingruntime.com",
    })

    assert config.provider == "RESEND"
    assert config.recipient == "owner@example.com"
    path = tmp_path / "operator" / "email-delivery.json"
    raw = path.read_text()
    assert json.loads(raw)["sender"] == "login@livingruntime.com"
    assert "api_key" not in raw.lower()
    assert "password" not in raw.lower()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
def test_email_settings_reject_invalid_addresses(tmp_path):
    settings = OperatorEmailSettings(tmp_path)

    with pytest.raises(ValueError, match="recipient"):
        settings.save({
            "provider": "RESEND",
            "recipient": "not-an-email",
            "sender": "login@livingruntime.com",
        })


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "email-test-123"}


class FakeHttp:
    def __init__(self, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        FakeHttp.last_call = (url, kwargs)
        return FakeResponse()


def test_resend_delivery_uses_secret_from_environment_without_exposing_token(tmp_path, monkeypatch):
    OperatorEmailSettings(tmp_path).save({
        "provider": "RESEND",
        "recipient": "owner@example.com",
        "sender": "login@livingruntime.com",
    })
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret")
    notifier = OperatorEmailNotifier(tmp_path, http_client_factory=FakeHttp)
    result = notifier.send_login_token(
        token="secret-one-time-token",
    )

    assert result == {"status": "SENT", "provider": "RESEND", "delivery_id": "email-test-123"}
    url, kwargs = FakeHttp.last_call
    assert url == "https://api.resend.com/emails"
    assert kwargs["headers"]["Authorization"] == "Bearer re_test_secret"
    assert kwargs["json"]["to"] == ["owner@example.com"]
    assert kwargs["json"]["from"] == "LivingRuntime <login@livingruntime.com>"
    assert kwargs["json"]["subject"].startswith("LivingRuntime 登录 Token · ")
    assert "secret-one-time-token" in kwargs["json"]["text"]
    assert "#bootstrap=" not in kwargs["json"]["text"]
    assert "re_test_secret" not in json.dumps(kwargs["json"])


def test_email_delivery_requires_provider_secret(tmp_path, monkeypatch):
    OperatorEmailSettings(tmp_path).save({
        "provider": "RESEND",
        "recipient": "owner@example.com",
        "sender": "login@livingruntime.com",
    })
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    notifier = OperatorEmailNotifier(tmp_path)
    assert notifier.status()["configured"] is False
    with pytest.raises(OperatorEmailError):
        notifier.send_login_token(
            token="token",
        )
