from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from threading import Event, Timer
import time
from types import SimpleNamespace

from wechat_traffic_agent.live_agent import LiveAgentService
from wechat_traffic_agent.network_service import ContentNetworkService
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


class SlowX:
    def __init__(self) -> None:
        self.cancelled = Event()

    async def observe(self, *, now):
        try:
            await asyncio.sleep(30)
            return {"new_mentions": 0}
        finally:
            self.cancelled.set()


class Wakeful:
    def emit_x_mentions(self, **kwargs):
        raise AssertionError("cancelled task must not emit X evidence")
def test_long_async_task_is_cancelled_after_shutdown_request():
    stop = {"requested": False}
    slow = SlowX()
    runner = WakefulContentTaskRunner(
        x_loop=slow,
        wakeful=Wakeful(),
        stop_requested=lambda: stop["requested"],
    )
    timer = Timer(0.12, lambda: stop.__setitem__("requested", True))
    timer.start()
    started = time.monotonic()
    try:
        result = runner.run(
            "CHECK_X",
            now=datetime.now(timezone.utc),
        )
    finally:
        timer.cancel()

    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert result == {
        "status": "SHUTDOWN_CHECKPOINT",
        "task": "CHECK_X",
        "reason": "SIGTERM_OR_SIGINT",
    }
    assert slow.cancelled.wait(timeout=0.5)
def test_live_agent_request_stop_is_non_blocking():
    service = object.__new__(LiveAgentService)
    server = SimpleNamespace(should_exit=False)
    service._server = server
    service._thread = None

    service.request_stop()

    assert server.should_exit is True


def test_network_request_stop_is_non_blocking():
    service = object.__new__(ContentNetworkService)
    server = SimpleNamespace(should_exit=False)
    service._server = server
    service._loop = None
    service._join_task = None

    service.request_stop()

    assert server.should_exit is True


def test_shutdown_checkpoint_summary_is_bounded():
    entrypoint = Path(__file__).resolve().parents[1] / "scripts" / "run_content_agent.py"
    spec = importlib.util.spec_from_file_location("run_content_agent_shutdown_test", entrypoint)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = module._shutdown_task_summary(
        "CHECK_X",
        {
            "status": "OK",
            "task": "CHECK_X",
            "x_social_observation": {"huge": "x" * 100_000},
        },
    )

    assert summary["status"] == "SHUTDOWN_CHECKPOINT"
    assert summary["interrupted_status"] == "OK"
    assert summary["task"] == "CHECK_X"
    assert "x_social_observation" in summary["interrupted_keys"]
    assert "huge" not in str(summary)
