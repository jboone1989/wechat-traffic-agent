from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import pytest

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW=datetime(2026,9,17,13,30,tzinfo=timezone.utc)

@pytest.fixture(autouse=True)



@pytest.fixture(autouse=True)


class Economy:
    def observe(self, now): return {"status":"OK"}
class Mind:
    def public_projection(self): return {"resource_awareness":{"current":{"pressure":"WATCH"}}}
class Wakeful:
    def set_resource_pressure(self, pressure): self.pressure=pressure
    def schedule_resource_followup(self, now, *, seconds=300): self.followup=(now,seconds)
class Scout:
    def maybe_discover(self, **kwargs): return {"status":"NOT_DUE"}
    def verify_pending(self, **kwargs): return {"status":"NO_PENDING"}
    def expand_provider_leads(self, **kwargs): return {"status":"NO_NEW_LEADS"}
    def verify_provider_leads(self, **kwargs): return {"status":"NO_PENDING"}
    def plan_trials(self, **kwargs):
        return {"status":"PLANNED","autonomous_work_pending":False,"pending_verification_count":0,"local_inference":{"status":"DEFER_LOCAL_RESOURCE_PRESSURE","reasons":["disk_pressure"]},"plans":[{
            "plan_id":"plan:1","lead_id":"lead:1","provider_label":"Provider One","credential_env":"ONE_API_KEY",
            "signup_url":"https://provider.example/keys","priority_score":0.63,"reasons":["recurring_free_evidence","credential_missing"],"status":"NEEDS_CREDENTIAL",
        }]}
    def run_ready_trials(self, **kwargs):
        return {"status":"TRIALED","executed_request_count":3,"providers":[],"empirical_profiles":[],"empirical_selection":None,"replan_required":False}
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


def test_resource_review_defers_human_and_schedules_followup_while_autonomous_work_remains():
    class PendingScout(Scout):
        def plan_trials(self, **kwargs):
            row=super().plan_trials(**kwargs)
            row["autonomous_work_pending"]=True
            row["pending_verification_count"]=7
            return row
    assistance=Assistance(); wakeful=Wakeful()
    runner=WakefulContentTaskRunner(resource_economy=Economy(),content_mind=Mind(),wakeful=wakeful,resource_scout=PendingScout(),human_assistance=assistance)
    result=runner.run("RESOURCE_REVIEW",now=NOW)
    assert result["status"]=="OK"
    assert not assistance.calls
    assert result["resource_followup"]["pending_verification_count"]==7
    assert wakeful.followup[1]==300


def test_resource_review_schedules_followup_when_trials_require_replan():
    class ReplanScout(Scout):
        def plan_trials(self, **kwargs):
            row=super().plan_trials(**kwargs); row["plans"][0]["status"]="READY_TO_TRIAL"; return row
        def run_ready_trials(self, **kwargs):
            return {"status":"ALREADY_TRIALED","executed_request_count":0,"providers":[],"empirical_profiles":[],"empirical_selection":None,"replan_required":True}
    assistance=Assistance(); wakeful=Wakeful()
    runner=WakefulContentTaskRunner(resource_economy=Economy(),content_mind=Mind(),wakeful=wakeful,resource_scout=ReplanScout(),human_assistance=assistance)
    result=runner.run("RESOURCE_REVIEW",now=NOW)
    assert result["status"]=="OK"
    assert result["resource_followup"]["reason"]=="provider trial evidence requires alternate routes"
    assert wakeful.followup[1]==300
    assert not assistance.calls

def test_resource_review_executes_one_chained_safe_survival_phase(tmp_path, monkeypatch):
    class Survival:
        def __init__(self):
            self.observe_calls = 0
            self.execute_calls = 0
        def observe(self, *, now):
            self.observe_calls += 1
            return {"status":"OBSERVED","pressure":"NORMAL","needs":[]}
        def remote_hosts(self):
            return [{"status":"ACTIVE"}]
        def execute_approved(self, *, now):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return {"status":"OK","results":[{
                    "action":"PROVISION_REMOTE_RECEIVER","status":"VERIFIED"
                }]}
            if self.execute_calls == 2:
                return {"status":"OK","results":[{
                    "action":"ENSURE_SERVICE_REPLICA","status":"DEPLOYED"
                }]}
            raise AssertionError("resource review must be bounded to two action phases")

    survival = Survival()
    monkeypatch.setattr(
        "wechat_traffic_agent.survival_resources.SurvivalResourceManager",
        lambda path: survival,
    )
    runner=WakefulContentTaskRunner(
        resource_economy=Economy(), content_mind=Mind(), wakeful=Wakeful(),
        resource_scout=Scout(), human_assistance=Assistance(), state_root=tmp_path,
    )
    result=runner.run("RESOURCE_REVIEW",now=NOW)
    assert result["status"]=="OK"
    assert result["survival_resource_actions"]["results"][0]["action"]=="PROVISION_REMOTE_RECEIVER"
    assert result["survival_resource_followup_actions"]["results"][0]["action"]=="ENSURE_SERVICE_REPLICA"
    assert "survival_resource_after_followup_actions" in result
    assert survival.execute_calls==2
    assert survival.observe_calls==3
