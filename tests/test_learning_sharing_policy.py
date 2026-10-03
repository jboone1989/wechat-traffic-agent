import asyncio
import json
import pytest
from wechat_traffic_agent.learning_expression_quality import learning_required_for_sharing
from wechat_traffic_agent.x_browser import XBrowserPublisher
from wechat_traffic_agent.x_social import XAutonomyLoop
from test_x_browser import _draft, _Transport, NOW
from test_x_owner_execution import cycle_context
from test_x_social import _ModelRuntime, _Runtime


def enable_policy(root):
    path = root / "content/learning/sharing_policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"require_learning_discovery": True}))
    return path


@pytest.mark.parametrize("fmt", ["SHORT_POST", "LONG_POST", "ARTICLE"])
def test_learning_first_owner_policy_blocks_legacy_native_drafts(tmp_path, fmt):
    enable_policy(tmp_path)
    folder = _draft(tmp_path, fmt=fmt)
    if fmt == "ARTICLE":
        path = folder / "social_expression_drafts.json"
        payload = json.loads(path.read_text())
        payload["drafts"]["x"]["title"] = "Legacy article title"
        path.write_text(json.dumps(payload))
    transport = _Transport()
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=transport,
        enabled=True, writes_enabled=True).after_content_cycle(cycle=folder.name, now=NOW))
    assert result["status"] == "HELD_BY_LEARNING_QUALITY"
    assert result["reason"] == "NO_VERIFIED_LEARNING_RECORD"
    assert transport.calls == []


@pytest.mark.parametrize("surface", ["X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"])
def test_learning_first_policy_retires_legacy_owner_drafts_before_model_or_browser(tmp_path, monkeypatch, surface):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    enable_policy(tmp_path)
    folder, result = cycle_context(tmp_path)
    path = folder / "owner_publication_request.json"
    request = json.loads(path.read_text())
    request["decision"]["publication_surface"] = surface
    path.write_text(json.dumps(request))
    model = _ModelRuntime([])
    runtime = _Runtime()
    transport = _Transport()
    loop = XAutonomyLoop(runtime=runtime, model_runtime=model, state_root=tmp_path,
        base_url="", browser_publish_transport=transport)
    outcome = asyncio.run(loop.after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "HELD_BY_LEARNING_QUALITY"
    assert outcome["reason"] == "NO_VERIFIED_LEARNING_RECORD"
    assert model.tasks == [] and runtime.calls == [] and transport.calls == []


def test_enabling_policy_preserves_completed_publication_receipts(tmp_path):
    folder = _draft(tmp_path)
    transport = _Transport()
    publisher = XBrowserPublisher(state_root=tmp_path, transport=transport, enabled=True, writes_enabled=True)
    assert asyncio.run(publisher.after_content_cycle(cycle=folder.name, now=NOW))["status"] == "POSTED"
    enable_policy(tmp_path)
    assert asyncio.run(publisher.after_content_cycle(cycle=folder.name, now=NOW))["status"] == "ALREADY_POSTED"
    assert len(transport.calls) == 1


def test_corrupted_owner_policy_fails_closed(tmp_path):
    path = enable_policy(tmp_path)
    path.write_text("{broken")
    assert learning_required_for_sharing(tmp_path) is True
