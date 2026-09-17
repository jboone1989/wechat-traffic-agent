from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW=datetime(2026,9,17,13,30,tzinfo=timezone.utc)

class Economy:
    def observe(self, now): return {"status":"OK"}
class Mind:
    def public_projection(self): return {"resource_awareness":{"current":{"pressure":"WATCH"}}}
class Wakeful:
    def set_resource_pressure(self, pressure): self.pressure=pressure
class Scout:
    def maybe_discover(self, **kwargs): return {"status":"NOT_DUE"}
    def verify_pending(self, **kwargs): return {"status":"NO_PENDING"}
    def expand_provider_leads(self, **kwargs): return {"status":"NO_NEW_LEADS"}
    def verify_provider_leads(self, **kwargs): return {"status":"NO_PENDING"}
    def plan_trials(self, **kwargs):
        return {"status":"PLANNED","local_inference":{"status":"DEFER_LOCAL_RESOURCE_PRESSURE","reasons":["disk_pressure"]},"plans":[{
            "plan_id":"plan:1","lead_id":"lead:1","provider_label":"Provider One","credential_env":"ONE_API_KEY",
            "signup_url":"https://provider.example/keys","priority_score":0.63,"reasons":["recurring_free_evidence","credential_missing"],"status":"NEEDS_CREDENTIAL",
        }]}
class Assistance:
    def __init__(self): self.calls=[]
    def request(self, **kwargs): self.calls.append(kwargs); return {"request_id":"human-request:1","delivery_status":"SENT","kind":kwargs["kind"]}

def test_resource_review_requests_only_top_credential_when_local_path_is_deferred():
    assistance=Assistance()
    runner=WakefulContentTaskRunner(resource_economy=Economy(),content_mind=Mind(),wakeful=Wakeful(),resource_scout=Scout(),human_assistance=assistance)
    result=runner.run("RESOURCE_REVIEW",now=NOW)
    assert result["status"]=="OK"
    assert result["provider_access_request"]["delivery_status"]=="SENT"
    assert len(assistance.calls)==1
    assert assistance.calls[0]["kind"]=="MODEL_RESOURCE_ACCESS"
    assert "ONE_API_KEY" in assistance.calls[0]["requested_action"]

def test_resource_review_does_not_interrupt_human_when_trial_is_ready():
    class ReadyScout(Scout):
        def plan_trials(self, **kwargs):
            row=super().plan_trials(**kwargs); row["plans"][0]["status"]="READY_TO_TRIAL"; return row
    assistance=Assistance()
    runner=WakefulContentTaskRunner(resource_economy=Economy(),content_mind=Mind(),wakeful=Wakeful(),resource_scout=ReadyScout(),human_assistance=assistance)
    result=runner.run("RESOURCE_REVIEW",now=NOW)
    assert result["status"]=="OK" and not assistance.calls
