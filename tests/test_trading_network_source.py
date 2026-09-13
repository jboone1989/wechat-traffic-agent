import asyncio
import json
from datetime import datetime, timezone
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


class _Peer:
    def __init__(self, task):
        self.task = task
        self.calls = []

    async def propose_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.task


class _Network:
    def __init__(self, task):
        self.identity = object()
        self.agent = SimpleNamespace(agent_id="trading-agent")
        self.peer = _Peer(task)
        self.required = []

    async def require(self, capability, version=None):
        self.required.append((capability, version))
        return self.agent

    async def connect(self, agent):
        assert agent is self.agent
        return self.peer


def _task():
    return {
        "status": {"state": "completed"},
        "metadata": {"contract": {"contract_id": "contract-1"}},
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
                            "sha256": "abc123",
                            "revision": 2,
                        },
                    }
                ]
            }
        ],
    }


def test_network_source_discovers_only_public_research_and_keeps_provenance():
    network = _Network(_task())

    async def loader(_manifest):
        return json.dumps(_envelope()).encode()

    topics = asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics(limit=10))

    assert network.required == [(CAP_TRADING_PUBLIC_RESEARCH, CAP_TRADING_PUBLIC_RESEARCH_VERSION)]
    assert network.peer.calls[0]["side_effects"] == "NONE"
    assert topics[0].source == "agent-network:trading-agent"
    assert topics[0].source_revision == "artifact-1@2"
    assert "network-contract:contract-1" in topics[0].evidence_refs
    assert "trading:report:report-1" in topics[0].evidence_refs
    assert any(ref.startswith("network-artifact:artifact-1@2:sha256:") for ref in topics[0].evidence_refs)


def test_network_source_rejects_private_or_control_fields_before_modeling():
    network = _Network(_task())
    poisoned = _envelope()
    poisoned["items"][0]["position_size"] = 1.0

    async def loader(_manifest):
        return json.dumps(poisoned).encode()

    with pytest.raises(ValueError, match="forbidden field"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_artifact_from_wrong_agent():
    task = _task()
    task["artifacts"][0]["parts"][0]["data"]["owner"] = "other-agent"
    network = _Network(task)

    async def loader(_manifest):
        return json.dumps(_envelope()).encode()

    with pytest.raises(ValueError, match="producer"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())


def test_network_source_rejects_naive_since_timestamp():
    network = _Network(_task())

    async def loader(_manifest):
        return json.dumps(_envelope()).encode()

    source = TradingResearchNetworkSource(network, artifact_loader=loader)
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(source.fetch_topics(since=datetime(2026, 9, 14)))


def test_network_source_does_not_treat_failed_task_as_empty_success():
    task = _task()
    task["status"] = {"state": "failed"}
    network = _Network(task)

    async def loader(_manifest):
        return json.dumps(_envelope()).encode()

    with pytest.raises(RuntimeError, match="did not complete"):
        asyncio.run(TradingResearchNetworkSource(network, artifact_loader=loader).fetch_topics())
