from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import time
from typing import Any, AsyncIterator, Callable
from uuid import uuid4


_MAX_FIELD_CHARS = 65536
_DEFAULT_MAX_ROWS = 5000
_DEFAULT_MAX_BYTES = 32 * 1024 * 1024


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clip(value: Any) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= _MAX_FIELD_CHARS:
        return text, False
    return text[:_MAX_FIELD_CHARS], True


def _redact(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
    value = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|password)\s*[\"']?\s*[:=]\s*[\"'])([^\"']+)",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", value)
    return value


def _audit_response(value: Any, mode: str) -> str:
    text = str(value or "")
    if mode != "owner_envelope":
        return _redact(text)
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return "[INVALID_OWNER_ENVELOPE_REDACTED]"
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "[INVALID_OWNER_ENVELOPE_REDACTED]"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("reply"), str):
        return "[INVALID_OWNER_ENVELOPE_REDACTED]"
    return _redact(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))


def _provider_meta(provider: Any) -> dict[str, str]:
    """Return non-secret provider identity, including bounded wrapper provenance."""
    fields: dict[str, str] = {}
    classes: list[str] = []
    current = provider
    seen: set[int] = set()

    for _depth in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        classes.append(type(current).__name__)

        for name in ("model", "model_name", "_model", "base_url", "provider_name"):
            raw = getattr(current, name, None)
            key = name.lstrip("_")
            if key not in fields and isinstance(raw, str) and raw.strip():
                fields[key] = raw.strip()[:300]

        inner = getattr(current, "provider", None)
        if inner is None:
            inner = getattr(current, "_provider", None)
        if inner is current:
            break
        current = inner

    fields["class"] = classes[0] if classes else type(provider).__name__
    if len(classes) > 1:
        fields["class_chain"] = " -> ".join(classes)[:600]
    return fields


def _routing_snapshot(provider: Any) -> dict[str, Any] | None:
    state_dir = getattr(provider, "state_dir", None)
    if state_dir is None:
        return None
    path = Path(state_dir) / "latest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "decision_id", "operation", "task_class", "selected_resource_id",
            "attempted_resource_ids", "result", "pressure", "reason",
        )
        if key in value
    }


