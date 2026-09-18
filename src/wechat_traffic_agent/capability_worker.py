"""Minimal public capability worker: no VirtualBrain subject and no publishing authority."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .network_capabilities import CAP_CONTENT_EVIDENCE, CAP_CONTENT_RESEARCH
from .runtime_app import ContentCapabilityPack, _capability_pack_deployment


PUBLIC_WORKER_CAPABILITIES = frozenset({CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE})


class PublicCapabilityWorkerApplication:
    def __init__(self, *, root: Path, worker_id: str, runtime: Any, capability_manager: Any) -> None:
        self.root = Path(root)
        self.worker_id = str(worker_id)
        self.runtime = runtime
        self.capability_manager = capability_manager
        # Research/evidence execution never touches domain_runtime, ContentMind, or VirtualBrain.
        self.domain_runtime = None

    def status_observation(self) -> dict[str, Any]:
        available = set(self.runtime.available_capabilities())
        return {
            "product": "content-capability-worker",
            "agent_type": "public-research-worker",
            "domain_agent_id": self.worker_id,
            "domain_runtime_cycle": int(getattr(self.runtime, "cycle", 0)),
            "generation_ready": {"web.search", "web.fetch"}.issubset(available),
            "publish_mode": "none",
        }

    def close(self) -> None:
        manager = self.capability_manager
        self.capability_manager = None
        if manager is not None:
            manager.close()


def build_public_capability_worker(*, root: Path, worker_id: str) -> PublicCapabilityWorkerApplication:
    from agent_runtime import ApplicationRuntime
    from agent_runtime.capabilities import (
        CapabilityNeed,
        CapabilityPackManager,
        CapabilityPolicy,
        InstalledCapabilityPackCatalog,
    )

    root = Path(root)
    runtime = ApplicationRuntime.bootstrap(
        root / "application" / "runtime",
        subject_id=str(worker_id),
        max_side_effect_level="READ_ONLY",
        allowed_permissions=frozenset(),
    )
    trusted, preferred, configs = _capability_pack_deployment()
    policy = CapabilityPolicy()
    policy.auto_acquire["read_only_skill"] = True
    manager = CapabilityPackManager(
        runtime,
        InstalledCapabilityPackCatalog(trusted_pack_ids=trusted),
        policy=policy,
    )
    try:
        for capability_id in ("web.search", "web.fetch"):
            manager.bind_need(
                CapabilityNeed.simple(
                    capability_id,
                    intent="public research worker dependency",
                ),
                preferred_pack_id=preferred.get(capability_id),
                config_by_pack=configs,
            )
        available = set(runtime.available_capabilities())
        missing = sorted({"web.search", "web.fetch"} - available)
        if missing:
            raise RuntimeError("public worker dependencies unavailable: " + ",".join(missing))
        app = PublicCapabilityWorkerApplication(
            root=root,
            worker_id=worker_id,
            runtime=runtime,
            capability_manager=manager,
        )
        runtime.register_provider(ContentCapabilityPack(app.status_observation))
        runtime.persist()
        return app
    except Exception:
        manager.close()
        raise


__all__ = [
    "PUBLIC_WORKER_CAPABILITIES",
    "PublicCapabilityWorkerApplication",
    "build_public_capability_worker",
]
