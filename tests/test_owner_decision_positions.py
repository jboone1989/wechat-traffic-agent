from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode

def test_decision_mind_context_includes_open_positions():
    current_mind = {
        "mind_tick": 7,
        "interests": [{"name": "sol", "salience": 1.0, "preference": 0.6, "familiarity": 0.5}],
        "open_inquiries": [], "beliefs": [],
        "positions": {"open": [{"position_id": "pos-1", "topic": "sol",
                                "proposition": "流入持续", "falsifier": "流入回落",
                                "age_days": 2.0}],
                      "recent_resolutions": []},
    }
    ctx = OwnerAgentEpisode._decision_mind_context(current_mind)
    assert ctx["positions"]["open"][0]["position_id"] == "pos-1"
    assert ctx["positions"]["open"][0]["age_days"] == 2.0


def test_decision_mind_context_without_positions_is_empty():
    ctx = OwnerAgentEpisode._decision_mind_context({"interests": [], "open_inquiries": [], "beliefs": []})
    assert ctx["positions"] == {"open": [], "recent_resolutions": []}
