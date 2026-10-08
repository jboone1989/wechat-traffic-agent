"""Regression contracts for the split content-task coordinator."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent"


def test_recovery_cycle_uses_new_cycle_identity_for_resource_admission():
    class Loop:
        def __init__(self):
            self.calls = []

        def preflight(self, *, now, cycle_id=None):
            self.calls.append(cycle_id)
            return {
                "status": "ALREADY_PROCESSED" if cycle_id is None else "READY",
                "cycle": cycle_id or "v2-bootstrap-123",
                "result_path": "/tmp/unneeded-for-overridden-recovery.json",
            }

    runner = WakefulContentTaskRunner(continuous_loop=Loop())
    runner._content_recovery = lambda **kwargs: {
        "status": "READY", "recovery_cycle": "v2-recovery-123-1-456",
    }
    rows = {}
    result = runner._prepare_content_cycle(rows, NOW, None, None, False)
    assert result is not None
    preflight, directed, cycle, recovery, followup = result
    assert directed == cycle == "v2-recovery-123-1-456"
    assert preflight["cycle"] == cycle
    assert runner.continuous_loop.calls == [None, directed]
    assert recovery["status"] == "READY"
    assert followup is None


def test_search_console_is_not_called_without_cognition_pipeline():
    calls = []
    class GSC:
        def collect(self, *, now):
            calls.append(now)
            return {"status": "OK"}
    class Site:
        async def run_once(self, *, now):
            return {"status": "OK"}
    runner = WakefulContentTaskRunner(
        continuous_loop=None, gsc_collector=GSC(), web_worker=Site(),
    )
    runner._runtime_tick = lambda now: (SimpleNamespace(generation_ready=True), {})
    runner._active_owner_goal_context = lambda **kwargs: None
    runner._run_async = lambda awaitable: _resolve(awaitable)
    result = runner._content(NOW, focus_topic=None)
    assert result["web_editorial"]["status"] == "OK"
    assert "google_search_console" not in result
    assert calls == []


def _resolve(awaitable):
    import asyncio
    return asyncio.run(awaitable)


def test_new_content_and_website_methods_stay_under_100_lines():
    # This is an architectural limit for refactored paths, not a claim
    # that all 140+ legacy oversized functions have already been refactored.
    for file in (
        ROOT / "orchestration/content.py",
        ROOT / "orchestration/resources.py",
        ROOT / "website/automatic_website.py",
    ):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        method_lengths = []
        for cls in [x for x in tree.body if isinstance(x, ast.ClassDef)]:
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([method.lineno] + [d.lineno for d in method.decorator_list])
                    method_lengths.append(method.end_lineno - start + 1)
                    assert method_lengths[-1] <= 100, (
                        f"{file.name}:{method.lineno} {cls.name}.{method.name} "
                        f"is {method.end_lineno - start + 1} lines"
                    )
        assert sum(length <= 50 for length in method_lengths) > len(method_lengths) / 2
