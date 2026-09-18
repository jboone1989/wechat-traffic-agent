"""Private operator console primitives for secret handoff and LLM resource status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from .human_assistance import HumanAssistanceService

_SESSION_TTL = timedelta(minutes=30)
_SAFE_SECRET = re.compile(r"^[^\r\n\x00]{8,8192}$")
_QUOTA_UNITS = {"REQUESTS", "TOKENS", "CREDITS"}


@dataclass(frozen=True)
class SecretSpec:
    env_name: str
    label: str
    filename: str = "model-providers.env"
    quota_metadata: bool = True


SECRET_SPECS = {
    "CEREBRAS_API_KEY": SecretSpec("CEREBRAS_API_KEY", "Cerebras"),
    "NVIDIA_API_KEY": SecretSpec("NVIDIA_API_KEY", "NVIDIA NIM"),
    "OPENROUTER_API_KEY": SecretSpec("OPENROUTER_API_KEY", "OpenRouter"),
    "VIRTUALBRAIN_LLM_API_KEY": SecretSpec(
        "VIRTUALBRAIN_LLM_API_KEY", "DeepSeek stable reserve", quota_metadata=False
    ),
}


def _metadata_env_names(env_name: str) -> dict[str, str]:
    return {
        "quota_remaining": env_name + "_QUOTA_REMAINING",
        "quota_limit": env_name + "_QUOTA_LIMIT",
        "quota_unit": env_name + "_QUOTA_UNIT",
        "quota_reset_at": env_name + "_RESET_AT",
        "expires_at": env_name + "_EXPIRES_AT",
    }


def _safe_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("quota values must be finite and non-negative")
    return number


def _safe_time(value: object, field_name: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class OperatorSecretVault:
    """Allowlisted secret store. Secret values are never returned by status APIs."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.secret_root = self.state_root / "secrets"

    def _spec(self, env_name: str) -> SecretSpec:
        try:
            return SECRET_SPECS[str(env_name)]
        except KeyError as exc:
            raise ValueError("unsupported secret") from exc

    def _path(self, spec: SecretSpec) -> Path:
        return self.secret_root / spec.filename

    @staticmethod
    def _assignments(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            rows = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key:
                    rows[key] = value.strip()
            return rows
        except OSError:
            return {}

    def configured(self, env_name: str) -> bool:
        spec = self._spec(env_name)
        if str(os.environ.get(spec.env_name) or "").strip():
            return True
        return bool(self._assignments(self._path(spec)).get(spec.env_name))

    def metadata(self, env_name: str) -> dict[str, Any]:
        spec = self._spec(env_name)
        names = _metadata_env_names(spec.env_name)
        values = self._assignments(self._path(spec))

        def get(name: str) -> str:
            return str(os.environ.get(name) or values.get(name) or "").strip()

        if not spec.quota_metadata:
            return {
                "quota_supported": False,
                "quota_remaining": None,
                "quota_limit": None,
                "quota_unit": None,
                "quota_reset_at": None,
                "expires_at": None,
            }
        remaining = _safe_float(get(names["quota_remaining"]))
        limit = _safe_float(get(names["quota_limit"]))
        unit = get(names["quota_unit"]).upper() or "REQUESTS"
        if unit not in _QUOTA_UNITS:
            unit = "REQUESTS"
        return {
            "quota_supported": True,
            "quota_remaining": remaining,
            "quota_limit": limit,
            "quota_unit": unit,
            "quota_reset_at": get(names["quota_reset_at"]) or None,
            "expires_at": get(names["expires_at"]) or None,
        }

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for spec in SECRET_SPECS.values():
            path = self._path(spec)
            rows.append({
                "env_name": spec.env_name,
                "label": spec.label,
                "configured": self.configured(spec.env_name),
                "updated_at": (
                    datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                    if path.exists() else None
                ),
                **self.metadata(spec.env_name),
            })
        return rows

    def _write_values(self, spec: SecretSpec, updates: Mapping[str, str | None]) -> None:
        self.secret_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secret_root, 0o700)
        path = self._path(spec)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        keys = set(updates)
        output: list[str] = []
        written: set[str] = set()
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key not in keys:
                output.append(line)
                continue
            if key in written:
                continue
            value = updates[key]
            if value is not None:
                output.append(key + "=" + value)
            written.add(key)
        for key, value in updates.items():
            if key not in written and value is not None:
                output.append(key + "=" + value)

        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(output) + ("\n" if output else ""))
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def put(self, env_name: str, value: str) -> dict[str, Any]:
        spec = self._spec(env_name)
        secret = str(value).strip()
        if not _SAFE_SECRET.fullmatch(secret):
            raise ValueError("secret must be 8-8192 characters and contain no newlines")
        self._write_values(spec, {spec.env_name: secret})
        os.environ[spec.env_name] = secret
        return {
            "env_name": spec.env_name,
            "label": spec.label,
            "configured": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def put_metadata(self, env_name: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        spec = self._spec(env_name)
        if not spec.quota_metadata:
            raise ValueError("quota metadata is not supported for this resource")
        names = _metadata_env_names(spec.env_name)
        remaining = _safe_float(raw.get("quota_remaining"))
        limit = _safe_float(raw.get("quota_limit"))
        unit = str(raw.get("quota_unit") or "REQUESTS").strip().upper()
        if unit not in _QUOTA_UNITS:
            raise ValueError("quota_unit must be REQUESTS, TOKENS, or CREDITS")
        reset_at = _safe_time(raw.get("quota_reset_at"), "quota_reset_at")
        expires_at = _safe_time(raw.get("expires_at"), "expires_at")
        updates = {
            names["quota_remaining"]: None if remaining is None else format(remaining, ".15g"),
            names["quota_limit"]: None if limit is None else format(limit, ".15g"),
            names["quota_unit"]: unit,
            names["quota_reset_at"]: reset_at,
            names["expires_at"]: expires_at,
        }
        self._write_values(spec, updates)
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return {"env_name": spec.env_name, "label": spec.label, **self.metadata(spec.env_name)}


class OperatorAuthenticator:
    def __init__(self, state_root: Path, *, ttl: timedelta = _SESSION_TTL) -> None:
        self.secret_root = Path(state_root) / "secrets"
        self.token_path = self.secret_root / "operator-console.token"
        self.bootstrap_path = self.secret_root / "operator-console-bootstrap.json"
        self.ttl = ttl
        self._sessions: dict[str, datetime] = {}
        self._ensure_bootstrap_token()

    def _ensure_bootstrap_token(self) -> None:
        self.secret_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secret_root, 0o700)
        if not self.token_path.exists():
            token = secrets.token_urlsafe(32)
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token + "\n")
        os.chmod(self.token_path, 0o600)

    def _new_session(self, instant: datetime) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = instant + self.ttl
        self._prune(instant)
        return session_id

    def login(self, token: str, *, now: datetime | None = None) -> str | None:
        try:
            expected = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        supplied = str(token or "").strip()
        if not supplied or not hmac.compare_digest(supplied, expected):
            return None
        return self._new_session(now or datetime.now(timezone.utc))

    def issue_one_time_access(
        self,
        *,
        now: datetime | None = None,
        ttl: timedelta = timedelta(hours=2),
    ) -> str:
        """Create a one-use bootstrap secret; only its digest is persisted."""
        instant = now or datetime.now(timezone.utc)
        if ttl <= timedelta(0) or ttl > timedelta(hours=24):
            raise ValueError("bootstrap ttl must be within 24 hours")
        token = secrets.token_urlsafe(32)
        payload = {
            "schema_version": "operator-bootstrap.v1",
            "token_sha256": sha256(token.encode()).hexdigest(),
            "created_at": instant.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "expires_at": (instant + ttl).astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        }
        tmp = self.bootstrap_path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.bootstrap_path)
        os.chmod(self.bootstrap_path, 0o600)
        return token

    def consume_one_time_access(
        self, token: str, *, now: datetime | None = None
    ) -> str | None:
        instant = now or datetime.now(timezone.utc)
        try:
            payload = json.loads(self.bootstrap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            expires = datetime.fromisoformat(
                str(payload.get("expires_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if expires.tzinfo is None or expires.astimezone(timezone.utc) <= instant.astimezone(timezone.utc):
            try:
                self.bootstrap_path.unlink()
            except OSError:
                pass
            return None
        supplied = str(token or "").strip()
        expected = str(payload.get("token_sha256") or "")
        digest = sha256(supplied.encode()).hexdigest() if supplied else ""
        if not supplied or not hmac.compare_digest(digest, expected):
            return None
        try:
            self.bootstrap_path.unlink()
        except OSError:
            return None
        return self._new_session(instant)

    def valid(self, session_id: str | None, *, now: datetime | None = None) -> bool:
        if not session_id:
            return False
        instant = now or datetime.now(timezone.utc)
        self._prune(instant)
        expires = self._sessions.get(session_id)
        return bool(expires and expires > instant)

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def _prune(self, now: datetime) -> None:
        for key, expires in list(self._sessions.items()):
            if expires <= now:
                self._sessions.pop(key, None)


class OperatorConsole:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.vault = OperatorSecretVault(self.state_root)
        self.auth = OperatorAuthenticator(self.state_root)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def pending_requests(self) -> list[dict[str, Any]]:
        root = self.state_root / "application" / "human_assistance"
        rows: list[dict[str, Any]] = []
        for path in sorted(root.glob("human-request-*.json")) if root.exists() else []:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            credential_env = str(details.get("credential_env") or "")
            if credential_env not in SECRET_SPECS or row.get("resolution_status") == "FULFILLED":
                continue
            rows.append({
                "request_id": str(row.get("request_id") or ""),
                "urgency": str(row.get("urgency") or "NORMAL"),
                "summary": str(row.get("summary") or ""),
                "requested_action": str(row.get("requested_action") or ""),
                "created_at": row.get("created_at"),
                "credential_env": credential_env,
                "provider_label": str(details.get("provider_label") or SECRET_SPECS[credential_env].label),
                "signup_url": str(details.get("signup_url") or ""),
            })
        return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def resource_status(self) -> dict[str, Any]:
        root = self.state_root / "application" / "llm_resource_pool"
        manifest = self._load_json(root / "resources.json")
        policy = self._load_json(root / "policy.json")
        health_doc = self._load_json(root / "runtime" / "health.json")
        latest = self._load_json(root / "runtime" / "latest.json")
        health_rows = health_doc.get("resources")
        health_rows = health_rows if isinstance(health_rows, dict) else {}
        resources = []
        for raw in manifest.get("resources") or []:
            if not isinstance(raw, dict):
                continue
            resource_id = str(raw.get("resource_id") or "")
            health = health_rows.get(resource_id)
            health = health if isinstance(health, dict) else {}
            resources.append({
                "resource_id": resource_id,
                "provider_label": raw.get("provider_label"),
                "model": raw.get("model"),
                "pricing_class": raw.get("pricing_class"),
                "lifecycle": raw.get("lifecycle"),
                "reserve": bool(raw.get("reserve")),
                "quality_score": raw.get("quality_score"),
                "success_rate": raw.get("success_rate"),
                "mean_latency_ms": raw.get("mean_latency_ms"),
                "metrics_source": raw.get("metrics_source"),
                "capability_tier": raw.get("capability_tier"),
                "capability_score": raw.get("capability_score"),
                "capability_tags": list(raw.get("capability_tags") or []),
                "windfall_eligible": bool(raw.get("windfall_eligible")),
                "quota_remaining": raw.get("quota_remaining"),
                "quota_limit": raw.get("quota_limit"),
                "quota_unit": raw.get("quota_unit"),
                "quota_reset_at": raw.get("quota_reset_at"),
                "expires_at": raw.get("expires_at"),
                "successes": int(health.get("successes") or 0),
                "failures": int(health.get("failures") or 0),
                "consecutive_failures": int(health.get("consecutive_failures") or 0),
                "last_error_code": str(health.get("last_error_code") or ""),
                "cooldown_until": health.get("cooldown_until"),
                "last_success_at": health.get("last_success_at"),
                "last_failure_at": health.get("last_failure_at"),
                "consumed_quota": float(health.get("consumed_quota") or 0.0),
            })
        safe_policy = {
            key: policy.get(key)
            for key in (
                "observed_at", "routing_mode", "pressure", "prefer_free",
                "minimum_quality", "max_failovers", "preferred_lifecycles",
                "paid_available_funds", "paid_reserve_floor", "runway_days", "mind_tick",
                "windfall_active", "windfall_bonus", "windfall_mode",
                "windfall_resource_ids", "high_value_task_classes",
            )
        }
        safe_latest = {
            key: latest.get(key)
            for key in (
                "observed_at", "operation", "task_class", "selected_resource_id",
                "attempted_resource_ids", "result", "pressure", "mind_tick", "reason",
            )
        }
        return {
            "resource_count": len(resources),
            "free_count": sum(1 for row in resources if row["pricing_class"] == "FREE"),
            "paid_count": sum(1 for row in resources if row["pricing_class"] == "PAID"),
            "windfall_count": sum(1 for row in resources if row["windfall_eligible"]),
            "resources": resources,
            "policy": safe_policy,
            "latest_route": safe_latest,
        }

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "secrets": self.vault.status(),
            "pending_requests": self.pending_requests(),
            "llm_resources": self.resource_status(),
        }

    def _sync_pool(self) -> dict[str, Any]:
        try:
            from .llm_resources import sync_llm_resource_pool
            return sync_llm_resource_pool(state_root=self.state_root)
        except Exception as exc:
            return {"status": "SYNC_ERROR", "error_type": type(exc).__name__}

    def store_secret(
        self, env_name: str, value: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        result = self.vault.put(env_name, value)
        instant = now or datetime.now(timezone.utc)
        fulfilled = HumanAssistanceService(state_root=self.state_root).fulfill_matching(
            kind="MODEL_RESOURCE_ACCESS",
            detail_key="credential_env",
            detail_value=env_name,
            now=instant,
            evidence_refs=(f"secret-configured:{env_name}",),
        )
        return {
            **result,
            "fulfilled_request_ids": [
                str(row.get("request_id") or "") for row in fulfilled
            ],
            "resource_pool_sync": self._sync_pool(),
        }

    def store_resource_metadata(
        self, env_name: str, raw: Mapping[str, Any]
    ) -> dict[str, Any]:
        result = self.vault.put_metadata(env_name, raw)
        return {**result, "resource_pool_sync": self._sync_pool()}


def operator_console_enabled() -> bool:
    value = str(os.environ.get("CONTENT_OPERATOR_CONSOLE_ENABLED") or "")
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


__all__ = [
    "OperatorConsole",
    "OperatorSecretVault",
    "OperatorAuthenticator",
    "SECRET_SPECS",
    "operator_console_enabled",
]
