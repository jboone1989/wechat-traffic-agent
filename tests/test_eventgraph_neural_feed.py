"""Tests for the EventGraph -> Ferro nervous system boundary."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from wechat_traffic_agent.eventgraph_neural_feed import EventGraphChangeSensor
from wechat_traffic_agent.eventgraph_research import EventGraphResearch


def _response(request):
    if request.url.path.endswith("/snapshot"):
        return httpx.Response(200, json={
            "schemaVersion": 1, "cursor": "initial", "items": [],
        })
    if request.url.path.endswith("/changes"):
        cursor = request.url.params["cursor"]
        if cursor == "initial":
            return httpx.Response(200, json={
                "schemaVersion": 1, "cursor": "after-1", "hasMore": False,
                "changes": [
                    {"op": "upsert", "changedAt": "2026-10-26T00:00:00Z",
                     "item": {"id": "article-1", "title": "Supply chain news",
                              "summary": "New data", "category": "Finance"}},
                    {"op": "remove", "id": "article-old"},
                ],
            })
        return httpx.Response(200, json={
            "schemaVersion": 1, "cursor": cursor,
            "changes": [], "hasMore": False,
        })
    raise AssertionError(request.url)


def test_baseline_then_deliver_exactly_one_new_event(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(_response))
    sensor = EventGraphChangeSensor(
        state_root=tmp_path, site_url="https://eventgraph.example",
        client=client,
    )
    delivered = []
    assert sensor.poll(lambda *row: delivered.append(row))["status"] == "BASELINED"
    assert delivered == []
    assert sensor.poll(lambda *row: delivered.append(row))["delivered"] == 1
    item, timestamp, ref = delivered[0]
    assert item["id"] == "article-1"
    assert timestamp == "2026-10-26T00:00:00Z"
    assert sensor.read_item(ref)["item"]["title"] == "Supply chain news"
    assert sensor.poll(lambda *row: delivered.append(row))["delivered"] == 0
    assert len(delivered) == 1


def test_failed_neural_delivery_replays_unadvanced_cursor(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(_response))
    sensor = EventGraphChangeSensor(
        state_root=tmp_path, site_url="https://eventgraph.example",
        client=client,
    )
    sensor.poll(lambda *row: None)
    with pytest.raises(RuntimeError):
        sensor.poll(lambda *row: (_ for _ in ()).throw(RuntimeError("neural unavailable")))
    received = []
    sensor.poll(lambda *row: received.append(row))
    assert len(received) == 1


def test_rebuilt_ledger_resyncs_without_fake_wakes(tmp_path):
    def responder(request):
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(200, json={"schemaVersion": 1, "cursor": "new-epoch"})
        return httpx.Response(409)
    sensor = EventGraphChangeSensor(
        state_root=tmp_path, site_url="https://eventgraph.example",
        client=httpx.Client(transport=httpx.MockTransport(responder)),
    )
    sensor._save_cursor("old-epoch")
    delivered = []
    assert sensor.poll(lambda *row: delivered.append(row))["status"] == "RESYNCED"
    assert delivered == []
    assert sensor._cursor() == "new-epoch"


class _MockMCP:
    def __init__(self, uri):
        assert uri == "https://eventgraph.example/api/mcp"
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            structured_content={"tool": name, "facts": [{"ref": "source-123"}]},
            is_error=False,
        )


class _FakeCognition:
    def __init__(self):
        self.calls = []

    async def cognitive_probe(self, prompt, max_tokens):
        self.calls.append(prompt)
        if "DEEP_RESEARCH_PLAN:" in prompt:
            return '{"queries":["export controls silicon 2026"],"questions":["Why?"]}'
        return "研究备忘录：现有证据不足以断言因果。来源 source-123。"


def test_mcp_research_uses_existing_tools_and_deduplicates(tmp_path):
    sensor = EventGraphChangeSensor(
        state_root=tmp_path, site_url="https://eventgraph.example",
        client=httpx.Client(transport=httpx.MockTransport(_response)),
    )
    ref = sensor.record_item(
        {"id": "article-5", "title": "Silicon export restrictions",
         "summary": "Public statement"}, "2026-10-26T00:00:00Z",
    )
    mcp = _MockMCP("https://eventgraph.example/api/mcp")
    researcher = EventGraphResearch(
        state_root=tmp_path, site_url="https://eventgraph.example",
        client_factory=lambda uri: mcp,
    )
    cortex = _FakeCognition()
    first = asyncio.run(researcher.run(evidence_ref=ref, runtime=cortex))
    assert first["status"] == "RESEARCHED"
    assert len(cortex.calls) == 2
    assert [name for name, _ in mcp.calls] == [
        "livingruntime_search", "livingruntime_get_hot_topics",
        "livingruntime_get_daily", "livingruntime_search",
    ]
    assert asyncio.run(researcher.run(evidence_ref=ref, runtime=cortex))["status"] == "ALREADY_RESEARCHED"
    assert len(cortex.calls) == 2


def test_neural_gate_requires_actual_matching_cognition_trigger():
    from wechat_traffic_agent.ferro_neural_runtime import FerroNeuralRuntime

    class Core:
        def __init__(self):
            self.events = []
            self.triggers = ()

        def emit(self, event, *, enqueue):
            self.events.append(event)
            return SimpleNamespace(channel="opportunity", threshold_crossed=False)

        def snapshot(self):
            return SimpleNamespace(cognition_triggers=self.triggers)

    core = Core()
    fake = SimpleNamespace(core=core)
    result = FerroNeuralRuntime.emit_eventgraph_item(
        fake, item={"id": "article-99", "category": "Finance"},
        changed_at="now", evidence_ref="eventgraph-event:abc",
    )
    assert not result["cognition_triggered"]
    core.triggers = (SimpleNamespace(source_event_id=result["event_id"]),)
    again = FerroNeuralRuntime.emit_eventgraph_item(
        fake, item={"id": "article-99", "category": "Finance"},
        changed_at="now", evidence_ref="eventgraph-event:abc",
    )
    assert again["cognition_triggered"]
    assert core.events[-1].kind == "MATERIAL_MARKET_EVENT"
