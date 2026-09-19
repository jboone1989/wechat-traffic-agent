from __future__ import annotations

import json
import re
from typing import Any, Mapping


MAX_TOOL_REQUESTS_PER_ROUND = 3
MAX_TOOL_PAYLOAD_BYTES = 8 * 1024
MAX_TOOL_RESULT_CHARS = 16_000
_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|password|secret|credential|authorization)", re.I)
_SENSITIVE_VALUE = re.compile(r"(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}\b)", re.I)


def _manifest(runtime: Any) -> dict[str, dict[str, Any]]:
    try:
        rows = runtime.capability_manifest()
    except Exception:
        return {}
    return {
        str(row.get("capability_id") or ""): dict(row)
        for row in rows
        if isinstance(row, Mapping) and str(row.get("capability_id") or "")
    }


def readonly_capability_rows(application: Any) -> list[dict[str, Any]]:
    runtime = getattr(application, "runtime", None)
    if runtime is None:
        return []
    rows = []
    for capability_id, row in _manifest(runtime).items():
        if str(row.get("side_effect_level") or "") != "READ_ONLY":
            continue
        rows.append({
            "capability_id": capability_id,
            "description": str(row.get("description") or "")[:300],
            "input_schema": dict(row.get("input_schema") or {}),
        })
    rows.sort(key=lambda row: row["capability_id"])
    return rows


def _payload_has_secret(value: Any, *, key: str = "") -> bool:
    if key and _SENSITIVE_KEY.search(key):
        return True
    if isinstance(value, Mapping):
        return any(
            _payload_has_secret(item, key=str(name))
            for name, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_payload_has_secret(item) for item in value)
    return isinstance(value, str) and bool(_SENSITIVE_VALUE.search(value))


def _request_error(capability_id: str, code: str, reason: str) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "ok": False,
        "status": code,
        "reason": reason,
    }


def _normalized_payload(capability_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if capability_id == "web.search":
        try:
            limit = int(normalized.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        normalized["limit"] = max(1, min(limit, 8))
        if "page" in normalized:
            try:
                normalized["page"] = max(1, min(int(normalized["page"]), 10))
            except (TypeError, ValueError):
                normalized["page"] = 1
        if "safesearch" in normalized:
            try:
                normalized["safesearch"] = max(0, min(int(normalized["safesearch"]), 2))
            except (TypeError, ValueError):
                normalized["safesearch"] = 1
    return normalized


def execute_readonly_tool_requests(
    application: Any, requests: list[Any]
) -> list[dict[str, Any]]:
    """Execute only manifest-declared READ_ONLY capabilities with bounded payloads."""
    runtime = getattr(application, "runtime", None)
    if runtime is None:
        return [_request_error("", "RUNTIME_UNAVAILABLE", "application runtime is unavailable")]

    manifest = _manifest(runtime)
    results: list[dict[str, Any]] = []
    for raw in list(requests or [])[:MAX_TOOL_REQUESTS_PER_ROUND]:
        if not isinstance(raw, Mapping):
            results.append(_request_error("", "INVALID_REQUEST", "tool request must be an object"))
            continue
        capability_id = str(raw.get("capability_id") or "").strip()
        payload = raw.get("payload")
        payload = dict(payload) if isinstance(payload, Mapping) else {}
        payload = _normalized_payload(capability_id, payload)
        row = manifest.get(capability_id)
        if row is None:
            results.append(_request_error(capability_id, "UNKNOWN_CAPABILITY", "capability is not registered"))
            continue
        if str(row.get("side_effect_level") or "") != "READ_ONLY":
            results.append(_request_error(
                capability_id, "FORBIDDEN_SIDE_EFFECT",
                "owner chat may invoke only READ_ONLY runtime capabilities",
            ))
            continue
        if _payload_has_secret(payload):
            results.append(_request_error(
                capability_id, "SENSITIVE_PAYLOAD_REJECTED",
                "owner chat will not send credentials or secret-shaped values to capabilities",
            ))
            continue
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_TOOL_PAYLOAD_BYTES:
            results.append(_request_error(
                capability_id, "PAYLOAD_TOO_LARGE",
                f"payload exceeds {MAX_TOOL_PAYLOAD_BYTES} bytes",
            ))
            continue
        try:
            receipt = runtime.invoke(capability_id, payload)
            result_payload = dict(getattr(receipt, "payload", {}) or {})
            result_text = json.dumps(
                result_payload, ensure_ascii=False, default=str, separators=(",", ":")
            )
            truncated = len(result_text) > MAX_TOOL_RESULT_CHARS
            results.append({
                "capability_id": capability_id,
                "ok": True,
                "status": "INVOKED",
                "receipt_id": str(getattr(receipt, "receipt_id", "") or ""),
                "result": result_text[:MAX_TOOL_RESULT_CHARS],
                "result_truncated": truncated,
            })
        except Exception as exc:
            results.append(_request_error(
                capability_id, "INVOCATION_FAILED",
                f"{type(exc).__name__}: {str(exc)[:500]}",
            ))
    try:
        runtime.persist()
    except Exception:
        pass
    return results


__all__ = [
    "MAX_TOOL_REQUESTS_PER_ROUND",
    "execute_readonly_tool_requests",
    "readonly_capability_rows",
]
