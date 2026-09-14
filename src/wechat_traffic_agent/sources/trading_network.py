"""Read-only Trading research source over Agent Network.

This adapter deliberately depends on Network semantics rather than Trading
implementation modules. Trading remains responsible for producing the governed
public projection; Content Agent validates it again before topic modeling.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import TopicSignal


CAP_TRADING_PUBLIC_RESEARCH = "crypto.research.public"
CAP_TRADING_PUBLIC_RESEARCH_VERSION = "1.0.0"
TRADING_RESEARCH_ARTIFACT_TYPE = "trading.public-research.v1"
TRADING_RESEARCH_MEDIA_TYPE = "application/vnd.trading.public-research+json"


class _NetworkLike(Protocol):
    identity: Any

    async def require(self, capability: str, version: str | None = None) -> Any: ...

    async def connect(self, agent: Any) -> Any: ...


class TradingResearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    observed_at: datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=50)
    limitations: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class TradingResearchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trading.public-research.v1"]
    producer_agent_id: str = Field(min_length=1, max_length=200)
    generated_at: datetime
    items: tuple[TradingResearchItem, ...] = Field(max_length=100)

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


ArtifactLoader = Callable[[dict[str, Any]], Awaitable[bytes]]


class TradingResearchNetworkSource:
    """Discover Trading via Agent Network and consume governed public research."""

    def __init__(
        self,
        network: _NetworkLike,
        *,
        artifact_loader: ArtifactLoader | None = None,
    ) -> None:
        self._network = network
        self._artifact_loader = artifact_loader

    async def fetch_topics(
        self,
        *,
        limit: int = 20,
        since: datetime | None = None,
    ) -> list[TopicSignal]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if since is not None and (since.tzinfo is None or since.utcoffset() is None):
            raise ValueError("since must be timezone-aware")

        agent = await self._network.require(
            CAP_TRADING_PUBLIC_RESEARCH,
            version=CAP_TRADING_PUBLIC_RESEARCH_VERSION,
        )
        provider_id = str(getattr(agent, "agent_id", "") or "")
        if not provider_id:
            raise ValueError("Agent Network returned a provider without agent_id")
        peer = await self._network.connect(agent)
        request: dict[str, Any] = {"limit": limit}
        if since is not None:
            request["since"] = since.isoformat()

        a2a = getattr(peer, "a2a", None)
        message_send = getattr(a2a, "message_send", None)
        if callable(message_send):
            requester_id = str(
                getattr(getattr(self._network, "identity", None), "agent_id", "") or ""
            )
            task = await message_send(
                {
                    "role": "user",
                    "parts": [{"kind": "data", "data": request}],
                    "metadata": {
                        "capability": CAP_TRADING_PUBLIC_RESEARCH,
                        "requester_agent_id": requester_id,
                    },
                }
            )
        else:
            task = await peer.propose_task(
                capability=CAP_TRADING_PUBLIC_RESEARCH,
                capability_version=CAP_TRADING_PUBLIC_RESEARCH_VERSION,
                objective="Provide delayed, public-safe Trading research evidence for editorial use.",
                constraints=[
                    "read-only",
                    "no current positions, orders, credentials, or execution controls",
                    "return only the versioned public research projection",
                ],
                deliverables=[TRADING_RESEARCH_ARTIFACT_TYPE],
                verification={"required": False},
                metadata={"content_agent_request": request},
                side_effects="NONE",
            )
        state = str(((task or {}).get("status") or {}).get("state") or "").lower()
        if state != "completed":
            raise RuntimeError(f"Trading research Network task did not complete: {state or 'unknown'}")

        metadata = dict((task or {}).get("metadata") or {})
        runtime_result = metadata.get("result")
        if isinstance(runtime_result, Mapping) and runtime_result.get("ok") is False:
            status = str(runtime_result.get("status") or "UNAVAILABLE")
            raise RuntimeError(f"Trading research unavailable: {status}")

        manifest = _select_research_artifact(task, required=False)
        if manifest is not None:
            artifact_provider = str(
                manifest.get("producer_agent_id") or manifest.get("owner") or ""
            )
            if artifact_provider != provider_id:
                raise ValueError("research artifact producer does not match discovered Trading agent")
            body = await self._load_artifact(manifest)
            _verify_artifact_hash(manifest, body)
            try:
                raw = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Trading research artifact is not valid UTF-8 JSON") from exc
            revision = f"{manifest.get('artifact_id', '')}@{manifest.get('revision', 1)}"
            artifact_ref = _artifact_evidence_ref(manifest)
        else:
            if not isinstance(runtime_result, Mapping) or not isinstance(runtime_result.get("envelope"), Mapping):
                raise ValueError("Trading research response contains neither artifact nor Runtime envelope")
            raw = dict(runtime_result["envelope"])
            receipt_id = str(metadata.get("receipt_id") or "")
            if not receipt_id:
                raise ValueError("Trading research Runtime receipt_id is required")
            revision = receipt_id
            artifact_ref = f"runtime-receipt:{receipt_id}"

        _reject_forbidden_fields(raw)
        envelope = TradingResearchEnvelope.model_validate(raw)
        if envelope.producer_agent_id != provider_id:
            raise ValueError("research envelope producer does not match discovered Trading agent")

        contract = metadata.get("contract") or {}
        contract_id = str(contract.get("contract_id") or "")
        network_refs = tuple(
            ref for ref in (artifact_ref, f"network-contract:{contract_id}" if contract_id else "") if ref
        )
        source_name = f"agent-network:{provider_id}"[:80]
        topics: list[TopicSignal] = []
        for item in envelope.items:
            if since is not None and item.observed_at < since:
                continue
            refs = tuple(dict.fromkeys((*network_refs, *item.evidence_refs)))
            summary = item.summary
            if item.limitations:
                limitations = " Limitations: " + "; ".join(item.limitations)
                summary = (summary + limitations)[:500]
            topics.append(
                TopicSignal(
                    title=item.title,
                    summary=summary,
                    source=source_name,
                    source_revision=revision,
                    evidence_refs=refs,
                    observed_at=item.observed_at,
                )
            )
            if len(topics) >= limit:
                break
        return topics

    async def _load_artifact(self, manifest: dict[str, Any]) -> bytes:
        if self._artifact_loader is not None:
            return await self._artifact_loader(manifest)
        try:
            from agent_network.artifact_ref import fetch_artifact
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError(
                "agent-network-sdk is required to materialize Network artifacts"
            ) from exc
        identity = getattr(self._network, "identity", None)
        if identity is None:
            raise RuntimeError("Agent Network identity is required for artifact access")
        return await fetch_artifact(manifest, identity=identity)


def _select_research_artifact(
    task: Mapping[str, Any], *, required: bool = True
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("kind") != "data" or not isinstance(part.get("data"), dict):
                continue
            manifest = dict(part["data"])
            if (
                manifest.get("type") == TRADING_RESEARCH_ARTIFACT_TYPE
                and manifest.get("media_type") == TRADING_RESEARCH_MEDIA_TYPE
            ):
                matches.append(manifest)
    if not matches and not required:
        return None
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Trading research artifact, found {len(matches)}")
    manifest = matches[0]
    required_fields = ("artifact_id", "sha256", "revision")
    if any(not manifest.get(field) for field in required_fields):
        raise ValueError("Trading research artifact manifest is incomplete")
    return manifest


def _verify_artifact_hash(manifest: Mapping[str, Any], body: bytes) -> None:
    expected = str(manifest.get("sha256") or "").strip().lower()
    if expected.startswith("sha256:"):
        expected = expected.split(":", 1)[1]
    actual = hashlib.sha256(body).hexdigest()
    if expected != actual:
        raise ValueError("Trading research artifact SHA256 mismatch")


def _artifact_evidence_ref(manifest: Mapping[str, Any]) -> str:
    artifact_id = str(manifest.get("artifact_id") or "")
    digest = str(manifest.get("sha256") or "")
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    revision = manifest.get("revision", 1)
    if not artifact_id or not digest:
        return ""
    return f"network-artifact:{artifact_id}@{revision}:sha256:{digest}"


_FORBIDDEN_KEYS = {
    "account",
    "account_id",
    "accounts",
    "api_key",
    "api_secret",
    "credential",
    "credentials",
    "current_position",
    "entry_price",
    "exchange_key",
    "leverage",
    "live_qty",
    "margin",
    "notional",
    "open_orders",
    "order",
    "order_id",
    "orders",
    "position",
    "position_size",
    "positions",
    "private_key",
    "quantity",
    "secret",
    "token",
}
_FORBIDDEN_FRAGMENTS = ("password", "credential", "private_key", "api_token", "auth_token")


def _reject_forbidden_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS or any(part in normalized for part in _FORBIDDEN_FRAGMENTS):
                raise ValueError(f"Trading research artifact contains forbidden field at {path}.{key}")
            _reject_forbidden_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_fields(child, path=f"{path}[{index}]")
