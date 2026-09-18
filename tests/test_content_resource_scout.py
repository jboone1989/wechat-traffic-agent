from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.resource_scout import ContentResourceScout

NOW=datetime(2026,9,17,tzinfo=timezone.utc)

class Runtime:
    def __init__(self): self.calls=[]
    def invoke(self, capability, payload):
        self.calls.append((capability,payload))
        q=payload["query"]
        return SimpleNamespace(receipt_id="receipt:"+str(len(self.calls)),payload={"results":[{
            "title":"Community free inference tier", "url":"https://provider.example/free", "snippet":"Free tier; API key required after signup."
        }]})


def test_scout_discovers_from_resource_need_then_respects_cadence(tmp_path):
    runtime=Runtime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    first=scout.maybe_discover(now=NOW)
    assert first["status"]=="DISCOVERED" and first["opportunity_count"]==1
    assert len(runtime.calls)==6
    assert all(cap=="web.search" for cap,_ in runtime.calls)
    assert all("provider.example" not in payload["query"] for _,payload in runtime.calls)
    technical = [payload for _,payload in runtime.calls if payload.get("categories") == ["it"]]
    assert technical
    second=scout.maybe_discover(now=NOW)
    assert second["status"]=="NOT_DUE" and second["retry_after_seconds"] == 86400 and len(runtime.calls)==6


def test_zero_result_run_retries_after_thirty_minutes(tmp_path):
    class EmptyRuntime:
        def __init__(self): self.calls=[]
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            return SimpleNamespace(receipt_id="empty", payload={"results":[]})
    runtime=EmptyRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    first=scout.maybe_discover(now=NOW)
    assert first["status"] == "NO_NEW_OPPORTUNITIES"
    before=scout.maybe_discover(now=NOW.replace(minute=20))
    assert before["status"] == "NOT_DUE" and before["retry_after_seconds"] == 1800
    later=scout.maybe_discover(now=NOW.replace(minute=31))
    assert later["status"] == "NO_NEW_OPPORTUNITIES"
    assert len(runtime.calls) == 12


def test_verify_pending_uses_runtime_fetch_and_promotes_evidence(tmp_path):
    class VerifyRuntime:
        def __init__(self): self.calls=[]; self.persisted=0
        def persist(self): self.persisted += 1
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            if capability == "web.search":
                return SimpleNamespace(receipt_id="search:1", payload={"results":[{
                    "title":"Local LLM inference", "url":"https://code.example/local-llm", "snippet":"open source LLM inference"
                }]})
            if capability == "web.fetch":
                return SimpleNamespace(receipt_id="fetch:1", payload={"ok":True,"page":{
                    "success":True,"url":payload["url"],"markdown":"Free, open source LLM inference API and OpenAI alternative. Run locally with docker run."
                }})
            raise AssertionError(capability)
    runtime=VerifyRuntime(); scout=ContentResourceScout(state_root=tmp_path, capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    result=scout.verify_pending(now=NOW)
    assert result["verified_candidate_count"] == 1
    assert any(capability == "web.fetch" for capability,_ in runtime.calls)
    assert runtime.persisted > 0


def test_expand_provider_leads_uses_verified_public_catalogue(tmp_path):
    class LeadRuntime:
        def __init__(self): self.calls=[]; self.persisted=0
        def persist(self): self.persisted += 1
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            if capability == "web.search":
                return SimpleNamespace(receipt_id="search:lead",payload={"results":[{
                    "title":"Free LLM router","url":"https://github.com/acme/free-router","snippet":"free LLM API key router"
                }]})
            if capability == "web.fetch":
                url=payload["url"]
                if url.endswith('/HEAD/.env.example'):
                    text="# Provider One — free tier\n# Get key: https://console.one.example/keys\nONE_API_KEY=your-key\n"
                else:
                    text="Free LLM router. Free tier providers. API key required. OpenAI-compatible chat completions."
                return SimpleNamespace(receipt_id="fetch:lead",payload={"ok":True,"page":{"success":True,"url":url,"markdown":text}})
            raise AssertionError(capability)
    runtime=LeadRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    scout.verify_pending(now=NOW)
    result=scout.expand_provider_leads(now=NOW)
    assert result["lead_count"]==1
    assert result["new_leads"][0]["credential_env"]=="ONE_API_KEY"
    assert result["new_leads"][0]["official_root"]=="one.example"
    assert any(cap=="web.fetch" and payload["url"].endswith('/HEAD/.env.example') for cap,payload in runtime.calls)


def test_verify_provider_leads_uses_runtime_search_and_fetch_for_official_evidence(tmp_path):
    class OfficialRuntime:
        def __init__(self): self.calls=[]; self.persisted=0; self.phase="discover"
        def persist(self): self.persisted += 1
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            if capability == "web.search":
                if self.phase == "official":
                    return SimpleNamespace(receipt_id="search:official",payload={"results":[
                        {"title":"Provider One API pricing","url":"https://docs.one.example/pricing","snippet":"Free tier rate limits"},
                        {"title":"Unofficial","url":"https://blog.example/one","snippet":"free"},
                    ]})
                return SimpleNamespace(receipt_id="search:discover",payload={"results":[{
                    "title":"Free LLM router","url":"https://github.com/acme/free-router","snippet":"free LLM API key router"
                }]})
            if capability == "web.fetch":
                url=payload["url"]
                if url.endswith('/HEAD/.env.example'):
                    text="# Provider One — free tier\n# Get key: https://console.one.example/keys\nONE_API_KEY=your-key\n"
                elif "docs.one.example" in url:
                    text="Provider One API pricing. Free tier gives 100 requests/day at no charge. No credit card required. Rate limits apply."
                elif "console.one.example" in url:
                    text="Provider One API key signup account."
                else:
                    text="Free LLM router. Free tier providers. API key required. OpenAI-compatible chat completions."
                return SimpleNamespace(receipt_id="fetch:"+str(len(self.calls)),payload={"ok":True,"page":{"success":True,"url":url,"markdown":text}})
            raise AssertionError(capability)
    runtime=OfficialRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    scout.verify_pending(now=NOW)
    scout.expand_provider_leads(now=NOW)
    runtime.phase="official"
    result=scout.verify_provider_leads(now=NOW)
    assert result["processed_count"]==1
    row=result["processed"][0]
    assert row["status"]=="VERIFIED_RECURRING_FREE"
    assert row["free_status"]=="RECURRING_FREE"
    assert row["payment_method_required"] is False
    assert row["rate_limit_evidence"] is True
    assert all("blog.example" not in url for url in row["official_urls"])


def test_plan_trials_records_compute_pressure_and_missing_credential(tmp_path, monkeypatch):
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime())
    scout.scout._write_provider_lead(ProviderLead(
        "lead:a","op:a","Alpha","ALPHA_API_KEY","https://alpha.example/keys","alpha.example",NOW.isoformat(),"r"
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True
    ))
    monkeypatch.delenv("ALPHA_API_KEY",raising=False)
    monkeypatch.setattr(scout,"local_inference_feasibility",lambda:{"status":"DEFER_LOCAL_RESOURCE_PRESSURE","reasons":["disk_pressure"]})
    result=scout.plan_trials(now=NOW)
    assert result["plans"][0]["status"]=="NEEDS_CREDENTIAL"
    assert result["local_inference"]["status"]=="DEFER_LOCAL_RESOURCE_PRESSURE"
    assert (tmp_path/"application/resource_discovery/trial_plans/latest.json").exists()


