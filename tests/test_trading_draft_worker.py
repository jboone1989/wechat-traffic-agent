import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.models import TopicSignal
from wechat_traffic_agent.trading_draft_worker import TradingResearchDraftWorker


class _Runtime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self):
        self.calls = 0
        self.persisted = 0

    async def cognitive_probe(self, task: str, max_tokens: int) -> str:
        self.calls += 1
        assert "trading.cai.001" in task
        return json.dumps({
            "title": "Why a Research Scan Can Reject Every Trading Candidate",
            "summary": "A practical explanation of what an all-rejected scan does and does not prove.",
            "sections": [
                {"heading": "What the scan observed", "paragraphs": ["The supplied evidence records a bounded candidate scan."]},
                {"heading": "What rejection means", "paragraphs": ["A rejected candidate is evidence against promotion, not proof that no future edge can exist."]},
            ],
            "topic": "Research scan evaluated 5 candidate relationships",
            "source_urls": [],
            "disclosure": "Research evidence only; not investment advice.",
        })

    def persist(self):
        self.persisted += 1


class _Source:
    def __init__(self, _network):
        pass

    async def fetch_topics(self, *, limit: int):
        assert limit == 20
        return [TopicSignal(
            title="Research scan evaluated 5 candidate relationships",
            summary="The scan evaluated five relationships and did not promote unsupported candidates.",
            source="agent-network:trading.cai.001",
            source_revision="trading-public-research-abc@1",
            evidence_refs=(
                "network-artifact:artifact-1@1:sha256:" + "a" * 64,
                "network-contract:contract-1",
                "trading:report:report-1",
            ),
            observed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        )]


def test_worker_creates_one_non_public_draft_per_stable_source(monkeypatch, tmp_path):
    import wechat_traffic_agent.trading_draft_worker as module

    monkeypatch.setattr(module, "_network_client", lambda _root: object())
    monkeypatch.setattr(module, "TradingResearchNetworkSource", _Source)
    runtime = _Runtime()
    worker = TradingResearchDraftWorker(runtime=runtime, state_root=tmp_path)
    now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)

    first = asyncio.run(worker.run_once(now=now))
    second = asyncio.run(worker.run_once(now=now))

    assert first.status == "DRAFT_CREATED"
    assert second.status == "ALREADY_DRAFTED"
    assert first.source_fingerprint == second.source_fingerprint
    assert first.artifact_id == second.artifact_id
    assert runtime.calls == 1
    assert runtime.persisted == 1
    record = worker.inbox.load(first.source_fingerprint)
    assert record.artifact.public_safe is False
    assert record.artifact.slug == "why-a-research-scan-can-reject-every-trading-candidate"
    assert any(ref.ref_id.startswith("network-artifact:") for ref in record.artifact.source_refs)
