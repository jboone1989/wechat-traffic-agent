import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from wechat_traffic_agent.content_loop import _put
from wechat_traffic_agent.decision_context import build_ferro_decision_context
from wechat_traffic_agent.learning_application import (
    KnowledgeApplication, assess_application, commit_application, application_feedback,
)
from wechat_traffic_agent.learning_research import (
    ResearchFocus, choose_research_focus, investigate_learning, research_memory,
)

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
OLD = {
    "evidence_id": "old-source", "url": "https://example.org/old",
    "summary": "The worker writes an invocation ID before executing an external operation.",
}
NEW = {
    "evidence_id": "new-source", "url": "https://example.org/new-case",
    "summary": "The second worker persists its invocation ID only after the external operation, so a crash can repeat that operation.",
}
QUESTION = "Does another worker persist invocation identity before the external operation?"
QUERY = "second worker invocation identity persistence implementation"
CORRECTION = "Durable invocation identity is a requirement, not a guarantee that every worker implements it."
NEXT = "second worker source code fix persist invocation before side effect"


def seed(state):
    _put(state / "content/learning/research.json", {
        "inquiries": {}, "history": [{
            "cycle": "original", "topic": "worker-reliability", "observed_at": NOW.isoformat(),
            "facts": [{"evidence_id": OLD["evidence_id"], "quote": OLD["summary"],
                       "observation": "The first worker saves invocation identity before execution."}],
            "source_evidence_ids": [OLD["evidence_id"]], "source_urls": [OLD["url"]],
        }],
    })


def focus(memory):
    return ResearchFocus(
        question=QUESTION, search_query=QUERY,
        why_interested="Apply the saved restart lesson to a previously unseen worker implementation.",
        application=KnowledgeApplication(memory_ref=next(iter(memory["knowledge_catalog"])),
            prediction="The second worker will persist invocation identity before the external operation.",
            falsifier="Its implementation persists invocation identity only after the external operation.",
            confidence=0.8),
    )


class Runtime:
    def __init__(self, status="REFUTED", invented_ref=False):
        self.calls = []
        self.status = status
        self.invented_ref = invented_ref

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        data = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])[0]
        if task.startswith("PLAN_LEARNING:"):
            memory = {key: value for key, value in data.items() if key != "topic"}
            value = focus(memory).model_dump(mode="json")
            if data.get("application_feedback"):
                assert data["application_feedback"][-1]["correction"] == CORRECTION
                value["search_query"] = NEXT
                value["application"]["confidence"] = 0.35
            return json.dumps(value)
        if task.startswith("ADVANCE_LEARNING:"):
            return json.dumps({
                "progress": "DEEPENED", "evidence_id": data["sources"][0]["evidence_id"],
                "learning_delta": "The newly studied worker saves identity too late to prevent repeated external operations.",
                "facts": [{"source_quote_id": next(iter(data["source_quotes"])),
                           "observation": "The second worker saves identity only after the side effect."}],
                "related_proposition": None, "next_question": QUESTION,
                "next_query": "generic worker checkpoint design overview",
                "rationale": "A concrete counterexample narrows the scope of the original implementation lesson.",
            })
        assert task.startswith("ASSESS_LEARNING_APPLICATION:")
        return json.dumps({
            "status": self.status,
            "source_quote_ids": ["invented"] if self.invented_ref else [next(iter(data["source_quotes"]))],
            "rationale": "The new worker source explicitly places persistence after the external operation.",
            "correction": CORRECTION if self.status == "REFUTED" else "",
            "next_question": "Where must the second worker move persistence to prevent repeated operations?",
            "next_query": NEXT,
        })


def test_saved_fact_is_precommitted_tested_and_corrects_next_cycle_after_restart(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path, "worker-reliability")
    runtime = Runtime()
    folder = tmp_path / "content/continuous/cycles/application-1"
    selected = asyncio.run(choose_research_focus(
        runtime=runtime, folder=folder, topic="worker-reliability", memory=memory))
    plan = json.loads((folder / "learning_application_plan.json").read_text())
    assert plan["knowledge"]["quote"] == OLD["summary"]
    assert plan["application"]["confidence"] == 0.8

    class Explorer:
        async def search_web_query(self, *, query, goal_topic, now):
            # The expected observation is durable BEFORE an acquisition starts.
            assert (folder / "learning_application_plan.json").exists()
            assert query == QUERY
            return {"items": [NEW], "failures": []}

    pool = asyncio.run(Explorer().search_web_query(query=selected.search_query,
        goal_topic="worker-reliability", now=NOW))
    _put(folder / "learning_initial_search.json", pool)
    result = asyncio.run(investigate_learning(
        runtime=runtime, explorer=Explorer(), folder=folder, state_root=tmp_path,
        topic="worker-reliability", focus=selected, memory=memory,
        evidence=[NEW], selected=NEW, now=NOW, search_rounds=0))
    assert result["application"]["assessment"]["status"] == "REFUTED"
    assert result["application"]["brier_score"] == pytest.approx(0.64)
    assert result["next_query"] == NEXT  # Outcome takes precedence over the generic follow-up.
    state = json.loads((tmp_path / "content/learning/research.json").read_text())
    assert state["inquiries"]["worker-reliability"]["search_query"] == NEXT
    restarted_memory = research_memory(None, tmp_path, "worker-reliability")
    assert restarted_memory["application_feedback"][-1]["correction"] == CORRECTION
    next_focus = asyncio.run(choose_research_focus(runtime=Runtime(), folder=folder.parent / "application-2",
        topic="worker-reliability", memory=restarted_memory))
    assert next_focus.search_query == NEXT
    assert next_focus.application.confidence == 0.35
    replay = asyncio.run(investigate_learning(runtime=Runtime(), explorer=Explorer(),
        folder=folder, state_root=tmp_path, topic="worker-reliability",
        focus=selected, memory=memory, evidence=[NEW], selected=NEW, now=NOW, search_rounds=0))
    assert replay == result
    application_state = json.loads((tmp_path / "content/learning/applications.json").read_text())
    assert len(application_state["history"]) == 1
    assert application_state["metrics"]["refuted"] == 1


