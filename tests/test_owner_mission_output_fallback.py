from __future__ import annotations

from wechat_traffic_agent.content_goal_loop import (
    _allow_unconstrained_output_fallback,
)


def test_owner_mission_never_falls_back_to_unrelated_output():
    assert _allow_unconstrained_output_fallback(
        goal_active=True,
        goal_origin="EXTERNAL_MISSION",
        owner_goal={"goal_id": "goal-owner"},
    ) is False


def test_external_mission_without_owner_payload_still_fails_closed():
    assert _allow_unconstrained_output_fallback(
        goal_active=True,
        goal_origin="EXTERNAL_MISSION",
        owner_goal=None,
    ) is False


def test_internal_goal_can_still_use_unconstrained_output_fallback():
    assert _allow_unconstrained_output_fallback(
        goal_active=True,
        goal_origin="INTEREST",
        owner_goal=None,
    ) is True


def test_no_goal_never_uses_goal_miss_fallback():
    assert _allow_unconstrained_output_fallback(
        goal_active=False,
        goal_origin="",
        owner_goal=None,
    ) is False
