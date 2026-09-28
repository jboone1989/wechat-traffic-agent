from __future__ import annotations

from wechat_traffic_agent.context_compiler import (
    compile_surface_metadata,
    extract_open_loops,
    infer_decision_frame,
)


def test_decision_frame_classifies_runtime_and_chat_surfaces():
    runtime = infer_decision_frame(
        "SELECT_EVIDENCE: choose the strongest source",
        surface="agent_runtime",
    )
    owner = infer_decision_frame(
        "继续把这个目标做完",
        surface="owner_chat",
        owner_mode=True,
    )
    public = infer_decision_frame(
        "你今天在做什么？",
        surface="public_chat",
    )

    assert runtime["decision_type"] == "SELECTION"
    assert runtime["task_prefix"] == "SELECT_EVIDENCE"
    assert owner["decision_type"] == "OWNER_DIALOGUE"
    assert owner["owner_authority_scope"] is True
    assert public["decision_type"] == "PUBLIC_DIALOGUE"


def test_open_loops_keep_unfinished_work_and_avoid_goal_mission_duplication():
    loops = extract_open_loops(
        missions=[{
            "mission_id": "mission-x",
            "goal_id": "goal-x",
            "description": "Ship the experiment",
            "status": "ACTIVE",
            "roadmap": {
                "steps": [
                    {"step_id": "done", "title": "Done", "status": "COMPLETED"},
                    {"step_id": "next", "title": "Verify outcome", "status": "PLANNED"},
                ]
            },
        }],
        goals=[
            {"goal_id": "goal-x", "description": "Duplicate mission goal", "status": "ACTIVE"},
            {"goal_id": "goal-y", "description": "Independent goal", "status": "ACTIVE"},
        ],
        inquiries=[
            {"inquiry_id": "inq-open", "question": "What changed?", "status": "OPEN"},
            {"inquiry_id": "inq-done", "question": "Old question", "status": "RESOLVED"},
        ],
        pending_plan_count=2,
    )

    assert [row["kind"] for row in loops] == [
        "MISSION",
        "GOAL",
        "INQUIRY",
        "PENDING_PLAN_QUEUE",
    ]
    assert loops[0]["unfinished_steps"] == [{
        "step_id": "next",
        "title": "Verify outcome",
        "status": "PLANNED",
    }]
    assert loops[1]["id"] == "goal-y"


def test_context_metadata_records_selection_provenance_and_budget():
    metadata = compile_surface_metadata(
        prompt="CHECK_RESULT: verify the deployed behavior",
        surface="agent_runtime",
        sections={
            "current_state": {"status": "RUNNING"},
            "recent_experience": [{"outcome": "ok"}],
        },
        inquiries=[{
            "inquiry_id": "inq-1",
            "question": "Did mobile behavior change?",
            "status": "OPEN",
        }],
        candidate_counts={"recent_experience": 9},
        downstream_budget_chars=60_000,
    )

    assert metadata["decision_frame"]["decision_type"] == "VERIFICATION"
    assert metadata["open_loops"][0]["id"] == "inq-1"
    assert (
        metadata["epistemic_contract"]["model_inference"]
        == "unverified_until_observed"
    )
    receipt = metadata["context_receipt"]
    assert receipt["candidate_counts"]["recent_experience"] == 9
    assert receipt["downstream_budget_chars"] == 60_000
    assert receipt["selected_sections"] == ["current_state", "recent_experience"]
    assert receipt["selected_chars_estimate"] > 0


def test_open_loops_drop_collapsed_or_empty_rows():
    loops = extract_open_loops(
        inquiries=[
            {"summary": "4 fields omitted"},
            {"inquiry_id": "", "question": "", "status": "OPEN"},
            {"inquiry_id": "inq-real", "question": "What changed?", "status": "OPEN"},
        ],
        goals=[{"goal_id": "", "description": "", "status": "ACTIVE"}],
        missions=[{"mission_id": "", "description": "", "status": "ACTIVE"}],
    )

    assert loops == [{
        "kind": "INQUIRY",
        "id": "inq-real",
        "description": "What changed?",
        "status": "OPEN",
    }]


def test_context_receipt_has_stable_identity_and_budget_utilization():
    first = compile_surface_metadata(
        prompt="VERIFY_RESULT: inspect evidence",
        surface="agent_runtime",
        sections={"state": {"x": 1}, "evidence": [{"id": "e-1"}]},
        candidate_counts={"evidence": 4},
        downstream_budget_chars=1000,
    )
    second = compile_surface_metadata(
        prompt="VERIFY_RESULT: inspect evidence",
        surface="agent_runtime",
        sections={"state": {"x": 1}, "evidence": [{"id": "e-1"}]},
        candidate_counts={"evidence": 4},
        downstream_budget_chars=1000,
    )

    receipt = first["context_receipt"]
    assert receipt["schema_version"] == "ferro-context-receipt.v2"
    assert receipt["receipt_id"].startswith("context-receipt:sha256:")
    assert receipt["receipt_id"] == second["context_receipt"]["receipt_id"]
    assert 0 < receipt["budget_utilization_estimate"] < 1