@pytest.mark.parametrize("source", [
    OLD,
    {**OLD, "evidence_id": "new-id-same-url"},
    {**OLD, "evidence_id": "reposted", "url": "https://example.org/repost"},
])
def test_old_source_or_reposted_fact_cannot_count_as_transfer_success(tmp_path, source):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    commit_application(folder, focus(memory), memory)
    _put(folder / "learning_initial_search.json", {"items": [source]})
    runtime = Runtime(status="CONFIRMED")
    result = asyncio.run(assess_application(runtime=runtime, folder=folder,
        state_root=tmp_path, topic="worker-reliability"))
    assert result["assessment"]["status"] == "UNRESOLVED"
    assert result["brier_score"] is None
    assert runtime.calls == []
    metrics = json.loads((tmp_path / "content/learning/applications.json").read_text())["metrics"]
    assert metrics["tested"] == 0 and metrics["unresolved"] == 1


def test_evidence_acquired_before_prediction_cannot_resolve_it(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    _put(folder / "learning_initial_search.json", {"items": [NEW]})
    os.utime(folder / "learning_initial_search.json", (100, 100))
    commit_application(folder, focus(memory), memory)
    result = asyncio.run(assess_application(runtime=Runtime(status="CONFIRMED"),
        folder=folder, state_root=tmp_path, topic="worker-reliability"))
    assert result["assessment"]["status"] == "UNRESOLVED"
    assert result["excluded_sources"][0]["reason"] == "ACQUIRED_BEFORE_COMMIT"


def test_an_invented_quote_reference_is_rejected_and_not_scored(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    commit_application(folder, focus(memory), memory)
    _put(folder / "learning_initial_search.json", {"items": [NEW]})
    with pytest.raises(ValueError, match="source_quote_ids"):
        asyncio.run(assess_application(runtime=Runtime(invented_ref=True),
            folder=folder, state_root=tmp_path, topic="worker-reliability"))
    assert not (tmp_path / "content/learning/applications.json").exists()


def test_outcome_receipt_repairs_feedback_after_interrupted_writeback(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    commit_application(folder, focus(memory), memory)
    _put(folder / "learning_initial_search.json", {"items": [NEW]})
    result = asyncio.run(assess_application(runtime=Runtime(), folder=folder,
        state_root=tmp_path, topic="worker-reliability"))
    (tmp_path / "content/learning/applications.json").unlink()
    replay = asyncio.run(assess_application(runtime=Runtime(), folder=folder,
        state_root=tmp_path, topic="worker-reliability"))
    assert replay == result
    assert application_feedback(tmp_path)[0]["correction"] == CORRECTION


def test_operational_decision_retrieves_the_research_counterexample(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    commit_application(folder, focus(memory), memory)
    _put(folder / "learning_initial_search.json", {"items": [NEW]})
    asyncio.run(assess_application(runtime=Runtime(), folder=folder,
        state_root=tmp_path, topic="worker-reliability"))
    context = build_ferro_decision_context(
        prompt="PLAN: repair second worker invocation persistence after external operation",
        decision_frame={"purpose": "PLANNING", "target": "second worker invocation persistence",
                        "objective": "prevent repeated external operations"},
        owner_scope=None, mission_state={}, intents=[], projection={}, resources={},
        capabilities=[], capability_beliefs=[], relationship_rows=[], commitments=[],
        life_experience=[], wake={}, latest_plan=None, state_root=tmp_path,
        now=NOW, budget_chars=8000)
    assert "learning-application:cycle" in context["selected_ids"]
    payload = next(row["payload"] for row in context["selected_facts"]
                   if row["context_id"] == "learning-application:cycle")
    assert payload["status"] == "REFUTED" and payload["correction"] == CORRECTION


def test_recent_reflection_is_supplied_to_research_and_bound_to_execution(tmp_path):
    seed(tmp_path)
    path = tmp_path / "application/reflections/reflection.json"
    _put(path, {"observed_at": NOW.isoformat(),
                "reflection_text": "I repeated generic checkpoint searches; next I should inspect another worker implementation."})
    memory = research_memory(None, tmp_path)
    folder = tmp_path / "cycle"
    runtime = Runtime()
    asyncio.run(choose_research_focus(runtime=runtime, folder=folder,
        topic="worker-reliability", memory=memory))
    data = json.JSONDecoder().raw_decode(runtime.calls[0].split("\n", 1)[1])[0]
    assert "inspect another worker" in data["recent_reflections"][0]["reflection_text"]
    plan = json.loads((folder / "learning_application_plan.json").read_text())
    assert plan["reflection_refs"] == ["application/reflections/reflection.json"]
    assert "known_quotes" not in data  # Host lineage is not a duplicate model message.


def test_unknown_saved_fact_cannot_be_bound_by_the_model(tmp_path):
    seed(tmp_path)
    memory = research_memory(None, tmp_path)
    class InvalidRuntime(Runtime):
        async def cognitive_probe(self, *args, **kwargs):
            value = json.loads(await super().cognitive_probe(*args, **kwargs))
            value["application"]["memory_ref"] = "invented"
            return json.dumps(value)
    with pytest.raises(ValueError, match="memory_ref"):
        asyncio.run(choose_research_focus(runtime=InvalidRuntime(), folder=tmp_path/"cycle",
            topic="worker-reliability", memory=memory))
    assert not (tmp_path / "cycle/learning_application_plan.json").exists()
