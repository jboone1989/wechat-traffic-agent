import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from test_square_cognition import episode, service, write
from wechat_traffic_agent.square_website_syndication import SquareWebsiteSyndication
from wechat_traffic_agent.orchestration.owner import OwnerTaskMethods
from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal


def enable(root):
    write(root / "square_editorial/editorial_preferences.json", {
        "production_mode": "COGNITION_LED", "syndicate_website": True})


def test_published_cycle_adapts_once_through_real_review(tmp_path):
    folder, learning, cognition = episode(tmp_path)
    enable(tmp_path)
    write(folder / "mind_plan.json", {})
    run, calls = service(tmp_path)
    sync = SquareWebsiteSyndication(tmp_path, service=run)
    published = {"status": "PUBLISHED", "publication": {"url": "https://site.example/article", "artifact_id": "article-1"}}
    assert sync.enqueue(folder=folder, result=published)["status"] == "PENDING"
    assert sync.enqueue(folder=folder, result=published)["status"] == "PENDING"
    result = asyncio.run(sync.run_once(runtime=object()))
    assert result["status"] == "SQUARE_EXPRESSION_READY"
    assert result["publication_confirmed"] is False
    assert calls == ["compose", "semantic_review", "collect"]
    assert not any(t["status"] == "PENDING" for t in sync.records.all("square_preparation"))
    assert sync.enqueue(folder=folder, result=published)["draft_id"] == result["draft_id"]
    assert asyncio.run(sync.run_once(runtime=object())) is None
    assert published["status"] == "PUBLISHED"


@pytest.mark.parametrize("status", ["DRAFT_READY", "ERROR", "COGNITION_ONLY"])
def test_unpublished_cycle_never_enters_square_queue(tmp_path, status):
    folder, _, _ = episode(tmp_path)
    enable(tmp_path)
    sync = SquareWebsiteSyndication(tmp_path)
    assert sync.enqueue(folder=folder, result={"status": status}) is None
    assert not any(t["status"] == "PENDING" for t in sync.records.all("square_preparation"))


def test_failure_retries_preparation_without_republishing_website(tmp_path):
    folder, _, _ = episode(tmp_path)
    enable(tmp_path)
    write(folder / "mind_plan.json", {})
    class Failing:
        async def prepare(self, **kwargs):
            raise TimeoutError("model timeout")
    sync = SquareWebsiteSyndication(tmp_path, service=Failing())
    published = {"status": "PUBLISHED", "publication": {"url": "https://site.example/article", "artifact_id": "article-1"}}
    sync.enqueue(folder=folder, result=published)
    now = datetime.now(timezone.utc)
    result = asyncio.run(sync.run_once(runtime=object(), now=now))
    assert result["status"] == "PREPARATION_FAILED"
    assert published["status"] == "PUBLISHED"
    assert asyncio.run(sync.run_once(runtime=object(), now=now + timedelta(seconds=1))) is None
    run, calls = service(tmp_path)
    sync.service = run
    assert asyncio.run(sync.run_once(runtime=object(), now=now + timedelta(seconds=301)))["status"] == "SQUARE_EXPRESSION_READY"
    assert calls == ["compose", "semantic_review", "collect"]


def test_unsuitable_research_records_skip_not_empty_post(tmp_path):
    folder, _, _ = episode(tmp_path)
    enable(tmp_path)
    write(folder / "mind_plan.json", {})
    run, calls = service(tmp_path, action="NOT_FOR_SQUARE")
    sync = SquareWebsiteSyndication(tmp_path, service=run)
    sync.enqueue(folder=folder, result={"status": "PUBLISHED", "publication": {"artifact_id": "article-1"}})
    result = asyncio.run(sync.run_once(runtime=object()))
    assert result["status"] == "COGNITION_ONLY"
    assert calls == ["compose"]
    assert not sync.records.all("binance_square")


def test_daily_goal_is_retained_with_square_syndication(tmp_path, monkeypatch):
    enable(tmp_path)
    expected = {"goal_id": "daily", "description": "20 original posts"}
    monkeypatch.setattr(DailyPostingGoal, "owner_goal", lambda self, now: expected)
    runner = SimpleNamespace(state_root=tmp_path)
    assert OwnerTaskMethods._active_owner_goal_context(runner) == expected
