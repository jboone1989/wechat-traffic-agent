"""LLM contract over-length prose is clamped before validation, not fatal."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.content_goal_contracts import EvidenceSelection
from wechat_traffic_agent.evidence_choice_service import (
    EvidenceChoicePolicies, EvidenceChoiceRequest, EvidenceChoiceService,
)
from wechat_traffic_agent.learning_research import ResearchFocus, _research_focus_contract
from wechat_traffic_agent.memory_service import MemoryService


def _selection(gap: str, reader_question: str) -> EvidenceSelection:
    return EvidenceSelection(
        title="Evidence-grounded comparison", evidence_id="actual-1",
        topic="agent-memory", related_ids=(),
        reader_question=reader_question, gap=gap,
    )


def test_evidence_selection_clamps_overlong_prose_fields():
    selection = _selection("x" * 600, "What observation establishes the changed outcome? " + "y" * 500)
    assert len(selection.gap) == 500
    assert len(selection.reader_question) == 400


def test_task_research_focus_clamps_why_interested_including_dynamic_contract():
    focus = ResearchFocus(
        question="ETH 反弹是否已有新增持仓配合？这决定接力判断。",
        search_query="eth open interest rebound analysis",
        why_interested="z" * 700,
    )
    assert len(focus.why_interested) == 600
    task_model = _research_focus_contract({})
    task_focus = task_model(
        question="ETH 反弹是否已有新增持仓配合？这决定接力判断。",
        search_query="eth open interest rebound analysis",
        why_interested="z" * 700,
        approach="EXPLAIN",
        completion_criterion="Explain how open interest interacts with the rebound.",
        method_change="",
    )
    assert len(task_focus.why_interested) == 600


def test_prefix_clamp(tmp_path: Path):
    from conftest import RetrievalModelsDouble

    service = MemoryService(tmp_path, models=RetrievalModelsDouble())
    text = "The worker uses a checkpoint to restore state after a restart."
    service.ingest_source({"evidence_id": "src-long-checkpoint", "title": "Checkpoint code",
                           "summary": text, "evidence_text": text,
                           "url": "https://example.com/code"},
                          topic="restart")
    with service.store._connect() as db:
        node_id = db.execute(
            "SELECT node_id FROM memory_nodes WHERE node_type='evidence'"
        ).fetchone()["node_id"]

    class LongPrefixRuntime:
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
            return json.dumps({"prefixes": [
                {"ref": chunk["ref"], "prefix": "前" * 300} for chunk in data["chunks"]
            ]})

    asyncio.run(service.contextualize(LongPrefixRuntime(), [node_id]))
    after = service.read(node_id)
    assert len(after["context"]) == 240
    assert after["text"] == text


def test_string_too_long_error_takes_deterministic_fallback(tmp_path: Path):
    async def too_long_selection(*args, **kwargs):
        raise ValueError(
            "1 validation error for EvidenceSelection\ngap\n"
            "  String should have at most 500 characters [type=string_too_long, "
            "input_value='too long', input_type=str]")

    fallback = _selection("The controlled comparison is not yet available.",
                          "What observation establishes the changed outcome?")
    events = []
    service = EvidenceChoiceService(
        runtime=object(), explorer=object(), inbox=object(),
        activity=lambda *args, **kwargs: events.append(args),
        policies=EvidenceChoicePolicies(
            structured=too_long_selection,
            decision_context=lambda **kw: {},
            fallback=lambda *args, **kw: fallback,
            repair_related=lambda choice, site: choice,
            market_companion=lambda selected, rows: None,
            market_instruction=lambda selected: "",
            candidate_available=lambda inbox, row: True,
            title_key=lambda value: value.casefold(),
            scope_instruction="AI and finance",
        ),
    )
    source = {"evidence_id": "actual-1", "title": "Observational evidence",
              "summary": "A real source", "source_kind": "web-search"}
    request = EvidenceChoiceRequest(
        folder=tmp_path, cycle="cycle-1", now=datetime.now(timezone.utc),
        snapshot={"articles": [], "topics": {}}, evidence=[source],
        evidence_pool={"items": [source]}, previous_titles=[],
        traffic_payload={}, mind_plan={}, x_mission_context=None,
        learning_focus=None, required_topic=None, effective_topic=None,
        goal_active=False, explorer_runtime=None, topic_context=[],
    )
    result = asyncio.run(service.choose(request, receipt_runtime=object()))
    assert result.selection_mode == "DETERMINISTIC_EVIDENCE_FALLBACK"
    assert result.selected is source
    assert (tmp_path / "selection_fallback.json").is_file()
