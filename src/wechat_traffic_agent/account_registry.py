"""Non-secret multi-account registry.

Each provider identity gets a stable account_id. Secrets and browser cookies are
owned by their authentication backends, never copied into this registry.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

_SAFE_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9_.@+-]{1,160}$")
_SAFE_ACCOUNT_ID = re.compile(r"^acct_[a-z0-9_-]{1,32}_[0-9a-f]{12}$")


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    provider_id: str
    identity: str
    display_label: str
    oauth_app: str = ""
    oauth_user: str = ""
    oauth_status: str = "UNKNOWN"
    browser_profile_id: str = ""
    automation_engine: str = "JEV"
    web_fallback_provider: str = "LOCAL_JEV"
    created_at: str = ""
    updated_at: str = ""


def account_id_for(provider_id: str, identity: str) -> str:
    provider = str(provider_id).strip().lower()
    normalized = str(identity).strip().lstrip("@").lower()
    if not _SAFE_PROVIDER.fullmatch(provider):
        raise ValueError("invalid account provider")
    if not normalized or len(normalized) > 160:
        raise ValueError("invalid account identity")
    digest = hashlib.sha256(f"{provider}:{normalized}".encode()).hexdigest()[:12]
    return f"acct_{provider}_{digest}"


class AccountRegistry:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "accounts" / "instances.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "accounts": {}}
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "accounts": {}}
        if not isinstance(body, dict) or not isinstance(body.get("accounts"), dict):
            return {"schema_version": 1, "accounts": {}}
        return body

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

    def ensure(
        self,
        *,
        provider_id: str,
        identity: str,
        oauth_app: str = "",
        oauth_user: str = "",
        oauth_status: str = "CONNECTED",
        web_fallback_provider: str = "",
        now: datetime | None = None,
    ) -> AccountRecord:
        provider = str(provider_id).strip().lower()
        clean_identity = str(identity).strip().lstrip("@")
        if not _SAFE_PROVIDER.fullmatch(provider):
            raise ValueError("invalid account provider")
        if not _SAFE_IDENTITY.fullmatch(clean_identity):
            raise ValueError("invalid account identity")
        account_id = account_id_for(provider, clean_identity)
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        body = self._read()
        current = body["accounts"].get(account_id) or {}
        created_at = str(current.get("created_at") or instant.isoformat())
        record = AccountRecord(
            account_id=account_id,
            provider_id=provider,
            identity=clean_identity,
            display_label=f"@{clean_identity}" if provider == "x" else clean_identity,
            oauth_app=str(oauth_app or current.get("oauth_app") or "")[:80],
            oauth_user=str(oauth_user or current.get("oauth_user") or clean_identity)[:160],
            oauth_status=str(oauth_status or current.get("oauth_status") or "UNKNOWN")[:40],
            browser_profile_id=str(current.get("browser_profile_id") or "")[:160],
            automation_engine=str(current.get("automation_engine") or "JEV")[:40],
            web_fallback_provider=str(
                web_fallback_provider
                or current.get("web_fallback_provider")
                or "LOCAL_JEV"
            )[:40],
            created_at=created_at,
            updated_at=instant.isoformat(),
        )
        body["accounts"][account_id] = asdict(record)
        self._write(body)
        return record
    def get(self, account_id: str) -> AccountRecord:
        key = str(account_id).strip()
        if not _SAFE_ACCOUNT_ID.fullmatch(key):
            raise ValueError("invalid account_id")
        row = self._read()["accounts"].get(key)
        if not isinstance(row, dict):
            raise KeyError(key)
        return AccountRecord(**row)

    def list(self, *, provider_id: str | None = None) -> list[AccountRecord]:
        provider = None if provider_id is None else str(provider_id).strip().lower()
        rows = []
        for raw in self._read()["accounts"].values():
            if not isinstance(raw, dict):
                continue
            try:
                record = AccountRecord(**raw)
            except TypeError:
                continue
            if provider is None or record.provider_id == provider:
                rows.append(record)
        rows.sort(key=lambda row: (row.provider_id, row.identity.lower()))
        return rows

    def set_browser_profile(
        self, account_id: str, profile_id: str, *, now: datetime | None = None
    ) -> AccountRecord:
        record = self.get(account_id)
        body = self._read()
        row = dict(body["accounts"][record.account_id])
        row["browser_profile_id"] = str(profile_id)[:160]
        row["updated_at"] = (
            now or datetime.now(timezone.utc)
        ).astimezone(timezone.utc).isoformat()
        body["accounts"][record.account_id] = row
        self._write(body)
        return AccountRecord(**row)


__all__ = [
    "AccountRecord",
    "AccountRegistry",
    "account_id_for",
]
