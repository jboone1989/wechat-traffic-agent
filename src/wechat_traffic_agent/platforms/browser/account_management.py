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

from wechat_traffic_agent.account_registry import AccountRegistry


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
        "Official X OAuth/API remains identity binding; all authenticated web automation uses the Agent Runtime persistent browser profile.",
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
        self, state_root: Path, *, xurl_status_provider=None
    ) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "accounts" / "accounts.json"
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
            and not self.registry.is_deleted(provider_id="x", identity=identity)
            and not any(row.identity.lower() == identity.lower() for row in existing_x)
        ):
            self.registry.ensure(
                provider_id="x",
                identity=identity,
                oauth_app="content-agent",
                oauth_user=identity,
                oauth_status="CONNECTED",
            )

        from wechat_traffic_agent.browser_runtime import browser_runtime_status

        browser_status = browser_runtime_status()
        x_profile = next(
            (
                row
                for row in browser_status.get("profiles", [])
                if row.get("profile_id") == "x"
            ),
            {"status": "UNAVAILABLE"},
        )
        web_status = (
            "CONNECTED"
            if x_profile.get("status") == "READY"
            else "DEGRADED"
        )
        rows: list[dict[str, Any]] = []
        for record in self.registry.list():
            spec = self.provider(record.provider_id)
            context = "default" if record.browser_profile_id == "default" else record.account_id
            verification_path = self.state_root / "accounts/browser-verifications" / (record.account_id + ".json")
            try:
                verification = json.loads(verification_path.read_text())
            except FileNotFoundError:
                verification = {}
            account_web_status = ("CONNECTED" if context == "default" else str(verification.get("status") or "NEEDS_LOGIN")) if web_status == "CONNECTED" else "DEGRADED"
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
                        "binding_id": "agent_runtime_browser",
                        "label": "Agent Runtime Browser",
                        "status": account_web_status,
                        "profile_id": "x",
                        "account_id": record.account_id,
                        "browser_context": context,
                        "automation_engine": "AGENT_RUNTIME_BROWSER",
                    },
                ],
                "automation": {
                    "primary_engine": "AGENT_RUNTIME_BROWSER",
                    "preferred_browser_provider": "AGENT_RUNTIME_BROWSER",
                    "compatibility_fallback": "",
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
        from agent_runtime.integrations import BROWSER_NAVIGATE
        from wechat_traffic_agent.x_local_browser import build_agent_runtime_x_browser

        browser = build_agent_runtime_x_browser()
        session_id = "ferro-x-account-verify"
        try:
            browser.invoke(
                BROWSER_NAVIGATE,
                {"session_id": session_id, "url": "https://x.com/home"},
            )
            body = browser.run_session_script(
                session_id,
                """profile = js(
                    "(() => document.querySelector('[data-testid=\"AppTabBar_Profile_Link\"]')"
                    "?.getAttribute('href') || '')()"
                ) or ''
info = page_info()
result = {'profile': profile, 'url': info.get('url', '')}
""",
                timeout_seconds=45,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return self._record(
                "x", status="DEGRADED", last_error=type(exc).__name__
            )

        profile = str(body.get("profile") or "")
        url = str(body.get("url") or "")
        if profile:
            identity = profile.strip("/").split("/", 1)[0]
            return self._record("x", status="CONNECTED", identity=identity)
        return self._record(
            "x",
            status="NEEDS_LOGIN",
            last_error="authenticated X profile was not visible at " + url[:120],
        )


__all__ = ["AccountManager", "AccountProvider"]
