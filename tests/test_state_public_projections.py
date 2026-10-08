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
