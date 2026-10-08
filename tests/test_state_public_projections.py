"""Live API data must traverse the neural-owner event bus, not create a second neural runtime."""
from datetime import datetime, timezone
from wechat_traffic_agent.state_events import FerroStateClient, FerroStateEventApplier

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_neural_owner_applies_public_brain_and_dream_projections():
    class Wakeful:
        def brain_lab_projection(self):
            return {"status": "READY", "neurons": 8}
        def brain_lab_live_projection(self):
            return {"status": "LIVE", "frames": 12}
        def dream_projection(self):
            return {"dream_id": "real-dream"}
    applier = FerroStateEventApplier(Wakeful())
    for kind, expected in [
        ("BRAIN_LAB_PROJECTION", {"status": "READY", "neurons": 8}),
        ("BRAIN_LAB_LIVE_PROJECTION", {"status": "LIVE", "frames": 12}),
        ("DREAM_PROJECTION", {"dream_id": "real-dream"}),
    ]:
        assert applier.apply({
            "kind": kind, "payload": {}, "observed_at": NOW.isoformat()
        }) == {"status": "OK", "projection": expected}


def test_public_state_client_forwards_projection_requests(tmp_path, monkeypatch):
    client = FerroStateClient(tmp_path)
    queries = []
    def request(kind, payload, *, observed_at):
        queries.append(kind)
        return {"status": "OK", "projection": {"source": kind}}
    monkeypatch.setattr(client.bus, "request", request)
    assert client.brain_lab_projection() == {"source": "BRAIN_LAB_PROJECTION"}
    assert client.brain_lab_live_projection() == {"source": "BRAIN_LAB_LIVE_PROJECTION"}
    assert client.dream_projection() == {"source": "DREAM_PROJECTION"}
    assert queries == ["BRAIN_LAB_PROJECTION", "BRAIN_LAB_LIVE_PROJECTION", "DREAM_PROJECTION"]


def test_neural_owner_reads_game_and_dream_projections_without_running_games(tmp_path):
    from types import SimpleNamespace
    from wechat_traffic_agent.ferro_neural_runtime import FerroNeuralRuntime
    owner = object.__new__(FerroNeuralRuntime)
    owner.state_root = tmp_path
    owner.core = SimpleNamespace(reflex=SimpleNamespace(substrate=None))
    before = list(tmp_path.rglob("brain_lab.json"))
    snapshot = owner.brain_lab_projection()
    live = owner.brain_lab_live_projection()
    assert snapshot["schema_version"] == "ferro-brain-lab.v1"
    assert live["schema_version"] == snapshot["schema_version"]
    assert snapshot["total_steps"] == live["total_steps"] == 0
    assert before == list(tmp_path.rglob("brain_lab.json"))
    dreams = owner.dream_projection()
    assert "research_dream" in dreams and "awe_hillclimb" in dreams
