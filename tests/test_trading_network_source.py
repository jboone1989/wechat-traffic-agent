import asyncio
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from wechat_traffic_agent.sources.trading_network import (
    CAP_TRADING_PUBLIC_RESEARCH,
    CAP_TRADING_PUBLIC_RESEARCH_VERSION,
    TRADING_RESEARCH_ARTIFACT_TYPE,
    TRADING_RESEARCH_MEDIA_TYPE,
    TradingResearchNetworkSource,
)


def _envelope(**extra):
    payload = {
        "schema_version": "trading.public-research.v1",
        "producer_agent_id": "trading-agent",
        "generated_at": "2026-09-14T08:00:00+00:00",
        "items": [
            {
                "item_id": "report-1",
                "title": "Why one candidate remained under observation",
                "summary": "A prospective evaluation had clean data but insufficient independent evidence for promotion.",
                "observed_at": "2026-09-13T08:00:00+00:00",
                "evidence_refs": ["trading:report:report-1"],
                "limitations": ["sample_count_below_threshold"],
                "tags": ["validation", "sample size"],
            }
        ],
    }
    payload.update(extra)
    return payload


def _body(payload=None):
    return json.dumps(payload or _envelope(), ensure_ascii=False).encode()


class _Peer:
    def __init__(self, task):
        self.task = task
        self.calls = []

    async def propose_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.task


class _Identity:
    agent_id = "content-agent"
    key_id = "content-key"

    def sign_object(self, _payload):
        return "ed25519:content-signature"


class _Discovery:
    def __init__(self, agent):
        self.agents = [agent]

    def contract_metadata(self, provider_id):
        return {
            "agent_network": {
                "discovery_query_id": "q-test-1",
                "discovery_capability": CAP_TRADING_PUBLIC_RESEARCH,
                "selected_agent_id": provider_id,
                "shadow_policy": "provider-reputation-shadow-v1",
                "shadow_state": "READY",
                "shadow_recommended_agent_id": provider_id,
                "selected_shadow_rank": 1,
                "controls_production": False,
                "discovery_observed_at": "2026-09-15T12:00:00Z",
            }
        }


class _Network:
    def __init__(self, task):
        self.identity = _Identity()
        self.agent = SimpleNamespace(agent_id="trading-agent")
        self.peer = _Peer(task)
        self.discovered = []
        self.acceptances = []

    async def discover_with_context(self, capability, *, version=None, max_results=10):
        self.discovered.append((capability, version, max_results))
        return _Discovery(self.agent)

    async def connect(self, agent):
        assert agent is self.agent
        return self.peer

    async def submit_acceptance(self, acceptance):
        self.acceptances.append(dict(acceptance))
        return {"stored": True, "object_hash": "sha256:acceptance"}


def _task(body=None):
    raw = body or _body()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return {
        "status": {"state": "completed"},
        "metadata": {
            "contract": {
                "contract_id": "contract-1",
                "contract_hash": "sha256:contract-1",
                "requester": "content-agent",
                "executor": "trading-agent",
            },
            "network_evidence": {
                "contract_id": "contract-1",
                "contract_hash": "sha256:contract-1",
                "artifact_id": "artifact-1",
                "artifact_hash": digest,
                "artifact_manifest_hash": "sha256:artifact-manifest-1",
                "lifecycle": "DELIVERED",
            },
        },
        "artifacts": [
            {
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "artifact_id": "artifact-1",
                            "task_id": "task-1",
                            "type": TRADING_RESEARCH_ARTIFACT_TYPE,
                            "media_type": TRADING_RESEARCH_MEDIA_TYPE,
                            "owner": "trading-agent",
                            "sha256": digest,
                            "revision": 2,
                        },
                    }
                ]
            }
        ],
    }


def test_network_source_discovers_only_public_research_and_keeps_provenance():
    body = _body()
    network = _Network(_task(body))

    async def loader(_manifest):
        return body

    topics = asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics(limit=10))

    digest = hashlib.sha256(body).hexdigest()
    assert network.discovered == [(CAP_TRADING_PUBLIC_RESEARCH, CAP_TRADING_PUBLIC_RESEARCH_VERSION, 10)]
    assert network.peer.calls[0]["side_effects"] == "NONE"
    assert network.peer.calls[0]["metadata"]["agent_network"]["discovery_query_id"] == "q-test-1"
    assert len(network.acceptances) == 1
    assert network.acceptances[0]["result"] == "ACCEPTED"
    assert network.acceptances[0]["artifact_manifest_hash"] == "sha256:artifact-manifest-1"
    assert topics[0].source == "agent-network:trading-agent"
    assert topics[0].source_revision == "artifact-1@2"
    assert "network-contract:contract-1" in topics[0].evidence_refs
    assert "trading:report:report-1" in topics[0].evidence_refs
    assert f"network-artifact:artifact-1@2:sha256:{digest}" in topics[0].evidence_refs
    assert not any("sha256:sha256:" in ref for ref in topics[0].evidence_refs)


def test_network_source_accepts_sdk_prefixed_and_legacy_bare_sha256():
    body = _body()
    task = _task(body)
    digest = hashlib.sha256(body).hexdigest()
    network = _Network(task)

    async def loader(_manifest):
        return body

    prefixed = asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())
    assert len(prefixed) == 1

    task["artifacts"][0]["parts"][0]["data"]["sha256"] = digest
    bare = asyncio.run(TradingResearchNetworkSource(_Network(task), artifact_loader=loader).fetch_topics())
    assert len(bare) == 1


def test_network_source_accepts_valid_empty_research_as_empty_topics():
    body = _body(_envelope(items=[]))
    network = _Network(_task(body))

    async def loader(_manifest):
        return body

    topics = asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())
    assert topics == []


def test_network_source_rejects_private_or_control_fields_before_modeling():
    poisoned = _envelope()
    poisoned["items"][0]["position_size"] = 1.0
    body = _body(poisoned)
    network = _Network(_task(body))

    async def loader(_manifest):
        return body

    with pytest.raises(ValueError, match="forbidden field"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_artifact_hash_mismatch_even_with_injected_loader():
    body = _body()
    task = _task(body)
    task["artifacts"][0]["parts"][0]["data"]["sha256"] = "sha256:" + "0" * 64
    network = _Network(task)

    async def loader(_manifest):
        return body

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_malformed_artifact_digest():
    body = _body()
    task = _task(body)
    task["artifacts"][0]["parts"][0]["data"]["sha256"] = "sha256:not-a-digest"
    network = _Network(task)

    async def loader(_manifest):
        return body

    with pytest.raises(ValueError, match="SHA256 is invalid"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_artifact_from_wrong_agent():
    body = _body()
    task = _task(body)
    task["artifacts"][0]["parts"][0]["data"]["owner"] = "other-agent"
    network = _Network(task)

    async def loader(_manifest):
        return body

    with pytest.raises(ValueError, match="producer"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_naive_since_timestamp():
    body = _body()
    network = _Network(_task(body))

    async def loader(_manifest):
        return body

    source = TradingResearchNetworkSource(network, artifact_loader=loader)
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(source.fetch_topics(since=datetime(2026, 9, 14)))


def test_network_source_does_not_treat_failed_task_as_empty_success():
    body = _body()
    task = _task(body)
    task["status"] = {"state": "failed"}
    network = _Network(task)

    async def loader(_manifest):
        return body

    with pytest.raises(RuntimeError, match="did not complete"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())
