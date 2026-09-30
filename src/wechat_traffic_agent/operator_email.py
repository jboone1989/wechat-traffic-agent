"""Email delivery for Ferro Operator one-time login tokens."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import parseaddr
import json
import os
from pathlib import Path
import smtplib
import ssl
from typing import Any

import httpx


class OperatorEmailError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperatorEmailConfig:
    provider: str = "RESEND"
    recipient: str = ""
    sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_security: str = "STARTTLS"
def _valid_email(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or len(raw) > 254 or any(ch in raw for ch in "\r\n\x00"):
        return False
    _name, address = parseaddr(raw)
    return (
        address == raw
        and "@" in address
        and not address.startswith("@")
        and not address.endswith("@")
        and "." in address.rsplit("@", 1)[-1]
    )


class OperatorEmailSettings:
    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "operator" / "email-delivery.json"

    def load(self) -> OperatorEmailConfig:
        if not self.path.exists():
            return OperatorEmailConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return OperatorEmailConfig()
        if not isinstance(raw, dict):
            return OperatorEmailConfig()
        defaults = asdict(OperatorEmailConfig())
        clean = {key: raw.get(key, value) for key, value in defaults.items()}
        try:
            clean["smtp_port"] = int(clean["smtp_port"])
        except (TypeError, ValueError):
            clean["smtp_port"] = 587
        return OperatorEmailConfig(**clean)

    def save_recipient(self, recipient: str) -> OperatorEmailConfig:
        return self.save({
            "provider": "RESEND",
            "recipient": str(recipient or "").strip(),
            "sender": "login@livingruntime.com",
        })

    def save(self, raw: dict[str, Any]) -> OperatorEmailConfig:
        provider = str(raw.get("provider") or "RESEND").strip().upper()
        if provider not in {"RESEND", "SMTP"}:
            raise ValueError("email provider must be RESEND or SMTP")
        recipient = str(raw.get("recipient") or "").strip()
        sender = str(raw.get("sender") or "").strip()
        if not _valid_email(recipient):
            raise ValueError("valid recipient email is required")
        if not _valid_email(sender):
            raise ValueError("valid sender email is required")
        smtp_host = str(raw.get("smtp_host") or "").strip()
        if len(smtp_host) > 253 or any(ch in smtp_host for ch in "\r\n\x00"):
            raise ValueError("invalid SMTP host")
        try:
            smtp_port = int(raw.get("smtp_port") or 587)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid SMTP port") from exc
        if not 1 <= smtp_port <= 65535:
            raise ValueError("invalid SMTP port")
        smtp_username = str(raw.get("smtp_username") or "").strip()
        if len(smtp_username) > 320 or any(ch in smtp_username for ch in "\r\n\x00"):
            raise ValueError("invalid SMTP username")
        security = str(raw.get("smtp_security") or "STARTTLS").strip().upper()
        if security not in {"STARTTLS", "TLS", "NONE"}:
            raise ValueError("SMTP security must be STARTTLS, TLS, or NONE")
        if provider == "SMTP" and not smtp_host:
            raise ValueError("SMTP host is required")
        config = OperatorEmailConfig(
            provider=provider,
            recipient=recipient,
            sender=sender,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_security=security,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(config), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
        return config
class OperatorEmailNotifier:
    def __init__(
        self,
        state_root: Path,
        *,
        http_client_factory=None,
        smtp_factory=None,
        smtp_ssl_factory=None,
    ) -> None:
        self.settings = OperatorEmailSettings(state_root)
        self.http_client_factory = http_client_factory or httpx.Client
        self.smtp_factory = smtp_factory or smtplib.SMTP
        self.smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL

    def status(self) -> dict[str, Any]:
        cfg = self.settings.load()
        resend_ready = bool(os.environ.get("RESEND_API_KEY", "").strip())
        smtp_password_ready = bool(
            os.environ.get("CONTENT_OPERATOR_SMTP_PASSWORD", "").strip()
        )
        secret_ready = resend_ready if cfg.provider == "RESEND" else (
            smtp_password_ready or not cfg.smtp_username
        )
        configured = bool(
            _valid_email(cfg.recipient)
            and _valid_email(cfg.sender)
            and secret_ready
            and (cfg.provider != "SMTP" or cfg.smtp_host)
        )
        return {
            "provider": cfg.provider,
            "configured": configured,
            "recipient": cfg.recipient,
            "sender": cfg.sender,
            "smtp_host": cfg.smtp_host,
            "smtp_port": cfg.smtp_port,
            "smtp_username": cfg.smtp_username,
            "smtp_security": cfg.smtp_security,
            "resend_key_configured": resend_ready,
            "smtp_password_configured": smtp_password_ready,
        }

    def send_login_token(self, *, token: str, expires_minutes: int = 10) -> dict[str, Any]:
        cfg = self.settings.load()
        status = self.status()
        if not status["configured"]:
            raise OperatorEmailError("operator email delivery is not configured")
        subject = "Ferro 管理后台一次性登录 Token"
        text = (
            "Ferro Operator Console 登录请求\n\n"
            f"一次性登录 Token，{expires_minutes} 分钟内有效且仅可使用一次：\n"
            f"{token}\n\n"
            "如果这不是你发起的请求，请忽略这封邮件。"
        )
        if cfg.provider == "RESEND":
            return self._send_resend(cfg, subject=subject, text=text)
        return self._send_smtp(cfg, subject=subject, text=text)
    def _send_resend(
        self, cfg: OperatorEmailConfig, *, subject: str, text: str
    ) -> dict[str, Any]:
        key = os.environ.get("RESEND_API_KEY", "").strip()
        try:
            with self.http_client_factory(timeout=12.0) as client:
                response = client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": cfg.sender,
                        "to": [cfg.recipient],
                        "subject": subject,
                        "text": text,
                    },
                )
        except Exception as exc:
            raise OperatorEmailError("email provider request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise OperatorEmailError(
                f"email provider rejected delivery ({response.status_code})"
            )
        return {"status": "SENT", "provider": "RESEND"}

    def _send_smtp(
        self, cfg: OperatorEmailConfig, *, subject: str, text: str
    ) -> dict[str, Any]:
        password = os.environ.get("CONTENT_OPERATOR_SMTP_PASSWORD", "")
        message = EmailMessage()
        message["From"] = cfg.sender
        message["To"] = cfg.recipient
        message["Subject"] = subject
        message.set_content(text)
        try:
            if cfg.smtp_security == "TLS":
                client = self.smtp_ssl_factory(
                    cfg.smtp_host,
                    cfg.smtp_port,
                    timeout=12,
                    context=ssl.create_default_context(),
                )
            else:
                client = self.smtp_factory(cfg.smtp_host, cfg.smtp_port, timeout=12)
            with client:
                if cfg.smtp_security == "STARTTLS":
                    client.starttls(context=ssl.create_default_context())
                if cfg.smtp_username:
                    client.login(cfg.smtp_username, password)
                client.send_message(message)
        except Exception as exc:
            raise OperatorEmailError("SMTP delivery failed") from exc
        return {"status": "SENT", "provider": "SMTP"}
__all__ = [
    "OperatorEmailConfig",
    "OperatorEmailError",
    "OperatorEmailNotifier",
    "OperatorEmailSettings",
]
