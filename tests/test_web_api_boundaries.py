"""Independent web-api registration and short-function boundary contracts."""
import ast
from pathlib import Path
from types import SimpleNamespace

from starlette.routing import Route, WebSocketRoute

from wechat_traffic_agent.web_api.route_registry import (
    PUBLIC_ROUTES, OPERATOR_ROUTES, public_routes, operator_routes,
)
from wechat_traffic_agent.web_api.public_views import PublicViews

ROOT = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"


async def _endpoint(request):
    return None


def test_public_and_operator_route_surfaces_are_separate():
    assert len(PUBLIC_ROUTES) == 26
    assert len(OPERATOR_ROUTES) == 51
    assert all(path.startswith("/operator") for path, _, _ in OPERATOR_ROUTES)
    assert not any(path.startswith("/operator") for path, _, _ in PUBLIC_ROUTES)
    names = {name for _, name, _ in PUBLIC_ROUTES}
    public = public_routes({name: _endpoint for name in names})
    assert len(public) == 26
    assert all(isinstance(route, Route) for route in public)
    assert {route.path for route in public} == {path for path, _, _ in PUBLIC_ROUTES}
    private = operator_routes({name: _endpoint for _, name, _ in OPERATOR_ROUTES})
    assert sum(isinstance(route, WebSocketRoute) for route in private) == 2
    assert all(route.path.startswith("/operator") for route in private)
    # GET/POST intentionally share some operator paths; match by method as well.\n    identities = {\n        (route.path, tuple(sorted(route.methods)) if isinstance(route, Route) else ("WEBSOCKET",))\n        for route in public + private\n    }\n    assert len(identities) == len(public + private)


def test_public_views_initialize_without_operator_or_browser_state():
    service = SimpleNamespace(state_root=Path("/nonexistent"), wakeful=None)
    views = PublicViews(service, telemetry=None, circuit=None, engagement_enabled=False)
    import asyncio
    from starlette.requests import Request
    request = Request({"type": "http", "method": "GET", "path": "/brain",
                       "headers": [], "query_string": b"view=live"})
    response = asyncio.run(views.brain_lab_status(request))
    assert response.status_code == 503
    assert b"brain_lab_unavailable" in response.body


def test_new_content_goal_decision_helpers_are_under_100_lines():
    module = ast.parse((ROOT / "content_goal_loop.py").read_text(encoding="utf-8"))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef)
               and n.name == "ContinuousContentLoop")
    names = (
        "_cycle_inputs", "_sync_strategy_learning", "_owner_cycle_context",
        "_load_world_feed", "_settle_world_predictions", "_collect_world_evidence",
        "_bind_cycle_experiment", "_choose_mind_plan", "_focused_cycle_plan",
        "_select_stable_attractor", "_dream_cycle_plan", "_decide_stable_attractor",
        "_stable_attractor_plan", "_preconcept_plan", "_neural_taste_plan",
        "_build_mission_discovery_query", "_failed_mission_discovery",
        "_apply_mission_discovery_plan", "_mission_discovery_plan",
    )
    lengths = []
    for name in names:
        method = next(n for n in cls.body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
        length = method.end_lineno - method.lineno + 1
        lengths.append(length)
        assert length <= 100, f"{name} has {length} lines"
    assert sum(length <= 50 for length in lengths) > len(lengths) / 2


def test_original_mega_functions_have_shrunk_not_moved_to_a_new_mega_function():
    for file, clsname, methodname, max_len in [
        ("content_goal_loop.py", "ContinuousContentLoop", "_run", 1900),
        ("live_agent.py", "LiveAgentService", "_app", 2400),
    ]:
        module = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == clsname)
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef if methodname == "_app" else ast.AsyncFunctionDef)
                      and n.name == methodname)
        assert method.end_lineno - method.lineno + 1 < max_len
    web = ROOT / "web_api"
    for file in web.glob("*.py"):
        module = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    file.name, node.name, node.end_lineno - node.lineno + 1,
                )
