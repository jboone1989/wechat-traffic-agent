from datetime import datetime, timezone

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
PREDICTION_ID = "content-action-prediction:cccccccccccccccccccccccc"


class Tick:
    generation_ready = True
    def to_payload(self):
        return {"generation_ready": True}


class App:
    def run_tick(self, *, as_of_time):
        assert as_of_time == NOW.isoformat()
        return Tick()


class Loop:
    def preflight(self, *, now, cycle_id):
        return {"cycle": "cycle-1", "status": "ALREADY_PROCESSED"}
    async def run_once(self, *, now, cycle_id, focus_topic, owner_goal=None):
        return {
            "status": "DRAFT_CREATED", "cycle": "cycle-1",
            "action_prediction": {"prediction_id": PREDICTION_ID},
        }


class Wakeful:
    def __init__(self):
        self.commitments = []
        self.cognition_marks = []
    def emit_action_commitment(self, *, prediction_id, observed_at):
        self.commitments.append((prediction_id, observed_at))
        return {"status": "OBSERVED", "event_id": f"action:{prediction_id}"}
    def mark_cognition(self, now):
        self.cognition_marks.append(now)


class AfterCycle:
    async def after_content_cycle(self, **kwargs):
        return {"status": "OK"}


class Publisher:
    async def after_content_cycle(self, **kwargs):
        return {"status": "SKIPPED"}


def test_content_cycle_emits_provenance_bound_action_commitment():
    wakeful = Wakeful()
    runner = WakefulContentTaskRunner(
        app=App(), network_service=None, wakeful=wakeful,
        web_worker=None, gsc_collector=None, continuous_loop=Loop(),
        resource_economy=None, social_expression_loop=AfterCycle(),
        x_browser_publisher=Publisher(), okx_orbit_publisher=Publisher(), x_loop=None,
    )
    result = runner.run("CONTENT_COGNITION", now=NOW)
    assert result["status"] == "OK"
    assert result["neural_action_commitment"]["event_id"] == f"action:{PREDICTION_ID}"
    assert wakeful.commitments == [(PREDICTION_ID, NOW)]
    assert wakeful.cognition_marks == [NOW]
