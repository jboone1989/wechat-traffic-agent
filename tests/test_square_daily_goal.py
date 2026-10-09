import json
from datetime import datetime, timezone
from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore


def put(path, data):
    if path.parent.name == "drafts":
        SquareDraftStore(path.parents[2]).save({**data, "draft_id": path.stem})
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_square_target_counts_only_success_not_website_attempts(tmp_path):
    goal = DailyPostingGoal(tmp_path)
    put(goal.config_path, {"enabled": True, "minimum_original_posts": 100,
        "target_channel": "binance_square", "timezone": "Asia/Shanghai",
        "activated_at": "2026-10-09T00:00:00+00:00", "notifications_enabled": False})
    base = {"post_id": "123", "url": "https://www.binance.com/square/post/123",
            "published_at": "2026-10-09T01:00:00+00:00"}
    drafts = tmp_path / "square_editorial/drafts"
    put(drafts / "a.json", {**base, "status": "SUCCESS"})
    put(drafts / "duplicate.json", {**base, "status": "SUCCESS"})
    put(drafts / "pending.json", {**base, "post_id": "124", "status": "REVIEWED"})
    put(drafts / "unverified.json", {**base, "post_id": "125", "status": "UNVERIFIED"})
    put(drafts / "wrongurl.json", {**base, "url": "https://x.com/a/status/123", "status": "SUCCESS"})
    put(tmp_path / "publication/events/website.json", {"status": "PUBLISHED"})
    now = datetime(2026, 10, 9, 11, tzinfo=timezone.utc)
    status = goal.status(now)
    assert (status["minimum"], status["published"], status["remaining"]) == (100, 1, 99)
    assert status["metric"] == "VERIFIED_SQUARE_POSTS"
    assert goal.owner_goal(now)["daily_posting_contract"]["target_channel"] == "binance_square"
    assert "官网分发尝试、X帖、草稿及失败不抵扣" in goal.owner_goal(now)["description"]


def test_shanghai_day_boundary(tmp_path):
    goal = DailyPostingGoal(tmp_path)
    put(goal.config_path, {"enabled": True, "minimum_original_posts": 100,
        "target_channel": "binance_square", "timezone": "Asia/Shanghai",
        "activated_at": "2026-10-09T00:00:00+00:00"})
    put(tmp_path / "square_editorial/drafts/a.json", {"status": "SUCCESS",
        "post_id": "123", "url": "https://www.binance.com/square/post/123",
        "published_at": "2026-10-09T16:00:00+00:00"})
    assert goal.status(datetime(2026,10,9,15,tzinfo=timezone.utc))["published"] == 0
    assert goal.status(datetime(2026,10,9,16,tzinfo=timezone.utc))["published"] == 1
