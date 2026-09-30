from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.fast_decision import (
    FastDecisionError,
    FastDecisionResult,
    JevFastDecisionProvider,
)
from wechat_traffic_agent.wakeful_runtime import ContentWakefulRuntime


class _Response:
    is_error = False
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return _Response(self.payload)


def test_jev_fast_decision_uses_bounded_choice_and_persists_receipt(tmp_path):
    client = _Client({
        "model": "jev-test",
        "answers": {
            "next_action": {
                "choice": "CHECK_X",
                "confidence": 0.91,
                "probabilities": {
                    "CHECK_X": 0.72,
                    "CONTENT_COGNITION": 0.18,
                    "REST": 0.10,
                },
            }
        },
    })
    provider = JevFastDecisionProvider(
        api_key="test-key",
        state_root=tmp_path,
        client=client,
    )

    result = provider.choose(
        decision="next_action",
        state={"neural": {"intention": "REPRODUCE"}, "x_enabled": True},
        choices={
            "CHECK_X": "inspect X",
            "CONTENT_COGNITION": "escalate to deep reasoning",
            "REST": "do nothing",
        },
        instructions={"objective": "advance Ferro"},
    )

    assert result.provider == "JEV"
    assert result.choice == "CHECK_X"
    assert result.confidence == 0.91
    assert result.probabilities["CHECK_X"] == 0.72
    assert client.calls[0][1]["questions"]["next_action"]["type"] == "choice"
    assert client.calls[0][1]["state"]["x_enabled"] is True

    raw = (tmp_path / "application" / "fast_decisions.jsonl").read_text()
    receipt = json.loads(raw)
    assert receipt["choice"] == "CHECK_X"
    assert receipt["decision"] == "next_action"
    assert "state_sha256" in receipt
    assert "test-key" not in raw
    assert "REPRODUCE" not in raw


def test_jev_fast_decision_rejects_invalid_probability_contract(tmp_path):
    client = _Client({
        "model": "jev-test",
        "answers": {
            "next_action": {
                "choice": "CHECK_X",
                "confidence": 0.8,
                "probabilities": {
                    "CHECK_X": 0.4,
                    "REST": 0.4,
                },
            }
        },
    })
    provider = JevFastDecisionProvider(
        api_key="test-key",
        state_root=tmp_path,
        client=client,
    )

    with pytest.raises(FastDecisionError):
        provider.choose(
            decision="next_action",
            state={"x_enabled": True},
            choices={
                "CHECK_X": "inspect X",
                "CONTENT_COGNITION": "escalate",
                "REST": "do nothing",
            },
        )



def test_lif_state_flows_through_l1_into_real_scheduled_action(tmp_path, monkeypatch):
    now = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("CONTENT_X_ENABLED", "1")
    runtime = ContentWakefulRuntime(state_root=tmp_path)
    runtime.resource_regulator.pressure = lambda _now: "NORMAL"

    class _Provider:
        provider_name = "JEV"

        def __init__(self):
            self.state = None

        def choose(self, *, decision, state, choices, instructions=None):
            self.state = state
            assert decision == "next_action"
            assert "CHECK_X" in choices
            assert "CONTENT_COGNITION" in choices
            return FastDecisionResult(
                decision_id="fastdec_test",
                provider="JEV",
                model="jev-test",
                choice="CHECK_X",
                confidence=0.87,
                probabilities={
                    key: (0.82 if key == "CHECK_X" else 0.18 / (len(choices) - 1))
                    for key in choices
                },
                latency_ms=41,
                decided_at=now.isoformat(),
            )

    provider = _Provider()
    runtime._fast_decision_provider = provider

    decision = runtime.fast_decide_next_action(
        now=now,
        relevant_refs=("neural-intention:REPRODUCE",),
    )
    action = runtime.schedule_fast_decision_action(decision, now=now)

    assert provider.state["neural"]["drives"]
    assert provider.state["neural"]["affect"]
    assert provider.state["neural"]["intention"]
    assert provider.state["trigger_refs"] == ["neural-intention:REPRODUCE"]
    assert decision["selected_task"] == "CHECK_X"
    assert action["status"] == "SCHEDULED"
    wake = next(
        row
        for row in runtime.core.snapshot().pending_plans
        if row.wake_id == "fast-decision:fastdec_test"
    )
    assert wake.task == "CHECK_X"
    assert wake.relevant_refs == ("fast-decision:fastdec_test",)
    assert runtime.public_snapshot()["fast_decision"]["selected_task"] == "CHECK_X"



def test_jev_fast_decision_batches_independent_bounded_questions(tmp_path):
    client = _Client({
        "model": "jev-test",
        "answers": {
            "post_timing": {
                "choice": "POST_NOW",
                "confidence": 0.8,
                "probabilities": {"POST_NOW": 0.8, "HOLD": 0.2},
            },
            "x_format": {
                "choice": "SHORT_POST",
                "confidence": 0.7,
                "probabilities": {"SHORT_POST": 0.7, "LONG_POST": 0.3},
            },
        },
    })
    provider = JevFastDecisionProvider(
        api_key="test-key",
        state_root=tmp_path,
        client=client,
    )

    results = provider.choose_many(
        state={"mission": {"status": "ACTIVE"}},
        decisions={
            "post_timing": {"POST_NOW": "post", "HOLD": "hold"},
            "x_format": {"SHORT_POST": "short", "LONG_POST": "long"},
        },
    )

    assert results["post_timing"].choice == "POST_NOW"
    assert results["x_format"].choice == "SHORT_POST"
    assert len(client.calls) == 1
    assert set(client.calls[0][1]["questions"]) == {"post_timing", "x_format"}
    receipts = [
        json.loads(line)
        for line in (tmp_path / "application" / "fast_decisions.jsonl").read_text().splitlines()
    ]
    assert {row["decision"] for row in receipts} == {"post_timing", "x_format"}