def test_plan_trials_detects_existing_credential_by_name_only(tmp_path, monkeypatch):
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime())
    scout.scout._write_provider_lead(ProviderLead(
        "lead:a","op:a","Alpha","ALPHA_API_KEY","https://alpha.example/keys","alpha.example",NOW.isoformat(),"r"
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE"
    ))
    monkeypatch.setenv("ALPHA_API_KEY","super-secret")
    monkeypatch.setattr(scout,"local_inference_feasibility",lambda:{"status":"LOCAL_SETUP_REQUIRED","reasons":[]})
    result=scout.plan_trials(now=NOW)
    assert result["plans"][0]["status"]=="READY_TO_TRIAL"
    text=(tmp_path/"application/resource_discovery/trial_plans/latest.json").read_text()
    assert "super-secret" not in text


def test_new_external_friction_breaks_discovery_cooldown_and_changes_constraints(tmp_path):
    class AdaptiveRuntime:
        def __init__(self): self.calls=[]
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            return SimpleNamespace(receipt_id="r",payload={"results":[]})
    runtime=AdaptiveRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    before=len(runtime.calls)
    scout.record_acquisition_outcome(
        plan_id="plan:1",lead_id="lead:1",status="BLOCKED_EXTERNAL",now=NOW.replace(minute=10),
        friction_kind="PHONE_VERIFICATION",source="HUMAN_REPORTED",evidence_refs=("human-report:1",),
    )
    result=scout.maybe_discover(now=NOW.replace(minute=11))
    assert result["status"]=="NO_NEW_OPPORTUNITIES"
    assert "without phone verification" in result["adaptive_constraints"]
    assert len(runtime.calls) > before
    assert any("without phone verification" in payload["query"] for cap,payload in runtime.calls[before:] if cap=="web.search")


