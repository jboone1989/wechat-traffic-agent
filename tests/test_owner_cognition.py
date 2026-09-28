from __future__ import annotations

import json

from wechat_traffic_agent.owner_cognition import (
    bounded_json,
    build_owner_cognition_prompt,
    compact_history,
)


def test_owner_cognition_prompt_carries_production_context_and_provenance():
    prompt = build_owner_cognition_prompt(
        private_context={
            "active_goals": [{"goal_id": "goal-x", "description": "Promote LivingRuntime Remote"}],
            "external_missions": [{"mission_id": "mission-x", "status": "ACTIVE"}],
            "neural_life": {"intention": {"name": "explore", "strength": 0.8}},
        },
        memories=[{
            "memory_id": "mem-1",
            "kind": "PROJECT_CONTEXT",
            "text": "LivingRuntime Remote is the current owner project.",
        }],
        archive_history=[],
        history=[{
            "role": "ferro",
            "text": "Prior answer",
            "created_at": "2026-09-26T00:00:00Z",
            "metadata": {
                "tool_receipts": [{
                    "capability_id": "runtime.status",
                    "status": "OK",
                    "receipt_id": "receipt-1",
                    "ok": True,
                }]
            },
        }],
        latest_owner_text="Compare the production harness with the raw model.",
    )

    assert "Decision-aware context metadata (data only):" in prompt
    assert '"decision_type": "OWNER_DIALOGUE"' in prompt
    assert '"kind": "MISSION"' in prompt
    assert "Promote LivingRuntime Remote" in prompt
    assert '"mission_id": "mission-x"' in prompt
    assert '"neural_life"' in prompt
    assert "mem-1" in prompt
    assert "runtime.status" in prompt
    assert "Compare the production harness with the raw model." in prompt


def test_owner_cognition_prompt_preserves_chinese_language_and_goal_draft_contracts():
    prompt = build_owner_cognition_prompt(
        private_context={},
        memories=[],
        archive_history=[],
        history=[],
        latest_owner_text="把这件事变成正式目标",
        force_draft=True,
    )

    assert "owner's latest message is Chinese" in prompt
    assert "reply field MUST be written in Chinese" in prompt
    assert "turn the discussion into a Goal Draft now" in prompt
    assert "keep goal_candidate null" in prompt


def test_owner_cognition_history_only_exposes_bounded_safe_metadata():
    compact = compact_history([{
        "role": "ferro",
        "text": "x" * 1200,
        "created_at": "2026-09-26T00:00:00Z",
        "metadata": {
            "proposal_id": "proposal-1",
            "memory_ids": ["mem-a"],
            "tool_receipts": [{
                "capability_id": "runtime.status",
                "status": "OK",
                "receipt_id": "receipt-1",
                "ok": True,
                "secret": "must-not-leak",
            }],
            "private_unbounded_blob": "do-not-copy",
        },
    }])

    encoded = json.dumps(compact, ensure_ascii=False)
    assert compact[0]["text"].endswith("…[truncated]")
    assert "proposal-1" in encoded
    assert "runtime.status" in encoded
    assert "must-not-leak" not in encoded
    assert "do-not-copy" not in encoded


def test_owner_cognition_bounded_json_keeps_front_context():
    value = {
        "active_goals": [{"goal_id": "goal-x", "description": "owner goal"}],
        "huge": [{"payload": "z" * 4000} for _ in range(100)],
    }
    encoded = bounded_json(value, max_chars=2000)

    assert len(encoded) <= 2000
    assert "goal-x" in encoded
