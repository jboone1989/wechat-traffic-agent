"""Adaptive storage inventory, reclamation, and outcome learning for Ferro."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


INITIAL_RECLAIM_RATIO = 0.55
DEEP_RECLAIM_RATIO = 0.85
INITIAL_RECLAIM_LIMIT = 2 * 1024**3
DEEP_RECLAIM_LIMIT = 12 * 1024**3


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("storage timestamp requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


class StorageLifecycle:
    """Inspect broadly, reclaim bounded rebuildable storage, and learn from outcomes."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root).resolve()
        raw_root = str(os.environ.get("CONTENT_SURVIVAL_STORAGE_ROOT") or "").strip()
        if raw_root:
            self.storage_root = Path(raw_root).expanduser().resolve()
        else:
            home = Path.home().resolve()
            try:
                self.state_root.relative_to(home)
                self.storage_root = home
            except ValueError:
                # Tests and isolated runtimes should never scan the operator's real home.
                self.storage_root = self.state_root
        root = self.state_root / "application" / "survival_resources" / "storage"
        self.inventory_path = root / "inventory.json"
        self.knowledge_path = root / "knowledge.json"
        self.pending_path = root / "pending_outcomes.json"

    @staticmethod
    def mode(used_ratio: float) -> str:
        if used_ratio >= DEEP_RECLAIM_RATIO:
            return "DEEP"
        if used_ratio >= INITIAL_RECLAIM_RATIO:
            return "INITIAL"
        return "NORMAL"

    def observe(self, *, now: datetime, used_ratio: float) -> dict[str, Any]:
        self._settle_learning(now=now)
        mode = self.mode(used_ratio)
        if mode == "NORMAL":
            payload = {
                "schema_version": "ferro-storage-inventory.v1",
                "observed_at": _iso(now),
                "mode": mode,
                "root": str(self.storage_root),
                "candidate_count": 0,
                "estimated_reclaimable_bytes": 0,
                "candidates": [],
            }
            _atomic(self.inventory_path, payload)
            return payload
        candidates = self.inventory(now=now, deep=(mode == "DEEP"))
        payload = {
            "schema_version": "ferro-storage-inventory.v1",
            "observed_at": _iso(now),
            "mode": mode,
            "root": str(self.storage_root),
            "candidate_count": len(candidates),
            "learning_candidate_count": sum(1 for row in candidates if row.get("recommended_action") != "RECLAIM" and not row.get("protected")),
            "unknown_candidate_count": sum(1 for row in candidates if row.get("kind") == "UNKNOWN" and not row.get("protected")),
            "estimated_reclaimable_bytes": sum(
                int(row.get("size_bytes") or 0)
                for row in candidates
                if row.get("recommended_action") == "RECLAIM"
            ),
            "candidates": candidates[:120],
        }
        _atomic(self.inventory_path, payload)
        return payload

    def status(self) -> dict[str, Any]:
        return {
            "inventory": _load(self.inventory_path, {}),
            "knowledge": _load(self.knowledge_path, {"profiles": {}}),
            "pending_outcomes": _load(self.pending_path, []),
        }

    def inventory(self, *, now: datetime, deep: bool) -> list[dict[str, Any]]:
        if not self.storage_root.is_dir():
            return []
        active_refs = self._active_references()
        raw: list[dict[str, Any]] = []
        for path in self._candidate_paths(deep=deep):
            row = self._describe(path=path, now=now, active_refs=active_refs)
            if row is not None:
                raw.append(row)

        family_rows: dict[str, list[dict[str, Any]]] = {}
        for row in raw:
            family_rows.setdefault(str(row["family"]), []).append(row)
        for rows in family_rows.values():
            rows.sort(key=lambda item: float(item["mtime"]), reverse=True)
            for rank, row in enumerate(rows):
                row["family_count"] = len(rows)
                row["family_rank"] = rank
                row["newest_in_family"] = rank == 0

        knowledge = _load(self.knowledge_path, {"profiles": {}})
        profiles = dict(knowledge.get("profiles") or {})
        worktree_checks_remaining = 40
        for row in raw:
            family = str(row["family"])
            kind = str(row["kind"])
            age_hours = float(row.get("age_hours") or 0.0)
            if (
                deep
                and kind == "CODE_WORKTREE"
                and bool(row.get("worktree_detached"))
                and not row.get("protected")
                and int(row.get("family_rank") or 0) >= 2
                and age_hours >= 24.0
                and worktree_checks_remaining > 0
            ):
                row["worktree_reclaimable"] = self._git_worktree_clean(Path(str(row["path"])))
                worktree_checks_remaining -= 1
            signature = self._signature(row)
            row["signature"] = signature
            profile = dict(profiles.get(signature) or {})
            confidence = self._confidence(row=row, profile=profile)
            row["reclaim_confidence"] = round(confidence, 4)
            threshold = 0.80 if deep else 0.97
            worktree_ok = (
                deep
                and kind == "CODE_WORKTREE"
                and bool(row.get("worktree_reclaimable"))
                and int(row.get("family_rank") or 0) >= 2
                and age_hours >= 24.0
            )
            ordinary_ok = (
                kind not in {"UNKNOWN", "CODE_WORKTREE", "STATE", "SECRET"}
                and (kind == "CACHE" or age_hours >= (6.0 if deep else 24.0))
            )
            eligible = (
                not row["protected"]
                and confidence >= threshold
                and (worktree_ok or ordinary_ok)
            )
            row["recommended_action"] = "RECLAIM" if eligible else "KEEP_OR_LEARN"

        # Size only the highest-value reclaim candidates. Full recursive sizing of
        # every directory is too expensive on a long-lived host and teaches less
        # than classification metadata does.
        reclaim_rows = [
            row for row in raw if row.get("recommended_action") == "RECLAIM"
        ]
        reclaim_rows.sort(
            key=lambda row: (
                -float(row.get("reclaim_confidence") or 0.0),
                -float(row.get("age_hours") or 0.0),
            )
        )
        for row in reclaim_rows[:24]:
            if not row.get("size_known"):
                row["size_bytes"] = self._path_size(Path(str(row["path"])))
                row["size_known"] = True

        return sorted(
            raw,
            key=lambda row: (
                row["recommended_action"] != "RECLAIM",
                -float(row["reclaim_confidence"]),
                -int(row.get("size_bytes") or 0),
            ),
        )

    def reclaim(self, *, now: datetime, deep: bool) -> dict[str, Any]:
        before = shutil.disk_usage(self.state_root)
        tmp_result = self._reclaim_state_tmp(now=now)
        candidates = self.inventory(now=now, deep=deep)
        cap = DEEP_RECLAIM_LIMIT if deep else INITIAL_RECLAIM_LIMIT
        reclaimed = int(tmp_result["reclaimed_bytes"])
        deleted: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for row in candidates:
            if row.get("recommended_action") != "RECLAIM":
                continue
            if reclaimed >= cap:
                break
            path = Path(str(row["path"]))
            size = int(row.get("size_bytes") or 0)
            if size <= 0:
                size = self._path_size(path)
                row["size_bytes"] = size
            if size <= 0:
                continue
            try:
                self._delete_candidate(path=path, kind=str(row["kind"]))
                reclaimed += size
                deleted.append({
                    "path": str(path),
                    "kind": row["kind"],
                    "family": row["family"],
                    "signature": row["signature"],
                    "size_bytes": size,
                    "confidence": row["reclaim_confidence"],
                })
            except OSError as exc:
                self._record_learning(
                    signature=str(row["signature"]),
                    outcome="DELETE_FAILED",
                    now=now,
                )
                failures.append({
                    "path": str(path),
                    "kind": row["kind"],
                    "error_type": type(exc).__name__,
                })

        if deleted:
            pending = _load(self.pending_path, [])
            if not isinstance(pending, list):
                pending = []
            for row in deleted:
                pending.append({
                    **row,
                    "deleted_at": _iso(now),
                    "settle_after": _iso(
                        datetime.fromtimestamp(now.timestamp() + 600, tz=timezone.utc)
                    ),
                    "status": "PENDING",
                })
            _atomic(self.pending_path, pending[-500:])

        after = shutil.disk_usage(self.state_root)
        return {
            "status": "EXECUTED",
            "mode": "DEEP" if deep else "INITIAL",
            "deleted_count": len(deleted),
            "deleted_tmp_files": int(tmp_result["deleted_files"]),
            "failure_count": len(failures),
            "reclaimed_bytes_estimated": reclaimed,
            "free_bytes_before": int(before.free),
            "free_bytes_after": int(after.free),
            "free_bytes_delta": int(after.free - before.free),
            "deleted": deleted[:80],
            "failures": failures[:40],
            "unresolved_candidates": sum(
                1 for row in candidates if row.get("recommended_action") != "RECLAIM"
            ),
        }

    def _reclaim_state_tmp(self, *, now: datetime) -> dict[str, int]:
        cutoff = now.timestamp() - 24 * 3600
        reclaimed = deleted = 0
        for path in self.state_root.rglob("*.tmp"):
            if "secrets" in path.parts or not path.is_file():
                continue
            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff or reclaimed + stat.st_size > 512 * 1024**2:
                    continue
                size = stat.st_size
                path.unlink()
                reclaimed += size
                deleted += 1
            except OSError:
                continue
        return {"deleted_files": deleted, "reclaimed_bytes": reclaimed}

    def _candidate_paths(self, *, deep: bool) -> list[Path]:
        rows: list[Path] = []
        try:
            top = list(self.storage_root.iterdir())
        except OSError:
            return rows
        rows.extend(top)

        # Deep mode looks inside generational containers without assuming project names.
        if deep:
            for parent in top:
                if not parent.is_dir() or parent.is_symlink():
                    continue
                try:
                    children = list(parent.iterdir())[:400]
                except OSError:
                    continue
                if self._looks_like_generation_container(children):
                    rows.extend(children)
                for child in children[:80]:
                    if not child.is_dir() or child.is_symlink():
                        continue
                    try:
                        grandchildren = list(child.iterdir())[:400]
                    except OSError:
                        continue
                    if self._looks_like_generation_container(grandchildren):
                        rows.extend(grandchildren)
        dedup: dict[str, Path] = {}
        for path in rows:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            dedup[str(resolved)] = resolved
        return list(dedup.values())

    def _describe(
        self, *, path: Path, now: datetime, active_refs: set[Path]
    ) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        kind = self._kind(path)
        worktree_detached = (
            self._git_worktree_detached(path) if kind == "CODE_WORKTREE" else False
        )
        protected_reason = self._protected_reason(
            path=path,
            active_refs=active_refs,
            kind=kind,
            worktree_detached=worktree_detached,
        )
        size_known = path.is_file() or path.is_symlink()
        return {
            "path": str(path),
            "name": path.name,
            "family": self._family(path.name, kind=kind),
            "kind": kind,
            "worktree_detached": worktree_detached,
            "worktree_reclaimable": False,
            "size_bytes": int(stat.st_size) if size_known else 0,
            "size_known": size_known,
            "mtime": float(stat.st_mtime),
            "age_hours": max(0.0, (now.timestamp() - stat.st_mtime) / 3600.0),
            "protected": bool(protected_reason),
            "protected_reason": protected_reason,
        }

    def _protected_reason(
        self, *, path: Path, active_refs: set[Path],
        kind: str, worktree_detached: bool = False,
    ) -> str | None:
        try:
            resolved = path.resolve()
        except OSError:
            return "UNRESOLVED"
        if resolved == self.state_root or self.state_root in resolved.parents:
            return "PERSISTENT_STATE"
        if resolved.name in {".ssh", ".livingruntime"}:
            return "CREDENTIAL_OR_CONTROL_PLANE"
        running_prefix = Path(sys.prefix).resolve()
        if resolved == running_prefix or resolved in running_prefix.parents:
            return "RUNNING_INTERPRETER"
        for ref in active_refs:
            if resolved == ref or resolved in ref.parents:
                return "ACTIVE_PROCESS_REFERENCE"
        if kind == "CODE_WORKTREE" and not worktree_detached:
            return "BRANCHED_OR_UNVERIFIED_WORKTREE"
        return None

    def _kind(self, path: Path) -> str:
        name = path.name.lower()
        if name in {".ssh", ".livingruntime"}:
            return "SECRET"
        if path == self.state_root or self.state_root in path.parents:
            return "STATE"
        if name in {".cache", ".npm", ".pytest_cache", "__pycache__"} or "cache" in name:
            return "CACHE"
        if path.is_dir() and (path / "pyvenv.cfg").exists():
            return "ENVIRONMENT"
        if "venv" in name or "virtualenv" in name:
            return "ENVIRONMENT"
        if path.suffix.lower() in {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".whl"}:
            return "ARCHIVE"
        if path.is_dir() and ((path / ".git").exists() or (path / ".git").is_file()):
            return "CODE_WORKTREE"
        tokens = set(re.split(r"[-_.]+", name))
        if tokens & {
            "prod", "deploy", "deployment", "artifact", "artifacts", "backup",
            "release", "releases", "build", "smoke", "baseline", "preview", "test",
            "wheel", "wheels", "scratch", "work",
        }:
            return "GENERATED_ARTIFACT"
        if self._looks_generated(name):
            return "GENERATED_ARTIFACT"
        return "UNKNOWN"

    @staticmethod
    def _looks_generated(name: str) -> bool:
        return bool(
            re.fullmatch(r"[0-9a-f]{7,64}", name.lower())
            or re.search(r"20\d{6,12}", name)
            or re.search(r"[-_][0-9a-f]{7,64}(?:[-_.]|$)", name.lower())
        )

    @classmethod
    def _family(cls, name: str, *, kind: str) -> str:
        value = name.lower()
        value = re.sub(r"[0-9a-f]{7,64}", "{id}", value)
        value = re.sub(r"20\d{6,12}", "{date}", value)
        value = re.sub(r"\d{5,}", "{n}", value)
        value = re.sub(r"[-_.]+", "-", value).strip("-")
        return f"{kind}:{value or '{generated}'}"

    @classmethod
    def _looks_like_generation_container(cls, children: list[Path]) -> bool:
        if len(children) < 6:
            return False
        generated = sum(
            1 for child in children
            if cls._looks_generated(child.name)
            or any(
                token in child.name.lower()
                for token in ("prod", "venv", "release", "deploy", "artifact", "backup")
            )
        )
        return generated / max(1, len(children)) >= 0.5

    @staticmethod
    def _signature(row: dict[str, Any]) -> str:
        material = f"{row['kind']}|{row['family']}"
        return "storage-profile:" + sha256(material.encode()).hexdigest()[:20]

    @staticmethod
    def _confidence(*, row: dict[str, Any], profile: dict[str, Any]) -> float:
        kind = str(row["kind"])
        base = {
            "CACHE": 0.99,
            "ENVIRONMENT": 0.88,
            "ARCHIVE": 0.70,
            "GENERATED_ARTIFACT": 0.56,
            "CODE_WORKTREE": 0.72 if row.get("worktree_reclaimable") else 0.05,
            "STATE": 0.0,
            "SECRET": 0.0,
            "UNKNOWN": 0.10,
        }.get(kind, 0.10)
        if int(row.get("family_count") or 0) >= 2 and not row.get("newest_in_family"):
            base += 0.10 if kind == "ENVIRONMENT" else 0.16
        age_hours = float(row.get("age_hours") or 0.0)
        if age_hours >= 168:
            base += 0.05
        if age_hours >= 720:
            base += 0.04
        confirmed = int(profile.get("confirmed_survival") or 0)
        failures = int(profile.get("failures") or 0)
        base += min(0.12, confirmed * 0.03)
        base -= min(0.35, failures * 0.12)
        return max(0.0, min(0.999, base))

    def _delete_candidate(self, *, path: Path, kind: str) -> None:
        if kind in {"STATE", "SECRET", "UNKNOWN"}:
            raise OSError("candidate kind is not autonomously reclaimable")
        if kind == "CODE_WORKTREE":
            if not self._git_worktree_detached(path) or not self._git_worktree_clean(path):
                raise OSError("git worktree is no longer safe to reclaim")
            try:
                result = subprocess.run(
                    ["git", "-C", str(path), "worktree", "remove", str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("git worktree removal failed") from exc
            if result.returncode != 0:
                raise OSError("git worktree removal failed: " + result.stderr.strip()[:200])
            return
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @staticmethod
    def _git_worktree_detached(path: Path) -> bool:
        git_file = path / ".git"
        if not git_file.is_file():
            return False
        try:
            raw = git_file.read_text(encoding="utf-8").strip()
            if not raw.startswith("gitdir:"):
                return False
            gitdir = Path(raw.split(":", 1)[1].strip()).resolve()
            head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return False
        return bool(head) and not head.startswith("ref:")

    @staticmethod
    def _git_worktree_clean(path: Path) -> bool:
        try:
            status = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return status.returncode == 0 and not status.stdout.strip()

    def _settle_learning(self, *, now: datetime) -> None:
        pending = _load(self.pending_path, [])
        if not isinstance(pending, list) or not pending:
            return
        changed = False
        for row in pending:
            if row.get("status") != "PENDING":
                continue
            settle = str(row.get("settle_after") or "")
            try:
                due = datetime.fromisoformat(settle)
            except ValueError:
                due = now
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due > now:
                continue
            path = Path(str(row.get("path") or ""))
            outcome = "REBUILT_AFTER_RECLAIM" if path.exists() else "SURVIVED_RECLAIM"
            self._record_learning(
                signature=str(row.get("signature") or ""),
                outcome=outcome,
                now=now,
            )
            row["status"] = "SETTLED"
            row["outcome"] = outcome
            row["settled_at"] = _iso(now)
            changed = True
        if changed:
            _atomic(self.pending_path, pending[-500:])

    def _record_learning(self, *, signature: str, outcome: str, now: datetime) -> None:
        if not signature:
            return
        knowledge = _load(self.knowledge_path, {"schema_version": "ferro-storage-knowledge.v1", "profiles": {}})
        profiles = knowledge.setdefault("profiles", {})
        profile = dict(profiles.get(signature) or {})
        profile["attempts"] = int(profile.get("attempts") or 0) + 1
        if outcome in {"SURVIVED_RECLAIM", "REBUILT_AFTER_RECLAIM"}:
            profile["confirmed_survival"] = int(profile.get("confirmed_survival") or 0) + 1
        else:
            profile["failures"] = int(profile.get("failures") or 0) + 1
        profile["last_outcome"] = outcome
        profile["updated_at"] = _iso(now)
        profiles[signature] = profile
        knowledge["schema_version"] = "ferro-storage-knowledge.v1"
        knowledge["updated_at"] = _iso(now)
        _atomic(self.knowledge_path, knowledge)

    def _active_references(self) -> set[Path]:
        refs: set[Path] = {Path(sys.prefix).resolve()}
        proc = Path("/proc")
        if not proc.is_dir():
            return refs
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            for leaf in ("cwd", "exe"):
                try:
                    refs.add(Path(os.readlink(entry / leaf)).resolve())
                except (OSError, RuntimeError):
                    pass
            fd_root = entry / "fd"
            try:
                fds = list(fd_root.iterdir())[:256]
            except OSError:
                continue
            for fd in fds:
                try:
                    raw = os.readlink(fd)
                    if raw.startswith("/"):
                        refs.add(Path(raw.split(" (deleted)", 1)[0]).resolve())
                except (OSError, RuntimeError):
                    pass
        return refs

    @staticmethod
    def _path_size(path: Path) -> int:
        try:
            result = subprocess.run(
                ["du", "-sb", "--", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode == 0 and result.stdout:
                return int(result.stdout.split()[0])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        try:
            if path.is_file() or path.is_symlink():
                return int(path.stat().st_size)
        except OSError:
            return 0
        total = 0
        try:
            for root, _, files in os.walk(path):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        continue
        except OSError:
            return total
        return total


__all__ = [
    "StorageLifecycle",
    "INITIAL_RECLAIM_RATIO",
    "DEEP_RECLAIM_RATIO",
]
