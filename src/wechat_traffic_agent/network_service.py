"""Agent Network membership for the persistent Content Agent process."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import Any, Mapping

from .runtime_app import CAP_CONTENT_STATUS
from .public_persona import record_agent_capability_demand
from .network_capabilities import (
    CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW,
    NETWORK_WORK_CAPABILITIES, execute_network_capability,
)

DEFAULT_AGENT_ID = "content.cai.001"
DEFAULT_SEED_URL = "http://127.0.0.1:8001"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8103
DEFAULT_CAPABILITIES = (
    CAP_CONTENT_STATUS, CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE, CAP_CONTENT_DRAFT, CAP_CONTENT_REVIEW,
)


def _record_peer_capability_demand(*, root: Path, capability_id: str, payload: Mapping[str, Any],
                                   observed_at: datetime | None = None) -> dict[str, Any]:
    contract = _request_contract(payload)
    source_ref = str((contract or {}).get("contract_id") or payload.get("task_id") or "").strip() or None
    try:
        return record_agent_capability_demand(
            state_root=root, capability_id=capability_id, source_ref=source_ref,
            observed_at=observed_at or datetime.now(timezone.utc),
        )
    except Exception as exc:  # Learning telemetry must not break a peer capability response.
        return {"status": "ERROR", "error_type": type(exc).__name__}


def content_network_seed_urls_from_environment() -> tuple[str, ...]:
    raw = os.environ.get("CONTENT_NETWORK_SEED_URLS")
    values = (
        tuple(item.strip() for item in raw.split(",") if item.strip())
        if raw is not None
        else (os.environ.get("CONTENT_NETWORK_SEED_URL", DEFAULT_SEED_URL),)
    )
    cleaned = tuple(dict.fromkeys(item.strip().rstrip("/") for item in values if item.strip()))
    if not cleaned:
        raise ValueError("CONTENT_NETWORK_SEED_URLS must contain at least one non-empty URL")
    return cleaned


class ContentNetworkService:
    """Run Content Agent's signed A2A endpoint inside its application process."""

    def __init__(
        self,
        *,
        root: Path,
        application: Any,
        seed_url: str = DEFAULT_SEED_URL,
        seed_urls: tuple[str, ...] | None = None,
        bind_host: str = DEFAULT_BIND_HOST,
        advertise_host: str | None = None,
        public_base_url: str | None = None,
        port: int = DEFAULT_PORT,
        agent_id: str = DEFAULT_AGENT_ID,
        capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES,
        startup_timeout_seconds: float = 10.0,
        join_retry_seconds: float = 30.0,
        lease_seconds: int = 1800,
        engagement_enabled: bool = False,
    ) -> None:
        if port <= 0 or port > 65535:
            raise ValueError("network port is invalid")
        if not agent_id.strip():
            raise ValueError("network agent_id must be non-empty")
        if lease_seconds < 60:
            raise ValueError("network lease_seconds must be at least 60")
        if float(startup_timeout_seconds) <= 0:
            raise ValueError("network startup_timeout_seconds must be positive")
        if float(join_retry_seconds) <= 0:
            raise ValueError("network join_retry_seconds must be positive")
        raw_seeds = (seed_url,) if seed_urls is None else seed_urls
        cleaned_seeds = tuple(dict.fromkeys(item.strip().rstrip("/") for item in raw_seeds if item.strip()))
        if not cleaned_seeds:
            raise ValueError("network seed_urls must contain at least one non-empty URL")
        self.root = Path(root)
        self.application = application
        self.seed_urls = cleaned_seeds
        self.seed_url = cleaned_seeds[0]
        self.bind_host = bind_host
        self.advertise_host = advertise_host or bind_host
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self.port = int(port)
        self.agent_id = agent_id
        self.capability_ids = tuple(dict.fromkeys(capabilities))
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.join_retry_seconds = float(join_retry_seconds)
        self.lease_seconds = int(lease_seconds)
        self.engagement_enabled = bool(engagement_enabled)
        self.execution_lock = RLock()
        self._server: Any | None = None
        self._thread: Thread | None = None
        self._network: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._joined = Event()
        self._startup_error: BaseException | None = None
        self._join_task: asyncio.Task | None = None

    @classmethod
    def from_environment(cls, *, root: Path, application: Any) -> "ContentNetworkService":
        raw_caps = os.environ.get("CONTENT_NETWORK_CAPABILITIES") or ",".join(DEFAULT_CAPABILITIES)
        caps = tuple(item.strip() for item in raw_caps.split(",") if item.strip())
        return cls(
            root=root,
            application=application,
            seed_url=os.environ.get("CONTENT_NETWORK_SEED_URL", DEFAULT_SEED_URL),
            seed_urls=content_network_seed_urls_from_environment(),
            bind_host=os.environ.get("CONTENT_NETWORK_HOST", DEFAULT_BIND_HOST),
            advertise_host=os.environ.get("CONTENT_NETWORK_ADVERTISE_HOST") or None,
            public_base_url=os.environ.get("CONTENT_NETWORK_PUBLIC_BASE_URL") or None,
            port=int(os.environ.get("CONTENT_NETWORK_PORT", str(DEFAULT_PORT))),
            agent_id=os.environ.get("CONTENT_NETWORK_AGENT_ID", DEFAULT_AGENT_ID),
            capabilities=caps,
            startup_timeout_seconds=float(os.environ.get("CONTENT_NETWORK_STARTUP_TIMEOUT_SECONDS", "10")),
            join_retry_seconds=float(os.environ.get("CONTENT_NETWORK_JOIN_RETRY_SECONDS", "30")),
            lease_seconds=int(os.environ.get("CONTENT_NETWORK_LEASE_SECONDS", "1800")),
            engagement_enabled=(os.environ.get("CONTENT_WEB_ENGAGEMENT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"},
        )

    @property
    def identity_path(self) -> Path:
        return self.root / "application" / "network" / "identity.json"

    @property
    def join_status_path(self) -> Path:
        return self.root / "application" / "network" / "join_status.json"

    @property
    def joined(self) -> bool:
        return self._joined.is_set()

    @property
    def join_error_type(self) -> str | None:
        return type(self._startup_error).__name__ if self._startup_error is not None else None

    def _record_join_status(self, status: str, *, error: BaseException | None = None) -> None:
        row = {
            "status": str(status),
            "observed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "endpoint": self.endpoint,
            "seed_count": len(self.seed_urls),
            "error_type": type(error).__name__ if error is not None else None,
            "error_code": str(getattr(error, "code", "") or "") if error is not None else "",
        }
        path = self.join_status_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @property
    def public_base(self) -> str:
        if self.public_base_url:
            return self.public_base_url
        return f"http://{self.advertise_host}:{self.port}"

    @property
    def endpoint(self) -> str:
        return f"{self.public_base}/a2a"

    @property
    def card_url(self) -> str:
        return f"{self.public_base}/.well-known/agent-card.json"

    def advertised_capabilities(self) -> tuple[dict[str, Any], ...]:
        allowed = set(self.capability_ids)
        rows: list[dict[str, Any]] = []
        for raw in self.application.runtime.capability_manifest():
            row = dict(raw)
            capability_id = str(row.get("capability_id") or "")
            if capability_id not in allowed:
                continue
            if str(row.get("side_effect_level") or "READ_ONLY") != "READ_ONLY":
                raise ValueError(f"network capability must be READ_ONLY: {capability_id}")
            rows.append({
                "id": capability_id,
                "version": str(row.get("version") or "1.0.0"),
                "side_effects": "NONE",
                "input_schema": dict(row.get("input_schema") or {}),
                "output_schema": dict(row.get("output_schema") or {}),
                "description": str(row.get("description") or capability_id),
            })
        missing = sorted(allowed - {row["id"] for row in rows})
        if missing:
            raise ValueError(f"network capabilities not registered in Runtime: {','.join(missing)}")
        return tuple(rows)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ContentNetworkService already started")
        import uvicorn
        from agent_network.a2a_card import signed_agent_card
        from agent_network.a2a_server import A2AServer, HandlerResult, json_artifact
        from agent_network.crypto import Identity
        from agent_network.models import utc_now
        from agent_network.network import AgentNetwork

        rows = self.advertised_capabilities()
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity = Identity.load_or_create(self.identity_path, self.agent_id)
        card = signed_agent_card(
            identity=identity,
            name="Persistent Content Agent",
            description="Persistent research and content agent: evidence research, bounded drafting, review, and observable runtime state.",
            url=self.endpoint,
            skills=[{"id": row["id"], "name": row["id"], "description": row["description"]} for row in rows],
        )
        network = AgentNetwork(
            identity=identity,
            seeds=list(self.seed_urls),
            agent_card_url=self.card_url,
            endpoint=self.endpoint,
            capabilities=[{k: v for k, v in row.items() if k != "description"} for row in rows],
            lease_seconds=self.lease_seconds,
        )
        self._network = network
        handlers: dict[str, Any] = {}
        work_lock = asyncio.Lock()
        for row in rows:
            capability_id = row["id"]
            version = row["version"]

            async def handler(
                payload: dict[str, Any], *, _capability_id: str = capability_id, _version: str = version
            ) -> Any:
                args = _request_data(payload)
                if _capability_id in NETWORK_WORK_CAPABILITIES:
                    contract = _request_contract(payload)
                    if contract and not isinstance((contract.get("metadata") or {}).get("capability_input"), Mapping):
                        raise ValueError("signed content task requires contract metadata capability_input")
                    demand_learning = _record_peer_capability_demand(
                        root=self.root, capability_id=_capability_id, payload=payload,
                    )
                    async with work_lock:
                        executing = False
                        try:
                            if contract:
                                await network.submit_lifecycle_event(
                                    contract_id=str(contract["contract_id"]), status="EXECUTING",
                                    contract_hash=str(contract.get("contract_hash") or "") or None,
                                )
                                executing = True
                            result = await execute_network_capability(self.application, _capability_id, args)
                            metadata = {
                                "capability_id": _capability_id,
                                "capability_version": _version,
                                "provider": self.agent_id,
                                "execution_id": result.get("execution_id"),
                                "result": result,
                                "peer_demand_learning": demand_learning,
                            }
                            artifacts = []
                            if contract:
                                manifest = json_artifact(
                                    task_id=str(payload.get("task_id") or contract["contract_id"]),
                                    artifact_type="content-capability-result", payload=result,
                                    owner=self.agent_id, directory=self.root / "application" / "network" / "artifacts",
                                    identity=identity, public_base=self.public_base_url,
                                )
                                # Evidence binds the artifact to this exact signed contract. The public
                                # A2A artifact remains a normal ArtifactManifest, while the immutable
                                # Evidence record carries the causal contract binding used by acceptance.
                                artifact_evidence = manifest.to_dict()
                                artifact_evidence.update({
                                    "contract_id": str(contract["contract_id"]),
                                    "contract_hash": str(contract.get("contract_hash") or ""),
                                    "key_id": identity.key_id,
                                    "timestamp": utc_now(),
                                })
                                artifact_evidence["signature"] = identity.sign_object(artifact_evidence)
                                artifact_receipt = await network.reference_artifact(artifact_evidence)
                                manifest_hash = str(artifact_receipt.get("object_hash") or "")
                                if not manifest_hash:
                                    raise RuntimeError("Agent Network did not persist the delivered artifact reference")
                                await network.submit_lifecycle_event(
                                    contract_id=str(contract["contract_id"]), status="DELIVERED",
                                    artifact_hashes=[manifest.sha256],
                                    contract_hash=str(contract.get("contract_hash") or "") or None,
                                )
                                metadata["delivery"] = {
                                    "artifact_id": manifest.artifact_id,
                                    "artifact_manifest_hash": manifest_hash,
                                    "artifact_sha256": manifest.sha256,
                                    "status": "DELIVERED",
                                }
                                artifacts = [manifest]
                            return HandlerResult(metadata=metadata, artifacts=artifacts)
                        except Exception:
                            if contract and executing:
                                try:
                                    await network.submit_lifecycle_event(
                                        contract_id=str(contract["contract_id"]), status="FAILED",
                                        contract_hash=str(contract.get("contract_hash") or "") or None,
                                    )
                                except Exception:
                                    pass
                            raise
                with self.execution_lock:
                    receipt = self.application.runtime.invoke(_capability_id, args)
                    self.application.runtime.persist()
                return HandlerResult(metadata={
                    "capability_id": _capability_id,
                    "capability_version": _version,
                    "provider": receipt.provider,
                    "receipt_id": receipt.receipt_id,
                    "result": dict(receipt.payload),
                })

            handlers[capability_id] = handler

        async def join_with_retry() -> None:
            while True:
                try:
                    await network.join()
                    self._startup_error = None
                    self._joined.set()
                    self._record_join_status("JOINED")
                    return
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    self._startup_error = exc
                    self._joined.clear()
                    self._record_join_status("RETRYING", error=exc)
                    await asyncio.sleep(self.join_retry_seconds)

        async def startup() -> None:
            self._loop = asyncio.get_running_loop()
            self._record_join_status("PENDING")
            self._join_task = self._loop.create_task(join_with_retry())

        app = A2AServer(identity=identity, card=card, handlers=handlers).starlette_app(on_start=startup)
        if self.engagement_enabled:
            from starlette.responses import JSONResponse, Response
            from starlette.routing import Route
            from .website_engagement import MAX_BODY_BYTES, WebsiteEngagementStore, ingest_engagement_body

            engagement_store = WebsiteEngagementStore(self.root / "content" / "website_engagement")

            async def engagement_endpoint(request):
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return JSONResponse({"status": "UNSUPPORTED_MEDIA_TYPE"}, status_code=415)
                body = await request.body()
                if len(body) > MAX_BODY_BYTES:
                    return Response(status_code=413)
                try:
                    result = ingest_engagement_body(engagement_store, body, observed_at=datetime.now(timezone.utc))
                except ValueError as exc:
                    return JSONResponse({"status": "INVALID", "error_type": type(exc).__name__}, status_code=400)
                return JSONResponse(result, status_code=202)

            app.routes.append(Route("/events", engagement_endpoint, methods=["POST"]))
        server = uvicorn.Server(uvicorn.Config(app, host=self.bind_host, port=self.port, log_level="info"))
        self._server = server
        self._thread = Thread(target=server.run, name="content-agent-network", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if bool(getattr(server, "started", False)) or not self._thread.is_alive():
                break
            time.sleep(0.05)
        if not bool(getattr(server, "started", False)) or not self._thread.is_alive():
            self.stop()
            raise RuntimeError("Agent Network local endpoint did not become ready")

    def stop(self) -> None:
        loop = self._loop
        if self._join_task is not None and loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._join_task.cancel)
            except Exception:
                pass
        if self.joined and self._network is not None and loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._network.leave(), loop)
                future.result(timeout=2.0)
            except Exception:
                pass
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._joined.clear()
        self._join_task = None
        self._thread = self._server = self._network = self._loop = None

    def discover(self, capability: str, *, timeout: float = 10.0) -> list[dict[str, Any]]:
        if self._network is None or self._loop is None:
            raise RuntimeError("ContentNetworkService is not started")
        future = asyncio.run_coroutine_threadsafe(self._network.discover(capability), self._loop)
        return [dict(item.raw) for item in future.result(timeout=timeout)]

    def read_remote(self, capability: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 15.0) -> dict[str, Any]:
        if self._network is None or self._loop is None:
            raise RuntimeError("ContentNetworkService is not started")

        async def _call() -> dict[str, Any]:
            agent = await self._network.require(capability)
            peer = await self._network.connect(agent)
            task = await peer.a2a.message_send({
                "role": "user",
                "parts": [{"kind": "data", "data": dict(payload or {})}],
                "metadata": {"capability": capability, "requester_agent_id": self.agent_id},
            })
            return {"provider_agent_id": agent.agent_id, "provider_network_id": agent.network_id, "provider_endpoint": peer.endpoint, "task": task}

        return asyncio.run_coroutine_threadsafe(_call(), self._loop).result(timeout=timeout)


def network_enabled() -> bool:
    return (os.environ.get("CONTENT_NETWORK_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _request_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        return {}
    contract = metadata.get("contract") or {}
    return dict(contract) if isinstance(contract, Mapping) else {}


def _request_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    # A signed contract is authoritative for delegated work. Do not let an unsigned
    # message body override capability_input after both parties signed the contract.
    contract = _request_contract(payload)
    if contract:
        metadata = contract.get("metadata") or {}
        bound = metadata.get("capability_input") if isinstance(metadata, Mapping) else None
        return dict(bound) if isinstance(bound, Mapping) else {}
    message = dict(payload.get("message") or {})
    for part in message.get("parts") or []:
        if isinstance(part, Mapping) and part.get("kind") == "data" and isinstance(part.get("data"), Mapping):
            return dict(part["data"])
    return {}


__all__ = ["ContentNetworkService", "network_enabled"]
