"""Agent Network membership for the persistent Content Agent process."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import Any, Mapping

from .runtime_app import CAP_CONTENT_STATUS

DEFAULT_AGENT_ID = "content.cai.001"
DEFAULT_SEED_URL = "http://127.0.0.1:8001"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8103
DEFAULT_CAPABILITIES = (CAP_CONTENT_STATUS,)


class ContentNetworkService:
    """Run Content Agent's signed A2A endpoint inside its application process."""

    def __init__(
        self,
        *,
        root: Path,
        application: Any,
        seed_url: str = DEFAULT_SEED_URL,
        bind_host: str = DEFAULT_BIND_HOST,
        advertise_host: str | None = None,
        port: int = DEFAULT_PORT,
        agent_id: str = DEFAULT_AGENT_ID,
        capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES,
        startup_timeout_seconds: float = 10.0,
    ) -> None:
        if port <= 0 or port > 65535:
            raise ValueError("network port is invalid")
        if not agent_id.strip() or not seed_url.strip():
            raise ValueError("network agent_id and seed_url must be non-empty")
        self.root = Path(root)
        self.application = application
        self.seed_url = seed_url.rstrip("/")
        self.bind_host = bind_host
        self.advertise_host = advertise_host or bind_host
        self.port = int(port)
        self.agent_id = agent_id
        self.capability_ids = tuple(dict.fromkeys(capabilities))
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.execution_lock = RLock()
        self._server: Any | None = None
        self._thread: Thread | None = None
        self._network: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._joined = Event()
        self._startup_error: BaseException | None = None

    @classmethod
    def from_environment(cls, *, root: Path, application: Any) -> "ContentNetworkService":
        raw_caps = os.environ.get("CONTENT_NETWORK_CAPABILITIES") or ",".join(DEFAULT_CAPABILITIES)
        caps = tuple(item.strip() for item in raw_caps.split(",") if item.strip())
        return cls(
            root=root,
            application=application,
            seed_url=os.environ.get("CONTENT_NETWORK_SEED_URL", DEFAULT_SEED_URL),
            bind_host=os.environ.get("CONTENT_NETWORK_HOST", DEFAULT_BIND_HOST),
            advertise_host=os.environ.get("CONTENT_NETWORK_ADVERTISE_HOST") or None,
            port=int(os.environ.get("CONTENT_NETWORK_PORT", str(DEFAULT_PORT))),
            agent_id=os.environ.get("CONTENT_NETWORK_AGENT_ID", DEFAULT_AGENT_ID),
            capabilities=caps,
        )

    @property
    def identity_path(self) -> Path:
        return self.root / "application" / "network" / "identity.json"

    @property
    def endpoint(self) -> str:
        return f"http://{self.advertise_host}:{self.port}/a2a"

    @property
    def card_url(self) -> str:
        return f"http://{self.advertise_host}:{self.port}/.well-known/agent-card.json"

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
        from agent_network.a2a_server import A2AServer, HandlerResult
        from agent_network.crypto import Identity
        from agent_network.network import AgentNetwork

        rows = self.advertised_capabilities()
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity = Identity.load_or_create(self.identity_path, self.agent_id)
        card = signed_agent_card(
            identity=identity,
            name="Persistent Content Agent",
            description="Content Agent with Runtime + VirtualBrain; manual publishing authority",
            url=self.endpoint,
            skills=[{"id": row["id"], "name": row["id"], "description": row["description"]} for row in rows],
        )
        network = AgentNetwork(
            identity=identity,
            seeds=[self.seed_url],
            agent_card_url=self.card_url,
            endpoint=self.endpoint,
            capabilities=[{k: v for k, v in row.items() if k != "description"} for row in rows],
        )
        self._network = network
        handlers: dict[str, Any] = {}
        for row in rows:
            capability_id = row["id"]
            version = row["version"]

            async def handler(
                payload: dict[str, Any], *, _capability_id: str = capability_id, _version: str = version
            ) -> Any:
                args = _request_data(payload)
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

        async def startup() -> None:
            self._loop = asyncio.get_running_loop()
            try:
                await network.join()
            except BaseException as exc:  # noqa: BLE001
                self._startup_error = exc
                self._joined.set()
                raise
            self._joined.set()

        app = A2AServer(identity=identity, card=card, handlers=handlers).starlette_app(on_start=startup)
        server = uvicorn.Server(uvicorn.Config(app, host=self.bind_host, port=self.port, log_level="info"))
        self._server = server
        self._thread = Thread(target=server.run, name="content-agent-network", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._joined.wait(timeout=0.05) or not self._thread.is_alive():
                break
        if self._startup_error is not None:
            self.stop()
            raise RuntimeError(f"Agent Network join failed: {self._startup_error}") from self._startup_error
        if not self._joined.is_set() or not self._thread.is_alive():
            self.stop()
            raise RuntimeError("Agent Network endpoint did not become ready")

    def stop(self) -> None:
        if self._network is not None and self._loop is not None and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._network.leave(), self._loop)
                future.result(timeout=2.0)
            except Exception:
                pass
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
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


def _request_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    message = dict(payload.get("message") or {})
    for part in message.get("parts") or []:
        if isinstance(part, Mapping) and part.get("kind") == "data" and isinstance(part.get("data"), Mapping):
            return dict(part["data"])
    return {}


__all__ = ["ContentNetworkService", "network_enabled"]
