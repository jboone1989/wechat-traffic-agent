"""External account inventory and authentication lifecycle for Ferro.

Account metadata is non-secret. Credentials, passwords, verification codes and
browser cookies are never copied into this store.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .account_registry import AccountRegistry


@dataclass(frozen=True)
class AccountProvider:
    provider_id: str
    label: str
    auth_surface: str
    capabilities: tuple[str, ...]
    implemented: bool
    login_url: str | None = None
    note: str = ""


_PROVIDERS = {
    "x": AccountProvider(
        "x", "X", "MULTI_BINDING",
        ("READ", "PUBLISH", "MENTIONS", "METRICS"), True,
        "https://x.com/i/flow/login",
        "Official X OAuth/API is primary. TinyFish is the preferred authenticated web fallback; the local server browser is advanced recovery only.",
    ),
    "youtube": AccountProvider(
        "youtube", "YouTube", "PROVIDER_ADAPTER",
        ("READ", "PUBLISH", "ANALYTICS"), False, None,
        "Google/YouTube adapter slot. Prefer OAuth when implemented.",
    ),
    "email": AccountProvider(
        "email", "Email", "PROVIDER_ADAPTER",
        ("READ", "SEND", "SEARCH"), False, None,
        "Mailbox adapter slot. Provider-specific OAuth/app authentication belongs here.",
    ),
}


class AccountManager:
    def __init__(
        self, state_root: Path, *, browser_factory=None, xurl_status_provider=None
    ) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "accounts" / "accounts.json"
        self.browser_factory = browser_factory
        self.xurl_status_provider = xurl_status_provider
        self.registry = AccountRegistry(self.state_root)

    def provider(self, provider_id: str) -> AccountProvider:
        try:
            return _PROVIDERS[str(provider_id).strip().lower()]
        except KeyError as exc:
            raise ValueError("unsupported account provider") from exc

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "accounts": {}}
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(body, dict) and isinstance(body.get("accounts"), dict):
                return body
        except (OSError, json.JSONDecodeError):
            pass
        return {"schema_version": 1, "accounts": {}}

    def _write(self, body: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def _account_instances(self, oauth: dict[str, Any]) -> list[dict[str, Any]]:
        identity = str(oauth.get("identity") or "")
        existing_x = self.registry.list(provider_id="x")
        if (
            oauth.get("status") == "CONNECTED"
            and identity
            and not any(row.identity.lower() == identity.lower() for row in existing_x)
        ):
            self.registry.ensure(
                provider_id="x",
                identity=identity,
                oauth_app="content-agent",
                oauth_user=identity,
                oauth_status="CONNECTED",
            )
        tinyfish_ready = bool(
            os.environ.get("TINYFISH_API_KEY", "").strip()
            and os.environ.get("TINYFISH_X_PROFILE_ID", "").strip()
        )
        rows: list[dict[str, Any]] = []
        for record in self.registry.list():
            spec = self.provider(record.provider_id)
            profile_dir = (
                self.state_root
                / "accounts"
                / "browser-profiles"
                / record.account_id
                / "chrome-profile"
            )
            profile_status = "READY" if profile_dir.exists() else "NOT_PROVISIONED"
            compatibility = (
                "TINYFISH"
                if tinyfish_ready
                and record.provider_id == "x"
                and identity.lower() == record.identity.lower()
                else ""
            )
            rows.append({
                "account_id": record.account_id,
                "provider_id": record.provider_id,
                "label": spec.label,
                "identity": record.identity,
                "display_label": record.display_label,
                "status": record.oauth_status,
                "capabilities": list(spec.capabilities),
                "oauth_app": record.oauth_app,
                "oauth_user": record.oauth_user,
                "bindings": [
                    {
                        "binding_id": "api_oauth",
                        "label": "API OAuth",
                        "status": record.oauth_status,
                        "auth_mode": "OAUTH2_PKCE",
                    },
                    {
                        "binding_id": "browser_profile",
                        "label": "Account Browser Profile",
                        "status": profile_status,
                        "profile_id": record.browser_profile_id,
                        "automation_engine": record.automation_engine,
                    },
                ],
                "automation": {
                    "primary_engine": record.automation_engine,
                    "preferred_browser_provider": record.web_fallback_provider,
                    "compatibility_fallback": compatibility,
                },
            })
        return rows

    def status(self) -> dict[str, Any]:
        state = self._read()
        saved = state["accounts"]
        x_oauth = self._x_oauth_binding()
        rows = []
        for spec in _PROVIDERS.values():
            row = saved.get(spec.provider_id) or {}
            default_status = "NEEDS_LOGIN" if spec.implemented else "ADAPTER_NOT_INSTALLED"
            saved_status = str(row.get("status") or default_status)
            browser_status = (
                "NEEDS_LOGIN" if saved_status == "AUTHENTICATING" else saved_status
            )
            bindings: list[dict[str, Any]] = []
            overall_status = browser_status
            identity = str(row.get("identity") or "")
            if spec.provider_id == "x":
                oauth = x_oauth
                bindings.append(oauth)
                browser_error = str(row.get("last_error") or "")
                bindings.append({
                    "binding_id": "browser_session",
                    "label": "Browser Web Session",
                    "auth_mode": "PERSISTENT_BROWSER_SESSION",
                    "status": browser_status,
                    "identity": identity,
                    "purpose": "WEB_AUTOMATION",
                    "login_kind": "REMOTE_BROWSER",
                    "last_error": browser_error,
                })
                if oauth["status"] == "CONNECTED":
                    overall_status = "CONNECTED"
                    identity = identity or str(oauth.get("identity") or "")
            elif spec.provider_id == "youtube":
                bindings.append({
                    "binding_id": "oauth",
                    "label": "Google OAuth",
                    "auth_mode": "OAUTH2_REDIRECT",
                    "status": "ADAPTER_NOT_INSTALLED",
                    "identity": "",
                    "purpose": "API_IDENTITY",
                    "login_kind": "REDIRECT",
                })
            elif spec.provider_id == "email":
                bindings.append({
                    "binding_id": "oauth",
                    "label": "Mailbox OAuth",
                    "auth_mode": "OAUTH2_REDIRECT",
                    "status": "ADAPTER_NOT_INSTALLED",
                    "identity": "",
                    "purpose": "API_IDENTITY",
                    "login_kind": "REDIRECT",
                })
            rows.append({
                **asdict(spec),
                "capabilities": list(spec.capabilities),
                "status": overall_status,
                "identity": identity,
                "verified_at": row.get("verified_at"),
                "last_error": (
                    ""
                    if spec.provider_id == "x" and overall_status == "CONNECTED"
                    else str(row.get("last_error") or "")
                ),
                "bindings": bindings,
            })
        return {
            "schema_version": 3,
            "accounts": rows,
            "instances": self._account_instances(x_oauth),
            "supports_multiple_accounts": True,
        }

    def _x_oauth_binding(self) -> dict[str, Any]:
        status = ""
        try:
            if self.xurl_status_provider is not None:
                status = str(self.xurl_status_provider() or "")
            else:
                completed = subprocess.run(
                    ["xurl", "auth", "status"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                status = completed.stdout if completed.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            status = ""

        block = ""
        for candidate in re.split(r"\n\s*\n", status):
            if "content-agent" in candidate:
                block = candidate
                break
        match = re.search(r"oauth2:\s*([^\s]+)", block)
        identity = match.group(1) if match and match.group(1) != "(none)" else ""
        return {
            "binding_id": "api_oauth",
            "label": "X API OAuth",
            "auth_mode": "OAUTH2_PKCE",
            "status": "CONNECTED" if identity else "NEEDS_AUTHORIZATION",
            "identity": identity,
            "purpose": "API_IDENTITY",
            "login_kind": "REDIRECT",
            "headless_authorization_supported": True,
        }

    def _record(
        self, provider_id: str, *, status: str, identity: str = "",
        last_error: str = "", now: datetime | None = None,
    ) -> dict[str, Any]:
        spec = self.provider(provider_id)
        state = self._read()
        instant = now or datetime.now(timezone.utc)
        state["accounts"][spec.provider_id] = {
            "provider_id": spec.provider_id,
            "status": status,
            "identity": identity[:160],
            "verified_at": instant.isoformat(),
            "last_error": last_error[:240],
        }
        self._write(state)
        return next(x for x in self.status()["accounts"] if x["provider_id"] == spec.provider_id)

    def mark_authenticating(self, provider_id: str) -> dict[str, Any]:
        spec = self.provider(provider_id)
        if not spec.implemented:
            raise ValueError("account adapter is not installed")
        state = self._read()
        row = state["accounts"].get(spec.provider_id)
        if isinstance(row, dict) and row.get("last_error"):
            row["last_error"] = ""
            state["accounts"][spec.provider_id] = row
            self._write(state)
        return {
            "provider_id": spec.provider_id,
            "status": "AUTHENTICATING",
            "identity": "",
        }

    def record_connected(self, provider_id: str, *, identity: str = "") -> dict[str, Any]:
        spec = self.provider(provider_id)
        if not spec.implemented:
            raise ValueError("account adapter is not installed")
        return self._record(provider_id, status="CONNECTED", identity=identity)

    def mark_needs_login(self, provider_id: str, *, reason: str = "") -> dict[str, Any]:
        spec = self.provider(provider_id)
        if not spec.implemented:
            raise ValueError("account adapter is not installed")
        return self._record(provider_id, status="NEEDS_LOGIN", last_error=reason)

    def verify(self, provider_id: str) -> dict[str, Any]:
        spec = self.provider(provider_id)
        if not spec.implemented:
            raise ValueError("account adapter is not installed")
        if spec.provider_id != "x":
            raise ValueError("account verifier is not implemented")
        return self._verify_x()

    def _verify_x(self) -> dict[str, Any]:
        try:
            if self.browser_factory is None:
                from jev_ultrafast.browser import Browser
                factory = Browser
            else:
                factory = self.browser_factory
            browser = factory("https://x.com/home")
            try:
                profile = str(browser.evaluate(
                    "document.querySelector('[data-testid=\"AppTabBar_Profile_Link\"]')"
                    "?.getAttribute('href') || ''"
                ) or "")
                url = str(browser.evaluate("location.href") or "")
            finally:
                browser.close()
        except Exception as exc:
            return self._record(
                "x", status="DEGRADED", last_error=type(exc).__name__
            )

        if profile:
            identity = profile.strip("/").split("/", 1)[0]
            return self._record("x", status="CONNECTED", identity=identity)
        return self._record(
            "x", status="NEEDS_LOGIN",
            last_error="authenticated X profile was not visible at " + url[:120],
        )


__all__ = ["AccountManager", "AccountProvider"]
