from wechat_traffic_agent.operator_memory import memory_promotion_allowed_for_turn


def test_recall_only_owner_turn_does_not_promote_memory():
    assert memory_promotion_allowed_for_turn(
        "Please recall our earlier conversation and tell me what I asked then."
    ) is False


def test_explicit_memory_request_overrides_recall_filter():
    assert memory_promotion_allowed_for_turn(
        "Please remember this for future conversations and save it to memory."
    ) is True


def test_normal_project_update_can_still_promote_memory():
    assert memory_promotion_allowed_for_turn(
        "The production deployment now uses the new runtime release."
    ) is True