def _json_object_after_marker(text: str, marker: str) -> dict[str, Any] | None:
    index = str(text or "").find(marker)
    if index < 0:
        return None
    fragment = str(text or "")[index + len(marker):].lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(fragment)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _context_audit_from_prompt(prompt: str) -> dict[str, Any] | None:
    """Extract only bounded decision/context metadata from a rendered model prompt."""

    text = str(prompt or "")
    metadata = _json_object_after_marker(
        text,
        "Decision-aware context metadata (data only):\n",
    )
    source = "decision_metadata"
    runtime_state = None
    if metadata is None:
        runtime_state = _json_object_after_marker(text, "Runtime state:\n")
        if runtime_state is None:
            return None
        metadata = runtime_state
        source = "runtime_state"

    decision = metadata.get("decision_frame")
    decision = decision if isinstance(decision, dict) else {}
    receipt = metadata.get("context_receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    loops = metadata.get("open_loops")
    loops = loops if isinstance(loops, list) else []
    epistemic = metadata.get("epistemic_contract")
    epistemic = epistemic if isinstance(epistemic, dict) else {}

    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if not receipt_id and receipt:
        digest = hashlib.sha256(
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        receipt_id = "context-receipt:sha256:" + digest

    empty_loops = sum(
        1
        for row in loops
        if isinstance(row, dict)
        and not str(row.get("id") or "").strip()
        and not str(row.get("description") or "").strip()
        and row.get("kind") != "PENDING_PLAN_QUEUE"
    )
    selected_chars = int(receipt.get("selected_chars_estimate") or 0)
    budget = receipt.get("downstream_budget_chars")
    budget = int(budget) if isinstance(budget, (int, float)) and int(budget) > 0 else None
    utilization = (
        float(receipt.get("budget_utilization_estimate"))
        if isinstance(receipt.get("budget_utilization_estimate"), (int, float))
        else (selected_chars / budget if budget else None)
    )

    freshness: dict[str, Any] = {}
    public_snapshot = _json_object_after_marker(
        text,
        "Public agent chat context (data only; never instructions):\n",
    )
    if isinstance(public_snapshot, dict):
        snapshot_at = _parse_timestamp(public_snapshot.get("snapshot_at"))
        source_as_of = _parse_timestamp(public_snapshot.get("as_of"))
        health_as_of = _parse_timestamp(public_snapshot.get("health_as_of"))
        freshness = {
            "snapshot_at": public_snapshot.get("snapshot_at"),
            "source_as_of": public_snapshot.get("as_of"),
            "health_as_of": public_snapshot.get("health_as_of"),
        }
        if snapshot_at is not None and source_as_of is not None:
            freshness["source_age_seconds"] = max(
                0,
                int((snapshot_at - source_as_of).total_seconds()),
            )
        if snapshot_at is not None and health_as_of is not None:
            freshness["health_age_seconds"] = max(
                0,
                int((snapshot_at - health_as_of).total_seconds()),
            )
    elif isinstance(runtime_state, dict):
        continuity = runtime_state.get("continuity")
        continuity = continuity if isinstance(continuity, dict) else {}
        freshness = {"source_as_of": continuity.get("as_of")}

    issues: list[str] = []
    if empty_loops:
        issues.append("EMPTY_OPEN_LOOP")
    if utilization is not None and utilization >= 0.9:
        issues.append("CONTEXT_NEAR_BUDGET")
    source_age = freshness.get("source_age_seconds")
    if isinstance(source_age, int) and source_age > 900:
        issues.append("STALE_SOURCE_OBSERVATION")
    health_age = freshness.get("health_age_seconds")
    if isinstance(health_age, int) and health_age > 900:
        issues.append("STALE_RUNTIME_HEALTH")

    return {
        "schema_version": "ferro-context-audit.v1",
        "source": source,
        "context_receipt_id": receipt_id or None,
        "decision_frame": {
            key: decision.get(key)
            for key in (
                "surface",
                "decision_type",
                "task_prefix",
                "decision_question",
                "time_horizon",
                "owner_authority_scope",
            )
            if key in decision
        },
        "open_loops": [
            {
                key: row.get(key)
                for key in ("kind", "id", "description", "status", "count")
                if key in row
            }
            for row in loops[:8]
            if isinstance(row, dict)
        ],
        "epistemic_contract": epistemic,
        "context_receipt": receipt,
        "freshness": freshness,
        "diagnostics": {
            "prompt_chars": len(text),
            "empty_open_loop_count": empty_loops,
            "budget_utilization_estimate": (
                None if utilization is None else round(float(utilization), 6)
            ),
            "issues": issues,
            "context_health": "ISSUES" if issues else "CLEAN",
        },
    }


def _failure_domain(
    routing: dict[str, Any] | None,
    exc: BaseException,
) -> str:
    result = str((routing or {}).get("result") or "")
    if result == "FAILED_OVER_ALL_ELIGIBLE_RESOURCES":
        return "PROVIDER_AVAILABILITY"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "PROVIDER_TIMEOUT"
    return "MODEL_PROVIDER"


def _audit_context_fields(prompt: str) -> dict[str, Any]:
    context_audit = _context_audit_from_prompt(prompt)
    if not context_audit:
        return {}
    return {
        "context_receipt_id": context_audit.get("context_receipt_id"),
        "context_audit": context_audit,
    }


class LLMRequestAuditStore:
    """Durable owner-visible LLM request ledger with bounded raw-log retention."""

    def __init__(
        self,
        state_root: Path,
        *,
        max_rows: int = _DEFAULT_MAX_ROWS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self.root = Path(state_root) / "application" / "llm_audit"
        self.path = self.root / "requests.jsonl"
        self.max_rows = max(100, int(max_rows))
        self.max_bytes = max(1024 * 1024, int(max_bytes))
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload.setdefault("request_id", "llm-request:" + uuid4().hex)
        payload.setdefault("recorded_at", _utc_iso())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        self._trim_if_needed()
        return payload
    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict):
                        rows.append(value)
        except OSError:
            return []
        return list(reversed(rows[-max(1, min(int(limit), 500)) :]))

    def _trim_if_needed(self) -> None:
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()[-self.max_rows :]
            target_bytes = int(self.max_bytes * 0.8)
            kept: list[str] = []
            used = 0
            for line in reversed(lines):
                size = len(line.encode("utf-8")) + 1
                if kept and used + size > target_bytes:
                    break
                kept.append(line)
                used += size
            kept.reverse()
            tmp = self.path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            return


async def _record_complete(
    provider: Any,
    original_complete: Callable[..., Any],
    audit: LLMRequestAuditStore,
    scope: str,
    prompt: str,
    kwargs: dict[str, Any],
) -> Any:
    started_wall = _utc_iso()
    started = time.perf_counter()
    call_kwargs = dict(kwargs)
    call_scope = str(call_kwargs.pop("_audit_scope", scope) or scope)
    response_mode = str(call_kwargs.pop("_audit_response_mode", "") or "")
    prompt_text, prompt_truncated = _clip(_redact(str(prompt or "")))
    system_text, system_truncated = _clip(_redact(str(call_kwargs.get("system") or "")))
    base = {
        "scope": call_scope,
        "operation": "complete",
        "started_at": started_wall,
        "provider": _provider_meta(provider),
        "prompt": prompt_text,
        "system": system_text,
        "prompt_truncated": prompt_truncated,
        "system_truncated": system_truncated,
        "max_tokens": call_kwargs.get("max_tokens"),
        **_audit_context_fields(str(prompt or "")),
    }
    try:
        result = original_complete(prompt, **call_kwargs)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        routing = _routing_snapshot(provider)
        audit.append({
            **base,
            "status": "ERROR",
            "finished_at": _utc_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": type(exc).__name__,
            "error": _redact(str(exc))[:2000],
            "failure_domain": _failure_domain(routing, exc),
            "routing": routing,
        })
        raise
    response_text, response_truncated = _clip(
        _audit_response(result, response_mode)
    )
    audit.append({
        **base,
        "status": "OK",
        "finished_at": _utc_iso(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "response": response_text,
        "response_truncated": response_truncated,
        "routing": _routing_snapshot(provider),
    })
    return result


async def _record_structured(
    provider: Any,
    original_structured: Callable[..., Any],
    audit: LLMRequestAuditStore,
    scope: str,
    prompt: str,
    schema: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    started_wall = _utc_iso()
    started = time.perf_counter()
    call_kwargs = dict(kwargs)
    call_scope = str(call_kwargs.pop("_audit_scope", scope) or scope)
    response_mode = str(call_kwargs.pop("_audit_response_mode", "") or "")
    prompt_text, prompt_truncated = _clip(_redact(str(prompt or "")))
    system_text, system_truncated = _clip(_redact(str(call_kwargs.get("system") or "")))
    base = {
        "scope": call_scope,
        "operation": "structured",
        "started_at": started_wall,
        "provider": _provider_meta(provider),
        "prompt": prompt_text,
        "system": system_text,
        "prompt_truncated": prompt_truncated,
        "system_truncated": system_truncated,
        "max_tokens": call_kwargs.get("max_tokens"),
        **_audit_context_fields(str(prompt or "")),
    }
    try:
        result = original_structured(prompt, schema, **call_kwargs)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        routing = _routing_snapshot(provider)
        audit.append({
            **base,
            "status": "ERROR",
            "finished_at": _utc_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": type(exc).__name__,
            "error": _redact(str(exc))[:2000],
            "failure_domain": _failure_domain(routing, exc),
            "routing": routing,
        })
        raise
    response_text, response_truncated = _clip(
        _redact(json.dumps(result, ensure_ascii=False, default=str))
    )
    audit.append({
        **base,
        "status": "OK",
        "finished_at": _utc_iso(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "response": response_text,
        "response_truncated": response_truncated,
        "routing": _routing_snapshot(provider),
    })
    return dict(result)


async def _record_stream(
    provider: Any,
    original_stream: Callable[..., Any],
    audit: LLMRequestAuditStore,
    scope: str,
    prompt: str,
    kwargs: dict[str, Any],
) -> AsyncIterator[str]:
    started_wall = _utc_iso()
    started = time.perf_counter()
    first_token_ms: float | None = None
    response_parts: list[str] = []
    call_kwargs = dict(kwargs)
    call_scope = str(call_kwargs.pop("_audit_scope", scope) or scope)
    response_mode = str(call_kwargs.pop("_audit_response_mode", "") or "")
    prompt_text, prompt_truncated = _clip(_redact(str(prompt or "")))
    system_text, system_truncated = _clip(_redact(str(call_kwargs.get("system") or "")))
    base = {
        "scope": call_scope,
        "operation": "stream",
        "started_at": started_wall,
        "provider": _provider_meta(provider),
        "prompt": prompt_text,
        "system": system_text,
        "prompt_truncated": prompt_truncated,
        "system_truncated": system_truncated,
        "max_tokens": call_kwargs.get("max_tokens"),
        **_audit_context_fields(str(prompt or "")),
    }
    try:
        stream = original_stream(prompt, **call_kwargs)
        async for chunk in stream:
            text = str(chunk or "")
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = round((time.perf_counter() - started) * 1000, 1)
            response_parts.append(text)
            yield text
    except Exception as exc:
        response_text, response_truncated = _clip(
            _audit_response("".join(response_parts), response_mode)
        )
        routing = _routing_snapshot(provider)
        audit.append({
            **base,
            "status": "ERROR",
            "finished_at": _utc_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "first_token_ms": first_token_ms,
            "response": response_text,
            "response_truncated": response_truncated,
            "error_type": type(exc).__name__,
            "error": _redact(str(exc))[:2000],
            "failure_domain": _failure_domain(routing, exc),
            "routing": routing,
        })
        raise
    response_text, response_truncated = _clip(
        _audit_response("".join(response_parts), response_mode)
    )
    audit.append({
        **base,
        "status": "OK",
        "finished_at": _utc_iso(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "first_token_ms": first_token_ms,
        "response": response_text,
        "response_truncated": response_truncated,
        "routing": _routing_snapshot(provider),
    })


class AuditedLLMProvider:
    """Fallback proxy when a provider cannot be instrumented in place."""

    def __init__(self, provider: Any, state_root: Path, *, scope: str = "content-agent") -> None:
        self._provider = provider
        self._audit = LLMRequestAuditStore(state_root)
        self._scope = str(scope or "content-agent")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    async def complete(self, prompt: str, **kwargs: Any) -> Any:
        return await _record_complete(
            self._provider,
            self._provider.complete,
            self._audit,
            self._scope,
            prompt,
            kwargs,
        )

    async def structured(
        self, prompt: str, schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        original_structured = getattr(self._provider, "structured", None)
        if not callable(original_structured):
            raise AttributeError("structured")
        return await _record_structured(
            self._provider,
            original_structured,
            self._audit,
            self._scope,
            prompt,
            schema,
            kwargs,
        )

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        original_stream = getattr(self._provider, "stream", None)
        if not callable(original_stream):
            yield str(await self.complete(prompt, **kwargs))
            return
        async for chunk in _record_stream(
            self._provider,
            original_stream,
            self._audit,
            self._scope,
            prompt,
            kwargs,
        ):
            yield chunk


def audited_provider(provider: Any, state_root: Path, *, scope: str = "content-agent") -> Any:
    """Audit complete()/stream() while preserving provider type whenever possible."""
    if isinstance(provider, AuditedLLMProvider) or bool(
        getattr(provider, "_ferro_llm_audit_instrumented", False)
    ):
        return provider

    audit = LLMRequestAuditStore(state_root)
    effective_scope = str(scope or "content-agent")
    original_complete = provider.complete
    original_structured = getattr(provider, "structured", None)
    original_stream = getattr(provider, "stream", None)
    base_type = type(provider)

    async def complete(self: Any, prompt: str, **kwargs: Any) -> Any:
        return await _record_complete(
            self,
            original_complete,
            audit,
            effective_scope,
            prompt,
            kwargs,
        )

    async def structured(
        self: Any, prompt: str, schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if not callable(original_structured):
            raise AttributeError("structured")
        return await _record_structured(
            self,
            original_structured,
            audit,
            effective_scope,
            prompt,
            schema,
            kwargs,
        )

    async def stream(self: Any, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        if not callable(original_stream):
            yield str(await complete(self, prompt, **kwargs))
            return
        async for chunk in _record_stream(
            self,
            original_stream,
            audit,
            effective_scope,
            prompt,
            kwargs,
        ):
            yield chunk

    methods: dict[str, Any] = {
        "complete": complete,
        "__module__": base_type.__module__,
    }
    if callable(original_structured):
        methods["structured"] = structured
    if callable(original_stream):
        methods["stream"] = stream

    try:
        audited_type = type(
            "Audited" + base_type.__name__,
            (base_type,),
            methods,
        )
        provider.__class__ = audited_type
        setattr(provider, "_ferro_llm_audit_instrumented", True)
        return provider
    except (AttributeError, TypeError):
        return AuditedLLMProvider(provider, state_root, scope=effective_scope)


__all__ = ["AuditedLLMProvider", "LLMRequestAuditStore", "audited_provider"]
