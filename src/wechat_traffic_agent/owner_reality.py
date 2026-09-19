from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from .owner_tools import readonly_capability_rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _compact_capability(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capability_id": str(row.get("capability_id") or ""),
        "description": str(row.get("description") or "")[:300],
        "side_effect_level": str(row.get("side_effect_level") or "READ_ONLY"),
        "required_permissions": list(row.get("required_permissions") or []),
        "input_schema": dict(row.get("input_schema") or {}),
    }


def _compact_account(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": str(row.get("provider_id") or ""),
        "label": str(row.get("label") or ""),
        "identity": str(row.get("identity") or ""),
        "status": str(row.get("status") or ""),
        "capabilities": list(row.get("capabilities") or []),
        "bindings": [
            {
                "binding_id": str(item.get("binding_id") or ""),
                "label": str(item.get("label") or ""),
                "status": str(item.get("status") or ""),
                "purpose": str(item.get("purpose") or ""),
            }
            for item in (row.get("bindings") or [])
            if isinstance(item, Mapping)
        ],
    }
def build_owner_reality(
    *, state_root: Path, application: Any, account_manager: Any | None = None
) -> dict[str, Any]:
    """Build a secret-free, read-only snapshot of what Ferro can actually observe/use."""
    root = Path(state_root)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    runtime = getattr(application, "runtime", None)
    manifest: list[dict[str, Any]] = []
    try:
        manifest = [
            _compact_capability(row)
            for row in (runtime.capability_manifest() if runtime is not None else ())
            if isinstance(row, Mapping)
        ]
    except Exception:
        manifest = []

    accounts: dict[str, Any] = {"accounts": [], "instances": []}
    if account_manager is not None:
        try:
            raw = account_manager.status()
            accounts = {
                "accounts": [
                    _compact_account(row)
                    for row in (raw.get("accounts") or [])
                    if isinstance(row, Mapping)
                ],
                "instances": [
                    {
                        "account_id": str(row.get("account_id") or ""),
                        "provider_id": str(row.get("provider_id") or ""),
                        "identity": str(row.get("identity") or ""),
                        "display_label": str(row.get("display_label") or ""),
                        "status": str(row.get("status") or ""),
                        "oauth_status": str(row.get("oauth_status") or row.get("status") or ""),
                        "automation": dict(row.get("automation") or {}),
                    }
                    for row in (raw.get("instances") or [])
                    if isinstance(row, Mapping)
                ],
            }
        except Exception:
            pass
    disk = shutil.disk_usage(root)
    local_resources = {
        "filesystem": {
            "path": str(root),
            "total_bytes": int(disk.total),
            "used_bytes": int(disk.used),
            "free_bytes": int(disk.free),
            "used_ratio": round((disk.used / disk.total) if disk.total else 1.0, 6),
        }
    }

    llm_pool = _read_json(root / "application" / "llm_resource_pool" / "resources.json")
    llm_resources = []
    for row in llm_pool.get("resources") or []:
        if not isinstance(row, Mapping):
            continue
        llm_resources.append({
            "resource_id": str(row.get("resource_id") or ""),
            "provider_id": str(row.get("provider_id") or ""),
            "model": str(row.get("model") or ""),
            "pricing_class": str(row.get("pricing_class") or ""),
            "capability_tier": str(row.get("capability_tier") or ""),
            "task_classes": list(row.get("task_classes") or []),
        })

    return {
        "observed_at": now,
        "owner_chat_direct_capabilities": [
            "private_conversation",
            "recent_history_read",
            "long_term_memory_retrieval",
            "long_term_memory_candidate_write",
            "goal_draft_create",
            "explicit_goal_activation_only",
            "read_only_reality_snapshot",
            "runtime_readonly_capability_invoke",
        ],
        "owner_chat_readonly_runtime_capabilities": readonly_capability_rows(application)[:80],
        "runtime_capabilities": manifest[:80],
        "accounts": accounts,
        "local_resources": local_resources,
        "llm_resources": llm_resources[:24],
        "execution_boundary": {
            "owner_chat_arbitrary_capability_invocation": False,
            "owner_chat_readonly_capability_invocation": True,
            "owner_chat_side_effect_execution": False,
            "goal_activation_requires_explicit_owner_action": True,
            "reality_snapshot_is_read_only": True,
        },
    }


__all__ = ["build_owner_reality"]
