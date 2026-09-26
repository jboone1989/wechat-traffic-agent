from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from wechat_traffic_agent.content_loop import ContinuousContentLoop, ReceiptRuntime
from wechat_traffic_agent.livingruntime_chatgpt import LivingRuntimeChatGPTPendingError
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


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
        asyncio.run(wrapped.cognitive_probe("CHOOSE: original host state", 100))
        raise AssertionError("expected pending")
    except LivingRuntimeChatGPTPendingError as exc:
        assert exc.request_id == "llmreq_durable_test"

    marker = json.loads((receipt_root / "0.started").read_text(encoding="utf-8"))
    assert marker["status"] == "WAITING_FOR_CHATGPT"
    assert marker["request_id"] == "llmreq_durable_test"
    assert runtime.calls[0] == "CHOOSE: original host state"

    # A restarted content-cycle runtime starts at phase index zero and must
    # re-enter the same durable request rather than create a new model phase.
    resumed = ReceiptRuntime(runtime, receipt_root)
    result = asyncio.run(
        resumed.cognitive_probe("CHOOSE: newer host state", 100)
    )

    assert result == "completed-response"
    assert runtime.calls[1] == "CHOOSE: newer host state"
    receipt = json.loads((receipt_root / "0.json").read_text(encoding="utf-8"))
    assert receipt["durable_request_id"] == "llmreq_durable_test"
    assert receipt["phase_signature"]
    assert receipt["resume_input_changed"] is True
    assert not (receipt_root / "0.started").exists()

    replayed = ReceiptRuntime(runtime, receipt_root)
    assert asyncio.run(
        replayed.cognitive_probe("CHOOSE: even newer host state", 100)
    ) == "completed-response"
    assert len(runtime.calls) == 2


def test_completed_durable_receipt_does_not_bleed_into_next_semantic_phase(tmp_path):
    runtime = _PendingThenCompleteRuntime()
    receipt_root = tmp_path / "cycle" / "model_calls"

    first = ReceiptRuntime(runtime, receipt_root)
    try:
        asyncio.run(first.cognitive_probe("PREDICT: old host context", 100))
        raise AssertionError("expected pending")
    except LivingRuntimeChatGPTPendingError:
        pass

    resumed = ReceiptRuntime(runtime, receipt_root)
    assert asyncio.run(
        resumed.cognitive_probe("PREDICT: newer host context", 100)
    ) == "completed-response"

    async def expression(task: str, *, max_tokens: int):
        runtime.calls.append(task)
        return "expression-response"

    runtime.cognitive_probe = expression
    shifted = ReceiptRuntime(runtime, receipt_root)
    result = asyncio.run(
        shifted.cognitive_probe(
            "DECIDE_EXPRESSION: evaluate the completed cognition",
            100,
        )
    )

    assert result == "expression-response"
    assert runtime.calls[-1].startswith("DECIDE_EXPRESSION:")
    first_receipt = json.loads((receipt_root / "0.json").read_text())
    second_receipt = json.loads((receipt_root / "1.json").read_text())
    assert first_receipt["phase_signature"] != second_receipt["phase_signature"]
    assert second_receipt["response"] == "expression-response"


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


def test_preflight_accepts_explicit_standard_scheduled_cycle_id(tmp_path):
    loop = object.__new__(ContinuousContentLoop)
    loop.root = tmp_path / "continuous"
    loop.interval_hours = 6

    result = loop.preflight(
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
        cycle_id="v2-1790402400",
    )

    assert result["cycle"] == "v2-1790402400"
    assert result["status"] == "READY"


def test_content_cycle_resume_clears_waiting_marker_and_writes_terminal_result(tmp_path):
    loop = object.__new__(ContinuousContentLoop)
    loop.root = tmp_path / "continuous"
    cycle = "v2-test-2000"
    calls = 0

    def preflight(*, now=None, cycle_id=None):
        result_path = loop.root / "cycles" / cycle / "result.json"
        return {
            "cycle": cycle,
            "status": "ALREADY_PROCESSED" if result_path.exists() else "READY",
            "result_path": str(result_path),
        }

    async def run_once(folder, cycle_id, now):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LivingRuntimeChatGPTPendingError("llmreq_resume_same_cycle")
        return {"status": "COGNITION_ONLY", "cycle": cycle}

    loop.preflight = preflight
    loop._run = run_once
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)

    first = asyncio.run(loop.run_once(now=now, cycle_id=cycle))
    second = asyncio.run(loop.run_once(now=now, cycle_id=cycle))

    folder = loop.root / "cycles" / cycle
    assert first["status"] == "WAITING_FOR_CHATGPT"
    assert second == {"status": "COGNITION_ONLY", "cycle": cycle}
    assert not (folder / "waiting_for_chatgpt.json").exists()
    assert json.loads((folder / "result.json").read_text()) == second


def test_wakeful_runner_prefers_newest_unfinished_chatgpt_cycle(tmp_path):
    runner = object.__new__(WakefulContentTaskRunner)
    runner.state_root = tmp_path
    cycles = tmp_path / "content" / "continuous" / "cycles"
    older = cycles / "v2-recovery-1000-1-2000"
    newer = cycles / "v2-recovery-1000-2-3000"
    done = cycles / "v2-recovery-1000-3-4000"
    for folder, request_id in (
        (older, "llmreq_old"),
        (newer, "llmreq_new"),
        (done, "llmreq_done"),
    ):
        folder.mkdir(parents=True)
        (folder / "waiting_for_chatgpt.json").write_text(
            json.dumps({
                "status": "WAITING_FOR_CHATGPT",
                "cycle": folder.name,
                "request_id": request_id,
                "resumable": True,
            }),
            encoding="utf-8",
        )
    (done / "result.json").write_text(
        json.dumps({"status": "COGNITION_ONLY", "cycle": done.name}),
        encoding="utf-8",
    )
    older_marker = older / "waiting_for_chatgpt.json"
    newer_marker = newer / "waiting_for_chatgpt.json"
    older_marker.touch()
    import os
    os.utime(older_marker, (1000, 1000))
    os.utime(newer_marker, (2000, 2000))

    assert runner._pending_chatgpt_cycle() == {
        "cycle": newer.name,
        "request_id": "llmreq_new",
    }
