from __future__ import annotations

from datetime import datetime, timezone

from wechat_traffic_agent.decision_context import (
    build_decision_context,
    candidate,
    plan_information_needs,
)
from wechat_traffic_agent.memory_system import CanonicalMemoryStore


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def _frame(decision_type: str = "PLANNING") -> dict:
    return {
        "decision_type": decision_type,
        "decision_question": "test",
        "surface": "agent_runtime",
    }


def test_information_needs_change_when_internal_pressure_changes() -> None:
    normal = plan_information_needs(
        prompt="Answer the current question.",
        decision_frame=_frame("GENERAL_COGNITION"),
        owner_scope=None,
        missions=[],
        resource_pressure="NORMAL",
    )
    pressured = plan_information_needs(
        prompt="Answer the current question.",
        decision_frame=_frame("GENERAL_COGNITION"),
        owner_scope=None,
        missions=[],
        resource_pressure="WATCH",
    )

    assert "internal_pressure" not in {item.need_id for item in normal}
    assert "internal_pressure" in {item.need_id for item in pressured}


def test_high_importance_irrelevant_fact_does_not_create_relevance() -> None:
    rows = [
        candidate(
            context_id="goal:deploy",
            source="goal",
            kind="GOAL",
            payload={"goal_id": "deploy", "description": "repair Public Chat production"},
            text="repair Public Chat production",
            current=True,
            importance=0.95,
            confidence=1.0,
            refs=["deploy"],
        ),
        candidate(
            context_id="belief:irrelevant",
            source="belief",
            kind="BELIEF",
            payload={"proposition": "ancient crypto rates pattern"},
            text="ancient crypto rates pattern",
            importance=1.0,
            confidence=1.0,
        ),
        candidate(
            context_id="experience:failure",
            source="experience",
            kind="OUTCOME",
            payload={"summary": "Public Chat deploy failed because backend was unavailable"},
            text="Public Chat deploy failed because backend was unavailable",
            importance=0.9,
            confidence=1.0,
            refs=["deploy"],
        ),
    ]

    result = build_decision_context(
        prompt="PLAN: repair Public Chat production after the backend failure",
        decision_frame=_frame(),
        owner_scope=None,
        missions=[],
        local_candidates=rows,
        state_root=None,
        now=NOW,
    )

    assert "experience:failure" in result["selected_ids"]
    assert "belief:irrelevant" not in result["selected_ids"]
    rejected = {row["context_id"]: row for row in result["rejected_candidates"]}
    assert rejected["belief:irrelevant"]["reason"] == "below_decision_relevance_floor"


def test_memory_router_recalls_old_decision_changing_memory(tmp_path) -> None:
    store = CanonicalMemoryStore(tmp_path)
    store.append_memory({
        "memory_id": "m:old-public-chat-failure",
        "memory_type": "EPISODIC",
        "kind": "EPISODE",
        "text": (
            "Public Chat production failed because the synchronous OpenAI backend "
            "was unavailable; repair the backend before another deployment."
        ),
        "created_at": "2026-01-10T00:00:00Z",
        "status": "ACTIVE",
        "importance": 0.97,
        "confidence": 1.0,
        "source_type": "RUNTIME_EXPERIENCE",
        "source_ref": "outcome:public-chat-failure",
        "access_scope": "PRIVATE_OWNER",
        "evidence_refs": ["outcome:public-chat-failure"],
    })

    result = build_decision_context(
        prompt="PLAN: repair Public Chat production backend before deployment",
        decision_frame=_frame(),
        owner_scope=None,
        missions=[],
        local_candidates=[],
        state_root=tmp_path,
        now=NOW,
    )

    assert result["memory_router"]["enabled"] is True
    assert result["memory_router"]["recalled_rows_across_needs"] > 0
    assert "memory:m:old-public-chat-failure" in result["selected_ids"]
    selected = next(
        row
        for row in result["selected_facts"]
        if row["context_id"] == "memory:m:old-public-chat-failure"
    )
    assert any(
        reason.startswith("memory_router_rank:")
        for reason in selected["selection"]["reasons"]
    )


