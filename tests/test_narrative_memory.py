from wechat_traffic_agent.narrative_memory import build_story_so_far


class _FakeMind:
    def __init__(self, projection):
        self._projection = projection

    def public_projection(self):
        return self._projection


def test_story_contains_positions_resolutions_and_themes():
    mind = _FakeMind({
        "positions": {
            "open": [{"proposition": "ETH 破 4000 前维持多头", "falsifier": "周线收于 3800 下方"}],
            "recent_resolutions": [{"proposition": "SOL 反弹到 210", "status": "CONFIRMED",
                                    "resolution": "第五日达标"}],
        },
        "interest_terrain": [{"name": "山寨资金流", "decayed_salience": 0.9}],
    })
    recent = [{"published_at": "2026-10-12T03:00:00+00:00", "thesis": "SOL 走势判断",
               "observation_provenance": {"genre": "quick_take"}}]
    story = build_story_so_far(mind, recent)
    assert "ETH 破 4000 前维持多头" in story
    assert "周线收于 3800 下方" in story
    assert "CONFIRMED" in story
    assert "山寨资金流" in story
    assert "[quick_take]" in story


def test_budget_cap_at_600_chars():
    mind = _FakeMind({"positions": {"open": [
        {"proposition": "很长的立场" * 1000, "falsifier": "x"}]}})
    story = build_story_so_far(mind, [])
    assert len(story) <= 600


def test_degrades_to_empty_without_inputs():
    assert build_story_so_far(None, []) == ""


def test_broken_mind_projection_degrades_gracefully():
    class _Broken:
        def public_projection(self):
            raise RuntimeError("boom")
    recent = [{"published_at": "2026-10-12T03:00:00+00:00", "thesis": "旧帖",
               "research_provenance": {"learning_cycle": "c1"}}]
    story = build_story_so_far(_Broken(), recent)
    assert "旧帖" in story
    assert "[research]" in story
