"""Enforce functional import boundaries without starting the Ferro service."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
from wechat_traffic_agent.editorial.fingerprint import digest
from wechat_traffic_agent.editorial.routing import _route_for

PKG = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"


def test_article_store_can_be_imported_without_cognition_website_or_browser():
    code = f"""
import sys
sys.path.insert(0, {str(PKG.parent)!r})
from wechat_traffic_agent.editorial.article_repository import ArticleRepository
assert ArticleRepository.__module__ == 'wechat_traffic_agent.editorial.article_repository'
for name in ('wechat_traffic_agent.channels.web', 'wechat_traffic_agent.website.web_editorial',
             'wechat_traffic_agent.wakeful_tasks', 'wechat_traffic_agent.live_agent',
             'wechat_traffic_agent.platforms.x.x_social', 'agent_runtime'):
    assert name not in sys.modules, name
"""
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr


def test_editorial_implementation_has_no_web_platform_or_runtime_import():
    blocked = (
        "wechat_traffic_agent.channels", "wechat_traffic_agent.website",
        "wechat_traffic_agent.platforms", "wechat_traffic_agent.wakeful",
        "wechat_traffic_agent.live_agent", "agent_runtime",
    )
    for source in (PKG / "editorial").glob("*.py"):
        if source.name == "content_publication_store.py":  # legacy import alias
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
        assert not [
            item for item in modules
            if any(item == prefix or item.startswith(prefix + ".") for prefix in blocked)
        ], source.name


def test_route_and_fingerprint_are_shared_contracts_not_copied():
    from wechat_traffic_agent.channels.web import _route_for as web_route
    from wechat_traffic_agent.web_editorial import digest as web_digest
    assert web_route is _route_for
    assert web_digest is digest


def test_wakeful_host_has_only_coordination_methods_not_domain_handlers():
    expected = {
        "content": ["_content", "_content_recovery", "_content_mission_followup"],
        "social": ["_run_social_stage", "_reconcile_x_publication_backlog"],
        "owner": ["_owner_agent_control", "_active_owner_goal_context"],
        "neural": ["_neural_play_impl", "_neural_interoception"],
        "resources": ["_resource", "_evolution_deliberation"],
        "observation": ["_observe", "_outcomes"],
    }
    assert "run" in WakefulContentTaskRunner.__dict__
    for feature, methods in expected.items():
        for method in methods:
            assert method not in WakefulContentTaskRunner.__dict__, method
            fn = getattr(WakefulContentTaskRunner, method)
            assert fn.__module__ == f"wechat_traffic_agent.orchestration.{feature}"


def test_wakeful_dispatcher_remains_bounded():
    lines = (PKG / "wakeful_tasks.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 900  # no silent reintroduction of the old 3,940-line class


def test_neural_capability_can_run_with_an_injected_fake_runtime():
    from datetime import datetime, timezone
    from wechat_traffic_agent.orchestration.neural import NeuralTaskMethods

    class FakeNeuralPort:
        def run_neural_dream(self, now):
            return {"status": "OK", "memory_consolidated": 1}

        def run_neural_transfer(self, now, *, training_steps):
            assert training_steps == 12
            return {"status": "OK", "transfer_count": 2}

    handler = NeuralTaskMethods()
    handler.wakeful = FakeNeuralPort()
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert handler._neural_dream(now) == {
        "task": "NEURAL_DREAM", "status": "OK", "memory_consolidated": 1,
    }
    assert handler._neural_transfer(now) == {
        "task": "NEURAL_TRANSFER", "status": "OK", "transfer_count": 2,
    }
