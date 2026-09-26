from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from wechat_traffic_agent.content_loop import ContinuousContentLoop, ReceiptRuntime
from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTPendingError


class _PendingThenCompleteRuntime:
    agent_manifest = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def cognitive_probe(self, task: str, *, max_tokens: int):
        self.calls.append(task)
        if len(self.calls) == 1:
            raise LivingRuntimeChatGPTPendingError("llmreq_durable_test")
        return "completed-response"


def test_receipt_runtime_resumes_same_durable_phase_after_chatgpt_pending(tmp_path):
    runtime = _PendingThenCompleteRuntime()
    receipt_root = tmp_path / "cycle" / "model_calls"
    wrapped = ReceiptRuntime(runtime, receipt_root)

    try:
        asyncio.run(wrapped.cognitive_probe("choose", 100))
        raise AssertionError("expected pending")
    except LivingRuntimeChatGPTPendingError as exc:
        assert exc.request_id == "llmreq_durable_test"

    marker = json.loads((receipt_root / "0.started").read_text(encoding="utf-8"))
    assert marker["status"] == "WAITING_FOR_CHATGPT"
    assert marker["request_id"] == "llmreq_durable_test"
    assert runtime.calls[0] == "choose"

    # A restarted content-cycle runtime starts at phase index zero and must
    # re-enter the same durable request rather than create a new model phase.
    resumed = ReceiptRuntime(runtime, receipt_root)
    result = asyncio.run(resumed.cognitive_probe("choose", 100))

    assert result == "completed-response"
    assert runtime.calls[1] == "choose"
    assert (receipt_root / "0.json").exists()
    assert not (receipt_root / "0.started").exists()


def test_content_cycle_pending_is_nonterminal_and_has_no_result_receipt(tmp_path):
    loop = object.__new__(ContinuousContentLoop)
    loop.root = tmp_path / "continuous"
    cycle = "v2-test-1000"

    def preflight(*, now=None, cycle_id=None):
        return {
            "cycle": cycle,
            "status": "READY",
            "result_path": str(loop.root / "cycles" / cycle / "result.json"),
        }

    async def run_pending(folder, cycle_id, now):
        raise LivingRuntimeChatGPTPendingError("llmreq_waiting")

    loop.preflight = preflight
    loop._run = run_pending

    result = asyncio.run(
        loop.run_once(
            now=datetime(2026, 9, 26, tzinfo=timezone.utc),
            cycle_id=cycle,
        )
    )

    result_path = loop.root / "cycles" / cycle / "result.json"
    waiting_path = loop.root / "cycles" / cycle / "waiting_for_chatgpt.json"
    assert result == {
        "schema_version": "content-cognition-wait.v1",
        "status": "WAITING_FOR_CHATGPT",
        "cycle": cycle,
        "request_id": "llmreq_waiting",
        "resumable": True,
    }
    assert not result_path.exists()
    assert json.loads(waiting_path.read_text(encoding="utf-8"))["resumable"] is True
