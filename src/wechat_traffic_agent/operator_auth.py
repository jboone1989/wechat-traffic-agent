"""Operator authentication with short one-time codes and SQLite replay protection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3

_ONE_TIME_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ONE_TIME_CODE_LENGTH = 8


class OperatorAuthenticator:
    """Issue/consume short one-time login codes; browser sessions are Starlette-managed."""

    def __init__(self, state_root: Path) -> None:
        self.secret_root = Path(state_root) / "secrets"
        self.token_path = self.secret_root / "operator-console.token"
        self.replay_db_path = self.secret_root / "operator-login-replay.sqlite3"
        self._ensure_static_token()
        self._ensure_replay_store()

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
                CREATE TABLE IF NOT EXISTS operator_one_time_codes (
                    code_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
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
            raise ValueError("one-time code ttl must be within 24 hours")
        compact = "".join(
            secrets.choice(_ONE_TIME_CODE_ALPHABET)
            for _ in range(_ONE_TIME_CODE_LENGTH)
        )
        code = compact[:4] + "-" + compact[4:]
        digest = sha256(compact.encode()).hexdigest()
        with sqlite3.connect(self.replay_db_path, timeout=5) as db:
            db.execute(
                "INSERT INTO operator_one_time_codes(code_hash, expires_at, consumed_at) "
                "VALUES (?, ?, NULL)",
                (digest, (instant + ttl).isoformat()),
            )
            db.commit()
        os.chmod(self.replay_db_path, 0o600)
        return code

    @staticmethod
    def _normalize_one_time_code(token: str) -> str:
        compact = re.sub(r"[\s-]+", "", str(token or "").upper())
        return compact.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))

    def consume_one_time_access(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        compact = self._normalize_one_time_code(token)
        if (
            len(compact) != _ONE_TIME_CODE_LENGTH
            or any(ch not in _ONE_TIME_CODE_ALPHABET for ch in compact)
        ):
            return False
        digest = sha256(compact.encode()).hexdigest()
        with sqlite3.connect(self.replay_db_path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT expires_at, consumed_at FROM operator_one_time_codes "
                "WHERE code_hash=?",
                (digest,),
            ).fetchone()
            if row is None or row[1]:
                db.rollback()
                return False
            try:
                expires = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            except ValueError:
                db.rollback()
                return False
            if expires.tzinfo is None or expires.astimezone(timezone.utc) <= instant:
                db.rollback()
                return False
            updated = db.execute(
                "UPDATE operator_one_time_codes SET consumed_at=? "
                "WHERE code_hash=? AND consumed_at IS NULL",
                (instant.isoformat(), digest),
            ).rowcount
            db.commit()
            return updated == 1

    def revoke_one_time_access(self, token: str | None = None) -> None:
        if not token:
            return
        compact = self._normalize_one_time_code(token)
        if (
            len(compact) != _ONE_TIME_CODE_LENGTH
            or any(ch not in _ONE_TIME_CODE_ALPHABET for ch in compact)
        ):
            return
        digest = sha256(compact.encode()).hexdigest()
        with sqlite3.connect(self.replay_db_path, timeout=5) as db:
            db.execute(
                "UPDATE operator_one_time_codes SET consumed_at=? "
                "WHERE code_hash=? AND consumed_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), digest),
            )
            db.commit()


__all__ = ["OperatorAuthenticator"]
