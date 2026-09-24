from __future__ import annotations

import json

from wechat_traffic_agent.live_agent import (
    _bounded_owner_prompt_json,
    _compact_owner_prompt_value,
)


def test_owner_prompt_compaction_bounds_growing_state_without_losing_front_context():
    value = {
        "agent": {"status": "OK", "goal": "promote LivingRuntime Remote"},
        "external_missions": [
            {
                "mission_id": "mission-x-growth",
                "description": "Grow X followers",
                "status": "ACTIVE",
            }
        ],
        "world_history": [
            {
                "event_id": f"event-{index}",
                "text": "evidence-" + ("x" * 4000),
                "nested": {"rows": list(range(100))},
            }
            for index in range(200)
        ],
        "huge_tail": "z" * 100_000,
    }

    compact = _compact_owner_prompt_value(value)
    assert compact["agent"]["goal"] == "promote LivingRuntime Remote"
    assert compact["external_missions"][0]["status"] == "ACTIVE"
    assert compact["world_history"][-1]["_omitted_items"] == 192

    encoded = _bounded_owner_prompt_json(value, max_chars=3000)
    assert len(encoded) <= 3000
    parsed = json.loads(encoded)
    assert isinstance(parsed, dict)
    assert "promote LivingRuntime Remote" in encoded


def test_owner_prompt_compaction_marks_omissions_instead_of_silently_dropping():
    value = {
        "rows": [{"index": index, "payload": "p" * 2000} for index in range(25)]
    }
    compact = _compact_owner_prompt_value(value, list_limit=4, string_limit=100)
    assert compact["rows"][-1] == {"_omitted_items": 21}
    assert compact["rows"][0]["payload"].endswith("…[truncated]")
