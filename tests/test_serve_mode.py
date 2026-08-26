"""--serve mode connects the shared mailbox entry (WeCom operator bridge)."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.agents import load_agent_manifest
from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.runtime import AgentRuntime

import wechat_traffic_agent.__main__ as main_module


def _serve_runtime(tmp_path) -> AgentRuntime:
    manifest = load_agent_manifest(Path(__file__).parents[1] / "agent.yaml")
    backend = FixtureAgentBackend("ok")
    return AgentRuntime.bootstrap(
        tmp_path / "state", agent_manifest=manifest, agent_backend=backend
    )


def test_serve_flag_makes_audience_and_topics_optional() -> None:
    args = main_module.build_parser().parse_args(["--serve"])
    assert args.serve is True
    assert args.audience is None
    assert args.topics is None


def test_batch_mode_without_inputs_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit):
        main_module.main([])


def test_run_serve_starts_continuous_runner_without_tick_limit(
    tmp_path, monkeypatch
) -> None:
    captured: dict = {}
    runtime = _serve_runtime(tmp_path)

    class FakeRunner:
        def __init__(self, rt):
            self.runtime = rt

        def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="COMPLETED", stop_reason="fake")

    monkeypatch.setattr(main_module, "ContinuousRunner", FakeRunner)
    result = main_module._run_serve(runtime, argparse.Namespace(idle_seconds=0.0))
    assert result == 0
    assert captured["max_ticks"] is None
    assert captured["idle_seconds"] == 0.0
