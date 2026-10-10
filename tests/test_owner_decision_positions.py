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


def test_decision_mind_context_filters_stale_junk_and_caps():
    open_rows = [
        {"position_id": f"pos-{i}", "topic": "sol", "proposition": f"p{i}",
         "falsifier": f"f{i}", "age_days": 1.0}
        for i in range(4)
    ]
    open_rows.append({"position_id": "pos-old", "topic": "sol", "proposition": "p-old",
                      "falsifier": "f-old", "age_days": 6.0})
    open_rows.append("not-a-mapping")
    open_rows.append({"position_id": "pos-none-age", "topic": "sol",
                      "proposition": "p-none", "falsifier": "f-none", "age_days": None})
    resolutions = [
        {"position_id": f"res-{i}", "topic": "sol", "status": "CONFIRMED",
         "resolution": f"r{i}"}
        for i in range(3)
    ]
    resolutions.append({"position_id": "res-none", "topic": "sol",
                        "status": "FALSIFIED", "resolution": None})
    current_mind = {
        "interests": [], "open_inquiries": [], "beliefs": [],
        "positions": {"open": open_rows, "recent_resolutions": resolutions},
    }
    ctx = OwnerAgentEpisode._decision_mind_context(current_mind)
    open_rows_out = ctx["positions"]["open"]
    assert len(open_rows_out) == 5
    assert [row["position_id"] for row in open_rows_out] == [
        "pos-0", "pos-1", "pos-2", "pos-3", "pos-none-age"]
    assert open_rows_out[4] == {"position_id": "pos-none-age", "topic": "sol",
                                "proposition": "p-none", "falsifier": "f-none"}
    resolutions_out = ctx["positions"]["recent_resolutions"]
    assert len(resolutions_out) == 3
    assert resolutions_out == [
        {"position_id": "res-0", "topic": "sol", "status": "CONFIRMED", "resolution": "r0"},
        {"position_id": "res-1", "topic": "sol", "status": "CONFIRMED", "resolution": "r1"},
        {"position_id": "res-2", "topic": "sol", "status": "CONFIRMED", "resolution": "r2"},
    ]


def test_decision_mind_context_positions_non_mapping_is_empty():
    ctx = OwnerAgentEpisode._decision_mind_context(
        {"interests": [], "open_inquiries": [], "beliefs": [], "positions": ["junk"]})
    assert ctx["positions"] == {"open": [], "recent_resolutions": []}
