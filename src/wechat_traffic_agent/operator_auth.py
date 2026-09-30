"""Operator authentication built on standard signing, sessions, and SQLite replay protection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

_ONE_TIME_SALT = "ferro-operator-login-v1"
_ONE_TIME_PURPOSE = "operator-login"


class OperatorAuthenticator:
    """Issue/consume signed one-time login tokens; browser sessions are Starlette-managed."""

    def __init__(self, state_root: Path) -> None:
        self.secret_root = Path(state_root) / "secrets"
        self.token_path = self.secret_root / "operator-console.token"
        self.replay_db_path = self.secret_root / "operator-login-replay.sqlite3"
        self._ensure_static_token()
        self._ensure_replay_store()
        self._serializer = URLSafeSerializer(
            self.session_secret,
            salt=_ONE_TIME_SALT,
        )

    def _ensure_static_token(self) -> None:
        self.secret_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secret_root, 0o700)
        if not self.token_path.exists():
            token = secrets.token_urlsafe(32)
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token + "\n")
        os.chmod(self.token_path, 0o600)

    @property
    def session_secret(self) -> str:
        return self.token_path.read_text(encoding="utf-8").strip()

    def verify_static_token(self, token: str) -> bool:
        supplied = str(token or "").strip()
        return bool(supplied) and hmac.compare_digest(supplied, self.session_secret)

    def _ensure_replay_store(self) -> None:
        self.secret_root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.replay_db_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS consumed_operator_tokens (
                    nonce TEXT PRIMARY KEY,
                    consumed_at TEXT NOT NULL
                )
                """
            )
            db.commit()
        os.chmod(self.replay_db_path, 0o600)

    def issue_one_time_access(
        self,
        *,
        now: datetime | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> str:
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if ttl <= timedelta(0) or ttl > timedelta(hours=24):
            raise ValueError("one-time token ttl must be within 24 hours")
        payload: dict[str, Any] = {
            "purpose": _ONE_TIME_PURPOSE,
            "nonce": secrets.token_urlsafe(18),
            "issued_at": instant.isoformat(),
            "expires_at": (instant + ttl).isoformat(),
        }
        return self._serializer.dumps(payload)

    def _decode_one_time_access(self, token: str) -> dict[str, Any] | None:
        supplied = str(token or "").strip()
        if not supplied:
            return None
        try:
            payload = self._serializer.loads(supplied)
        except BadSignature:
            return None
        if not isinstance(payload, dict) or payload.get("purpose") != _ONE_TIME_PURPOSE:
            return None
        nonce = str(payload.get("nonce") or "").strip()
        expires_raw = str(payload.get("expires_at") or "").strip()
        if not nonce or not expires_raw:
            return None
        return payload

    def _consume_nonce(self, nonce: str, *, consumed_at: datetime) -> bool:
        try:
            with sqlite3.connect(self.replay_db_path, timeout=5) as db:
                db.execute(
                    "INSERT INTO consumed_operator_tokens(nonce, consumed_at) VALUES (?, ?)",
                    (nonce, consumed_at.astimezone(timezone.utc).isoformat()),
                )
                db.commit()
        except sqlite3.IntegrityError:
            return False
        os.chmod(self.replay_db_path, 0o600)
        return True

    def consume_one_time_access(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        payload = self._decode_one_time_access(token)
        if payload is None:
            return False
        try:
            expires = datetime.fromisoformat(
                str(payload["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            return False
        if expires.tzinfo is None or expires.astimezone(timezone.utc) <= instant:
            return False
        return self._consume_nonce(str(payload["nonce"]), consumed_at=instant)

    def revoke_one_time_access(self, token: str | None = None) -> None:
        if not token:
            return
        payload = self._decode_one_time_access(token)
        if payload is None:
            return
        self._consume_nonce(
            str(payload["nonce"]),
            consumed_at=datetime.now(timezone.utc),
        )


__all__ = ["OperatorAuthenticator"]