def test_dependency_expansion_recovers_linked_low_lexical_evidence() -> None:
    rows = [
        candidate(
            context_id="mission:mx",
            source="mission",
            kind="MISSION",
            payload={
                "mission_id": "mission-x-long",
                "goal_id": "goal-x-long",
                "description": "ship durable remote execution",
            },
            text="ship durable remote execution",
            current=True,
            importance=0.98,
            confidence=1.0,
            refs=["mission-x-long", "goal-x-long"],
        ),
        candidate(
            context_id="experience:opaque",
            source="experience",
            kind="OUTCOME",
            payload={"summary": "receipt 942 complete"},
            text="receipt 942 complete",
            importance=0.8,
            confidence=1.0,
            refs=["mission-x-long"],
        ),
    ]

    result = build_decision_context(
        prompt="PLAN: choose the next action for durable remote execution",
        decision_frame=_frame(),
        owner_scope={"goal_id": "goal-x-long", "mission_id": "mission-x-long"},
        missions=[{"mission_id": "mission-x-long", "goal_id": "goal-x-long", "description": "ship durable remote execution"}],
        local_candidates=rows,
        state_root=None,
        now=NOW,
    )

    assert "mission:mx" in result["selected_ids"]
    assert "experience:opaque" in result["selected_ids"]
    opaque = next(
        row for row in result["selected_facts"]
        if row["context_id"] == "experience:opaque"
    )
    assert any(
        reason.startswith("dependency_expansion:")
        for reason in opaque["selection"]["reasons"]
    )


def test_internal_state_is_selected_by_pressure_not_by_recency() -> None:
    rows = [
        candidate(
            context_id="internal:pressure",
            source="internal",
            kind="NEURAL_INTERNAL_STATE",
            payload={"threat": 0.92, "body": {"compute": 0.11}},
            text="threat 0.92 compute 0.11",
            current=True,
            importance=0.9,
            confidence=1.0,
        )
    ]

    normal = build_decision_context(
        prompt="Answer an unrelated general question.",
        decision_frame=_frame("GENERAL_COGNITION"),
        owner_scope=None,
        missions=[],
        local_candidates=rows,
        state_root=None,
        now=NOW,
        resource_pressure="NORMAL",
    )
    pressured = build_decision_context(
        prompt="Answer an unrelated general question.",
        decision_frame=_frame("GENERAL_COGNITION"),
        owner_scope=None,
        missions=[],
        local_candidates=rows,
        state_root=None,
        now=NOW,
        resource_pressure="WATCH",
    )

    assert "internal:pressure" not in normal["selected_ids"]
    assert "internal:pressure" in pressured["selected_ids"]
    assert "internal_pressure" in {
        need["need_id"] for need in pressured["information_needs"]
    }


def test_marginal_need_coverage_suppresses_redundant_same_need_noise() -> None:
    rows = [
        candidate(
            context_id="goal:owner",
            source="goal",
            kind="GOAL",
            payload={
                "goal_id": "goal-owner",
                "description": "Grow LivingRuntime Remote on X",
            },
            text="Grow LivingRuntime Remote on X",
            current=True,
            importance=0.98,
            confidence=1.0,
            refs=["goal-owner"],
        ),
        candidate(
            context_id="experience:critical",
            source="experience",
            kind="OUTCOME",
            payload={
                "goal_id": "goal-owner",
                "summary": "The current growth strategy failed three times.",
            },
            text="LivingRuntime Remote X growth strategy failed three times.",
            importance=0.98,
            confidence=1.0,
            refs=["goal-owner"],
        ),
    ]
    rows.extend(
        candidate(
            context_id=f"experience:noise:{index}",
            source="experience",
            kind="OUTCOME",
            payload={
                "goal_id": f"other-goal-{index}",
                "summary": "An unrelated growth strategy outcome was discussed.",
            },
            text=(
                "Unrelated X growth strategy outcome failed or succeeded; "
                f"other-goal-{index}"
            ),
            importance=0.95,
            confidence=1.0,
            refs=[f"other-goal-{index}"],
        )
        for index in range(30)
    )

    result = build_decision_context(
        prompt="SELECT: choose what to do next for LivingRuntime Remote X growth",
        decision_frame=_frame("SELECTION"),
        owner_scope={
            "goal_id": "goal-owner",
            "description": "Grow LivingRuntime Remote on X",
        },
        missions=[],
        local_candidates=rows,
        state_root=None,
        now=NOW,
        budget_chars=8_000,
        max_facts=20,
    )

    assert "goal:owner" in result["selected_ids"]
    assert "experience:critical" in result["selected_ids"]
    assert not any(
        context_id.startswith("experience:noise:")
        for context_id in result["selected_ids"]
    )
    assert result["selected_count"] <= 3
    assert "objective" in result["need_coverage"]["covered"]
    assert "past_outcomes" in result["need_coverage"]["covered"]
