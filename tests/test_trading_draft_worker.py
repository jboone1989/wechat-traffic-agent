import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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
    requests = 0

    def __init__(self, _network):
        pass

    async def fetch_topics(self, *, limit: int):
        assert limit == 20
        type(self).requests += 1
        return [TopicSignal(
            title="Research scan evaluated 5 candidate relationships",
            summary="The scan evaluated five relationships and did not promote unsupported candidates.",
            source="agent-network:trading.cai.001",
            source_revision="trading-public-research-abc@1",
            evidence_refs=(
                "network-artifact:artifact-1@1:sha256:" + "a" * 64,
                f"network-contract:contract-{type(self).requests}",
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


class _RepairRuntime(_Runtime):
    def __init__(self, failure, *, repair_succeeds=True):
        super().__init__()
        self.failure = failure
        self.repair_succeeds = repair_succeeds
        self.tasks = []

    async def cognitive_probe(self, task: str, max_tokens: int) -> str:
        self.tasks.append(task)
        payload = json.loads(await super().cognitive_probe(task, max_tokens))
        if self.calls == 1 or not self.repair_succeeds:
            if self.failure in {"summary", "both"}:
                payload["summary"] = "x" * 501
            if self.failure in {"extra", "both"}:
                payload["sections"][0]["note"] = None
        return json.dumps(payload)


@pytest.mark.parametrize("failure", ["summary", "extra", "both"])
def test_schema_repair_creates_private_draft_and_restart_skips_model(
    monkeypatch, tmp_path, failure
):
    import wechat_traffic_agent.trading_draft_worker as module

    monkeypatch.setattr(module, "_network_client", lambda _root: object())
    monkeypatch.setattr(module, "TradingResearchNetworkSource", _Source)
    runtime = _RepairRuntime(failure)
    worker = TradingResearchDraftWorker(runtime=runtime, state_root=tmp_path)
    first = asyncio.run(worker.run_once())
    assert first.status == "DRAFT_CREATED"
    assert runtime.calls == 2
    assert runtime.persisted == 1
    repair = runtime.tasks[1]
    assert "only repair attempt" in repair
    assert "500 characters" in repair
    assert "ONLY heading and paragraphs" in repair
    if failure in {"summary", "both"}:
        assert '"loc":["summary"]' in repair
        assert "string_too_long" in repair
    if failure in {"extra", "both"}:
        assert '"loc":["sections",0,"note"]' in repair
        assert "extra_forbidden" in repair
    record = worker.inbox.load(first.source_fingerprint)
    assert record.artifact.public_safe is False
    assert len(record.artifact.summary) <= 500
    assert set(record.artifact.sections[0].model_dump()) == {"heading", "paragraphs"}
    assert len(list(worker.inbox.drafts_dir.glob("*.json"))) == 1
    assert any(ref.ref_id.startswith("network-contract:") for ref in record.artifact.source_refs)

    restarted_runtime = _Runtime()
    restarted = TradingResearchDraftWorker(runtime=restarted_runtime, state_root=tmp_path)
    second = asyncio.run(restarted.run_once())
    assert second.status == "ALREADY_DRAFTED"
    assert second.source_fingerprint == first.source_fingerprint
    assert restarted_runtime.calls == 0
    assert restarted_runtime.persisted == 0
    assert len(restarted.inbox.list_records()) == 1


@pytest.mark.parametrize("failure", ["summary", "extra", "both"])
def test_schema_repair_failure_stops_after_two_calls_without_draft(
    monkeypatch, tmp_path, failure
):
    import wechat_traffic_agent.trading_draft_worker as module

    monkeypatch.setattr(module, "_network_client", lambda _root: object())
    monkeypatch.setattr(module, "TradingResearchNetworkSource", _Source)
    runtime = _RepairRuntime(failure, repair_succeeds=False)
    worker = TradingResearchDraftWorker(runtime=runtime, state_root=tmp_path)
    with pytest.raises(ValidationError):
        asyncio.run(worker.run_once())
    assert runtime.calls == 2
    assert runtime.persisted == 0
    assert worker.inbox.list_records() == ()
    assert not list(tmp_path.rglob("*.json"))


def test_outbound_trading_worker_uses_all_configured_seeds(monkeypatch, tmp_path):
    from wechat_traffic_agent import trading_draft_worker as module

    monkeypatch.setenv(
        "CONTENT_NETWORK_SEED_URLS",
        "https://seed-a.example/a2a,https://seed-b.example/a2a",
    )
    network = module._network_client(tmp_path)

    assert network.seeds == [
        "https://seed-a.example/a2a",
        "https://seed-b.example/a2a",
    ]
