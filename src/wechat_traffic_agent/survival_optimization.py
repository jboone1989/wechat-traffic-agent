"""Conservative host self-recovery before habitat migration."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("survival optimization time requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _exc: None):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total

def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def persist_runtime_footprint(
    *, state_root: Path, release_root: Path, venv_root: Path, now: datetime,
) -> dict[str, Any]:
    root = Path(state_root).resolve() / "application" / "survival_recovery"
    path = root / "runtime-footprint.json"
    active_release = str(Path(release_root).resolve())
    active_venv = str(Path(venv_root).resolve())
    previous = None
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                if str(prior.get("active_venv_root") or "") == active_venv:
                    previous = prior.get("previous_active")
                else:
                    previous = {
                        "release_root": prior.get("active_release_root"),
                        "venv_root": prior.get("active_venv_root"),
                        "observed_at": prior.get("observed_at"),
                    }
        except (OSError, ValueError, json.JSONDecodeError):
            previous = None
    payload = {
        "schema": "content-runtime-footprint:v1",
        "observed_at": _iso(now),
        "active_release_root": active_release,
        "active_venv_root": active_venv,
        "previous_active": previous,
    }
    _atomic_json(path, payload)
    return payload


@dataclass(frozen=True)
class RecoveryCandidate:
    candidate_id: str
    kind: str
    path: str
    reclaimable_bytes: int
    auto_allowed: bool
    reason: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class HostRecoveryPlanner:
    """Reclaim regenerable local resources before considering migration."""

    TMP_PREFIXES = (
        "content-agent-test", "content-live-test", "content-web-outcome-test",
        "runtime-", "agent-runtime-", "ferro-", "lr-", "agent-network-ci-",
        "pwprobe", "codex-oss-browser",
    )

    def __init__(
        self, *, state_root: Path, target_free_gib: float = 12.0,
        home_root: Path | None = None, tmp_root: Path | None = None,
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.target_free_gib = float(target_free_gib)
        self.home_root = Path(home_root).resolve() if home_root is not None else Path.home()
        self.tmp_root = Path(tmp_root).resolve() if tmp_root is not None else Path("/tmp")
        self.root = self.state_root / "application" / "survival_recovery"

    @staticmethod
    def _active_roots() -> tuple[Path, ...]:
        active: set[Path] = set()
        proc = Path("/proc")
        if not proc.exists():
            return ()
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            for leaf in ("cwd", "exe"):
                try:
                    active.add((child / leaf).resolve(strict=True))
                except OSError:
                    continue
        return tuple(sorted(active, key=str))

    @staticmethod
    def _contains_active(path: Path, active: tuple[Path, ...]) -> bool:
        resolved = path.resolve()
        for item in active:
            if item == resolved or resolved in item.parents:
                return True
        return False

    @staticmethod
    def _playwright_old_versions(cache_root: Path) -> list[Path]:
        stale: list[Path] = []
        for prefix in ("chromium-", "chromium_headless_shell-"):
            rows = []
            for path in cache_root.glob(prefix + "*"):
                suffix = path.name.removeprefix(prefix)
                if path.is_dir() and suffix.isdigit():
                    rows.append((int(suffix), path))
            rows.sort()
            stale.extend(path for _version, path in rows[:-1])
        return stale

    @staticmethod
    def _referenced_venv_roots(active_venv: Path) -> set[Path]:
        pattern = re.compile(r"(/[^ \t\r\n\"']*content-agent-prod-venv-[^/ \t\r\n\"']+)")
        roots: set[Path] = set()
        files: list[Path] = []
        bin_root = active_venv / "bin"
        if bin_root.exists():
            files.extend(path for path in bin_root.iterdir() if path.is_file())
        lib_root = active_venv / "lib"
        if lib_root.exists():
            for site_root in lib_root.glob("python*/site-packages"):
                files.extend(site_root.glob("*.pth"))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:8192]
            except OSError:
                continue
            for match in pattern.findall(text):
                roots.add(Path(match).resolve())
        return roots

    def audit(self, *, now: datetime) -> dict[str, Any]:
        observed_at = _iso(now)
        home = self.home_root
        active = self._active_roots()
        candidates: list[RecoveryCandidate] = []

        safe_paths: list[tuple[str, Path, str]] = [
            ("PACKAGE_CACHE", home / ".cache" / "pip", "pip download cache is regenerable"),
            ("PACKAGE_CACHE", home / ".npm" / "_cacache", "npm content cache is regenerable"),
        ]
        playwright = home / ".cache" / "ms-playwright"
        safe_paths.extend(
            ("OLD_BROWSER_CACHE", path, "older Playwright browser version is regenerable")
            for path in self._playwright_old_versions(playwright)
        )

        tmp_root = self.tmp_root
        try:
            tmp_rows = list(tmp_root.iterdir())
        except OSError:
            tmp_rows = []
        for path in tmp_rows:
            if not path.is_dir() or not path.name.startswith(self.TMP_PREFIXES):
                continue
            try:
                age_seconds = now.timestamp() - path.stat().st_mtime
            except OSError:
                continue
            if age_seconds < 6 * 3600:
                continue
            safe_paths.append(("STALE_TEST_SCRATCH", path, "stale project test scratch is regenerable"))

        seen: set[str] = set()
        for kind, path, reason in safe_paths:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            key = str(resolved)
            if key in seen or not resolved.exists() or self._contains_active(resolved, active):
                continue
            seen.add(key)
            size = _tree_bytes(resolved)
            if size <= 0:
                continue
            digest = sha256((kind + "\0" + key).encode()).hexdigest()[:20]
            candidates.append(RecoveryCandidate(
                candidate_id="reclaim:" + digest,
                kind=kind,
                path=key,
                reclaimable_bytes=size,
                auto_allowed=True,
                reason=reason,
            ))

        footprint_path = self.root / "runtime-footprint.json"
        footprint_valid = False
        protected_venvs: set[Path] = set()
        if footprint_path.exists():
            try:
                footprint = json.loads(footprint_path.read_text(encoding="utf-8"))
                if isinstance(footprint, dict) and footprint.get("schema") == "content-runtime-footprint:v1":
                    footprint_valid = True
                    active_venv = footprint.get("active_venv_root")
                    if isinstance(active_venv, str) and active_venv:
                        active_venv_path = Path(active_venv).resolve()
                        protected_venvs.add(active_venv_path)
                        protected_venvs.update(self._referenced_venv_roots(active_venv_path))
                    previous = footprint.get("previous_active")
                    if isinstance(previous, dict):
                        prior_venv = previous.get("venv_root")
                        if isinstance(prior_venv, str) and prior_venv:
                            protected_venvs.add(Path(prior_venv).resolve())
            except (OSError, ValueError, json.JSONDecodeError):
                footprint_valid = False

        # Deployment environments become auto-reclaimable only when an authoritative
        # footprint proves they are neither active nor the retained rollback body.
        for path in sorted(home.glob("content-agent-prod-venv-*")):
            if not path.is_dir() or self._contains_active(path, active):
                continue
            resolved = path.resolve()
            if resolved in protected_venvs:
                continue
            size = _tree_bytes(path)
            if size <= 0:
                continue
            kind = "STALE_DEPLOYMENT_ENV" if footprint_valid else "DEPLOYMENT_RETENTION"
            digest = sha256((kind + "\0" + str(path)).encode()).hexdigest()[:20]
            candidates.append(RecoveryCandidate(
                candidate_id="reclaim:" + digest,
                kind=kind,
                path=str(path),
                reclaimable_bytes=size,
                auto_allowed=footprint_valid,
                reason=(
                    "runtime footprint proves this environment is outside active/rollback retention"
                    if footprint_valid else
                    "old deployment may be rollback material; runtime footprint proof required"
                ),
            ))

        disk = shutil.disk_usage(self.state_root)
        auto_bytes = sum(row.reclaimable_bytes for row in candidates if row.auto_allowed)
        managed_bytes = sum(row.reclaimable_bytes for row in candidates if not row.auto_allowed)
        payload = {
            "observed_at": observed_at,
            "disk_free_gib": round(disk.free / 1024**3, 3),
            "disk_used_ratio": round(disk.used / disk.total, 4) if disk.total else 1.0,
            "target_free_gib": self.target_free_gib,
            "auto_reclaimable_gib": round(auto_bytes / 1024**3, 3),
            "managed_reclaimable_gib": round(managed_bytes / 1024**3, 3),
            "candidates": [row.payload() for row in candidates],
        }
        _atomic_json(self.root / "audit-latest.json", payload)
        return payload

    def optimize(self, *, now: datetime) -> dict[str, Any]:
        audit = self.audit(now=now)
        before = shutil.disk_usage(self.state_root)
        target_bytes = int(self.target_free_gib * 1024**3)
        actions: list[dict[str, Any]] = []

        for raw in sorted(
            (row for row in audit["candidates"] if row["auto_allowed"]),
            key=lambda row: (-int(row["reclaimable_bytes"]), row["candidate_id"]),
        ):
            current = shutil.disk_usage(self.state_root)
            if current.free >= target_bytes:
                break
            path = Path(str(raw["path"]))
            if not path.exists() or self._contains_active(path, self._active_roots()):
                continue
            expected = int(raw["reclaimable_bytes"])
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                actions.append({
                    "candidate_id": raw["candidate_id"],
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                })
                continue
            actions.append({
                "candidate_id": raw["candidate_id"],
                "kind": raw["kind"],
                "status": "RECLAIMED",
                "expected_bytes": expected,
            })

        after = shutil.disk_usage(self.state_root)
        target_met = after.free >= target_bytes and (after.used / after.total if after.total else 1.0) < 0.90
        remaining = self.audit(now=now)

        auto_remaining = float(remaining["auto_reclaimable_gib"])
        auto_remaining_exists = any(
            bool(row.get("auto_allowed")) and int(row.get("reclaimable_bytes") or 0) > 0
            for row in remaining["candidates"]
        )
        managed_remaining_exists = any(
            not bool(row.get("auto_allowed")) and int(row.get("reclaimable_bytes") or 0) > 0
            for row in remaining["candidates"]
        )
        if target_met:
            status = "RECOVERED"
        elif auto_remaining_exists:
            status = "RETRY_AVAILABLE"
        elif managed_remaining_exists:
            status = "MANAGED_RETENTION_REQUIRED"
        else:
            status = "EXHAUSTED"
        payload = {
            "observed_at": _iso(now),
            "status": status,
            "before_free_gib": round(before.free / 1024**3, 3),
            "after_free_gib": round(after.free / 1024**3, 3),
            "reclaimed_gib": round((after.free - before.free) / 1024**3, 3),
            "target_free_gib": self.target_free_gib,
            "actions": actions,
            "remaining_auto_reclaimable_gib": auto_remaining,
            "remaining_managed_reclaimable_gib": float(remaining["managed_reclaimable_gib"]),
        }
        _atomic_json(self.root / "latest.json", payload)
        return payload


__all__ = ["RecoveryCandidate", "HostRecoveryPlanner", "persist_runtime_footprint"]
