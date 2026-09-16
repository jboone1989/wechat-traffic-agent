"""Persistent Content Agent composition with Runtime and VirtualBrain."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

APPLICATION_VERSION = "content-application:v1"
CAP_CONTENT_STATUS = "content.agent.status"
from .network_capabilities import (
    CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW, capability_specs,
)
SUBJECT_ID = "content-agent"


@dataclass(frozen=True)
class ContentApplicationTickResult:
    status: str
    as_of_time: str
    subject_id: str
    subject_tick: int
    runtime_cycle: int
    domain_runtime_cycle: int
    generation_ready: bool

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class ContentCapabilityPack:
    """Read-only operational surface for Content Agent discovery."""

    def __init__(self, status_observer: Callable[[], Mapping[str, Any]]) -> None:
        self._status_observer = status_observer

    def manifest(self) -> dict[str, Any]:
        work = []
        for row in capability_specs():
            work.append({
                **row,
                "output_schema": {"type": "object"},
                "side_effect_level": "READ_ONLY",
            })
        return {
            "provider": "content-agent",
            "version": "1.1",
            "description": "Read-only Content Agent status plus delegated research/content capabilities.",
            "capabilities": [{
                "capability_id": CAP_CONTENT_STATUS,
                "description": "Observe bounded Content Agent readiness and lifecycle state.",
                "input_schema": {"type": "object", "additionalProperties": False},
                "output_schema": {"type": "object"},
                "side_effect_level": "READ_ONLY",
            }, *work],
        }

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(payload or {})
        if capability_id == CAP_CONTENT_STATUS:
            if args:
                return {"ok": False, "status": "INVALID_REQUEST", "reason": "content status takes no arguments"}
            return {"ok": True, "observation": _normalize_status(self._status_observer())}
        if capability_id in {CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW}:
            return {
                "ok": False, "status": "NETWORK_ENDPOINT_REQUIRED", "capability_id": capability_id,
                "reason": "delegated content work executes through the signed Agent Network endpoint",
            }
        return {"ok": False, "status": "UNKNOWN_CAPABILITY", "capability_id": capability_id}


class ContentApplication:
    """One persistent Content subject with explicit external Runtime authority."""

    def __init__(
        self,
        *,
        root: Path,
        subject: Any,
        runtime: Any,
        domain_runtime: Any,
        observation_factory: Callable[..., Any],
        capability_manager: Any | None = None,
        capability_binding_report: Any | None = None,
    ) -> None:
        if subject.subject_id != runtime.subject_id:
            raise ValueError("VirtualBrain subject_id and ApplicationRuntime subject_id must match")
        self.root = Path(root)
        self.subject = subject
        self.runtime = runtime
        self.domain_runtime = domain_runtime
        self.observation_factory = observation_factory
        self.capability_manager = capability_manager
        self.capability_binding_report = capability_binding_report

    @property
    def health_path(self) -> Path:
        return self.root / "application" / "health.json"

    def status_observation(self) -> dict[str, Any]:
        manifest = getattr(self.domain_runtime, "agent_manifest", None)
        identity = getattr(self.domain_runtime, "identity", None)
        return _normalize_status({
            "product": "content-agent",
            "agent_type": getattr(manifest, "agent_type", "content-agent"),
            "domain_agent_id": getattr(identity, "agent_id", "unknown"),
            "domain_runtime_cycle": int(getattr(self.domain_runtime, "cycle", 0)),
            "generation_ready": bool(getattr(self.domain_runtime, "has_advisory_backend", False)),
            "publish_mode": ("website_automatic" if all(os.environ.get(k, "").lower() in {"1", "true", "yes", "on"}
                for k in ("CONTENT_CONTINUOUS_ENABLED", "CONTENT_AUTO_PUBLISH_ENABLED")) else "manual"),
        })

    def close(self) -> None:
        manager = self.capability_manager
        self.capability_manager = None
        if manager is not None:
            manager.close()

    def run_tick(self, *, as_of_time: str) -> ContentApplicationTickResult:
        _instant(as_of_time)
        self.runtime.next_cycle()
        receipt = self.runtime.invoke(CAP_CONTENT_STATUS, {})
        observation = _normalize_status(receipt.payload.get("observation") or {})
        events: list[dict[str, Any]] = []
        if not observation["generation_ready"]:
            events.append({
                "kind": "uncertainty",
                "data": {
                    "question": "Which advisory backend should provide bounded content reasoning?",
                    "intensity": 0.5,
                    "personal_relevance": 1.0,
                    "hypotheses": {"reasoning_backend_unconfigured": 0.9},
                },
            })
        self.subject.step(self.observation_factory(
            world_state={"content_agent": observation}, body_state={}, recent_events=tuple(events)
        ))
        self.subject.persist()
        self.runtime.persist()
        self.domain_runtime.persist()
        result = ContentApplicationTickResult(
            status="OK" if observation["generation_ready"] else "DEGRADED",
            as_of_time=as_of_time,
            subject_id=self.subject.subject_id,
            subject_tick=int(self.subject.tick),
            runtime_cycle=int(self.runtime.cycle),
            domain_runtime_cycle=int(observation["domain_runtime_cycle"]),
            generation_ready=bool(observation["generation_ready"]),
        )
        self._write_json(self.health_path, {"application_version": APPLICATION_VERSION, **result.to_payload()})
        return result

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _capability_pack_deployment() -> tuple[tuple[str, ...], dict[str, str], dict[str, dict[str, Any]]]:
    trusted = tuple(dict.fromkeys(
        item.strip()
        for item in os.environ.get("CONTENT_RUNTIME_TRUSTED_PACKS", "").split(",")
        if item.strip()
    ))
    preferred: dict[str, str] = {}
    configs: dict[str, dict[str, Any]] = {}

    search_pack = os.environ.get("CONTENT_WEB_SEARCH_PACK", "searxng-search").strip()
    if search_pack and search_pack in trusted:
        preferred["web.search"] = search_pack
        if search_pack == "searxng-search":
            configs[search_pack] = {
                "base_url": os.environ.get("CONTENT_SEARXNG_URL", "http://127.0.0.1:8080").strip(),
                "max_results": 8,
            }

    fetch_pack = os.environ.get("CONTENT_WEB_FETCH_PACK", "jina-reader").strip()
    if fetch_pack and fetch_pack in trusted:
        preferred["web.fetch"] = fetch_pack
        if fetch_pack == "jina-reader":
            config: dict[str, Any] = {
                "reader_base_url": os.environ.get("CONTENT_JINA_READER_URL", "https://r.jina.ai").strip(),
                "max_chars": 100_000,
            }
            api_key = os.environ.get("CONTENT_JINA_API_KEY") or os.environ.get("JINA_API_KEY")
            if api_key:
                config["api_key"] = api_key
            configs[fetch_pack] = config
    return trusted, preferred, configs


def build_content_application(*, root: Path, manifest_path: Path) -> ContentApplication:
    """Production composition root; framework imports remain behind this seam."""
    from agent_runtime import ApplicationRuntime
    from agent_runtime.agents import load_agent_manifest
    from agent_runtime.capabilities import (
        CapabilityPackManager, CapabilityPolicy, InstalledCapabilityPackCatalog,
    )
    from agent_runtime.runtime import AgentRuntime
    from virtualbrain import Subject, SubjectObservation

    from .runtime_backend import load_optional_advisory_backend

    root = Path(root)
    manifest = load_agent_manifest(manifest_path)
    backend = load_optional_advisory_backend()
    domain_state = root / "application" / "domain_runtime"
    if (domain_state / "agent_state.json").exists():
        if backend is None:
            domain_runtime = AgentRuntime.restore(domain_state)
        else:
            domain_runtime = AgentRuntime.restore(domain_state, agent_backend=backend)
        if domain_runtime.agent_manifest is None:
            domain_runtime.agent_manifest = manifest
    else:
        kwargs: dict[str, Any] = {"agent_manifest": manifest}
        if backend is not None:
            kwargs["agent_backend"] = backend
        domain_runtime = AgentRuntime.bootstrap(domain_state, **kwargs)

    key = _runtime_authority_key(root)
    subject = Subject.bootstrap(
        root / "application" / "subject", subject_id=SUBJECT_ID, trusted_runtime_key=key
    )
    from .x_mcp import XCapabilityProvider, x_enabled, x_writes_enabled
    x_active = x_enabled()
    x_write_active = x_active and x_writes_enabled()
    runtime = ApplicationRuntime.bootstrap(
        root / "application" / "runtime",
        subject_id=SUBJECT_ID,
        max_side_effect_level="EXTERNAL_SIDE_EFFECT" if x_write_active else "READ_ONLY",
        allowed_permissions=(frozenset({"social.publish", "social.interact"}) if x_write_active else frozenset()),
    )
    app = ContentApplication(
        root=root,
        subject=subject,
        runtime=runtime,
        domain_runtime=domain_runtime,
        observation_factory=SubjectObservation,
    )
    runtime.register_provider(ContentCapabilityPack(app.status_observation))
    if x_active:
        runtime.register_provider(XCapabilityProvider())

    trusted, preferred, configs = _capability_pack_deployment()
    policy = CapabilityPolicy()
    policy.auto_acquire["read_only_skill"] = True
    manager = CapabilityPackManager(
        runtime,
        InstalledCapabilityPackCatalog(trusted_pack_ids=trusted),
        policy=policy,
    )
    try:
        report = manager.reconcile_manifest(
            manifest,
            preferred_pack_by_capability=preferred,
            config_by_pack=configs,
        )
    except Exception:
        manager.close()
        raise
    if not report.ready:
        manager.close()
        missing = ",".join(report.missing_required) or "unknown"
        raise RuntimeError("required Runtime capabilities unavailable: " + missing)
    app.capability_manager = manager
    app.capability_binding_report = report
    return app


def _runtime_authority_key(root: Path) -> bytes:
    path = Path(root) / "application" / "runtime_authority.key"
    if path.exists():
        value = path.read_bytes()
        if len(value) < 32:
            raise ValueError("runtime authority key is invalid")
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(value)
    return value


def _normalize_status(value: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(value)
    return {
        "product": str(row.get("product") or "content-agent"),
        "agent_type": str(row.get("agent_type") or "content-agent"),
        "domain_agent_id": str(row.get("domain_agent_id") or "unknown"),
        "domain_runtime_cycle": max(0, int(row.get("domain_runtime_cycle") or 0)),
        "generation_ready": bool(row.get("generation_ready", False)),
        "publish_mode": str(row.get("publish_mode") or "manual"),
    }


def _instant(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be non-empty")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "APPLICATION_VERSION", "CAP_CONTENT_STATUS", "SUBJECT_ID", "ContentApplication",
    "ContentApplicationTickResult", "ContentCapabilityPack", "build_content_application",
]