def test_trial_plan_uses_persisted_acquisition_friction(tmp_path, monkeypatch):
    from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=SimpleNamespace())
    lead_a=ProviderLead("lead:a","opp","A","A_KEY","https://a.example/key","a.example",NOW.isoformat(),"r")
    lead_b=ProviderLead("lead:b","opp","B","B_KEY","https://b.example/key","b.example",NOW.isoformat(),"r")
    scout.scout._write_provider_lead(lead_a); scout.scout._write_provider_lead(lead_b)
    scout.scout._write_provider_verification(ProviderVerification("lead:a","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True))
    scout.scout._write_provider_verification(ProviderVerification("lead:b","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE"))
    first=scout.plan_trials(now=NOW)
    top=first["plans"][0]
    assert top["lead_id"]=="lead:a"
    scout.record_acquisition_outcome(
        plan_id=top["plan_id"],lead_id=top["lead_id"],status="BLOCKED_EXTERNAL",now=NOW,
        friction_kind="PHONE_VERIFICATION",source="HUMAN_REPORTED",
    )
    second=scout.plan_trials(now=NOW.replace(minute=1))
    assert second["plans"][0]["lead_id"]=="lead:b"
    blocked=next(x for x in second["plans"] if x["lead_id"]=="lead:a")
    assert blocked["status"]=="BLOCKED_BY_FRICTION"


def test_no_viable_model_breaks_discovery_cooldown(tmp_path):
    class AdaptiveRuntime:
        def __init__(self): self.calls=[]
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            return SimpleNamespace(receipt_id="r",payload={"results":[]})
    runtime=AdaptiveRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    before=len(runtime.calls)
    scout.record_acquisition_outcome(
        plan_id="plan:1",lead_id="lead:1",status="NO_VIABLE_MODEL",now=NOW.replace(minute=10),
        source="RUNTIME",evidence_refs=("trial:1",),
    )
    result=scout.maybe_discover(now=NOW.replace(minute=11))
    assert result["status"]=="NO_NEW_OPPORTUNITIES"
    assert len(runtime.calls)>before
    assert any("reduce financial burn" in payload["query"] for cap,payload in runtime.calls[before:] if cap=="web.search")

def test_plan_trials_treats_unverified_provider_lead_as_autonomous_work(tmp_path, monkeypatch):
    from agent_runtime.resource_discovery import ProviderLead
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime())
    scout.scout._write_provider_lead(ProviderLead(
        'lead:new','op:new','New Cloud','NEW_CLOUD_API_KEY','https://new.example/keys','new.example',NOW.isoformat(),'r'
    ))
    monkeypatch.delenv('NEW_CLOUD_API_KEY',raising=False)
    monkeypatch.setattr(scout,'local_inference_feasibility',lambda:{'status':'DEFER_LOCAL_RESOURCE_PRESSURE','reasons':['disk_pressure']})
    result=scout.plan_trials(now=NOW)
    assert result['pending_verification_count']==0
    assert result['pending_provider_verification_count']==1
    assert result['autonomous_work_pending'] is True

def test_old_blocked_external_fact_does_not_break_discovery_cooldown(tmp_path):
    class AdaptiveRuntime:
        def __init__(self): self.calls=[]
        def invoke(self, capability, payload):
            self.calls.append((capability,payload))
            return SimpleNamespace(receipt_id="r",payload={"results":[{
                "title":"Free inference API","url":"https://models.example/free","snippet":"free API key tier"
            }]})
    runtime=AdaptiveRuntime(); scout=ContentResourceScout(state_root=tmp_path,capability_runtime=runtime)
    scout.maybe_discover(now=NOW)
    before=len(runtime.calls)
    scout.record_acquisition_outcome(
        plan_id="plan:old",lead_id="lead:old",status="BLOCKED_EXTERNAL",
        now=NOW.replace(year=2025),friction_kind="PHONE_VERIFICATION",
        source="HUMAN_REPORTED",evidence_refs=("human-report:old",),
    )
    result=scout.maybe_discover(now=NOW.replace(minute=5))
    assert result["status"]=="NOT_DUE"
    assert len(runtime.calls)==before

def test_acquisition_outcome_is_immutable_for_same_evidence(tmp_path):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=SimpleNamespace())
    first=scout.record_acquisition_outcome(
        plan_id="plan:1",lead_id="lead:1",status="MODEL_TARGETS_INCOMPATIBLE",
        now=NOW,source="RUNTIME",detail_summary="same fact",evidence_refs=("trial:1","trial:2"),
    )
    second=scout.record_acquisition_outcome(
        plan_id="plan:1",lead_id="lead:1",status="MODEL_TARGETS_INCOMPATIBLE",
        now=NOW.replace(minute=10),source="RUNTIME",detail_summary="same fact",
        evidence_refs=("trial:1","trial:2"),
    )
    third=scout.record_acquisition_outcome(
        plan_id="plan:1",lead_id="lead:1",status="MODEL_TARGETS_INCOMPATIBLE",
        now=NOW.replace(minute=11),source="RUNTIME",detail_summary="same fact",
        evidence_refs=("trial:1","trial:2","trial:3"),
    )
    assert first["created"] is True
    assert second["created"] is False
    assert second["observed_at"]==first["observed_at"]
    assert third["created"] is True
    rows=scout.acquisition_outcomes()
    assert len(rows)==2
    assert min(row.observed_at for row in rows)==NOW.isoformat()
