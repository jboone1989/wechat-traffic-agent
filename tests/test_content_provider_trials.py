from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import wechat_traffic_agent.resource_scout as resource_module
from wechat_traffic_agent.resource_scout import ContentResourceScout
from agent_runtime.resource_discovery import ModelTrial, ProviderLead, ProviderVerification
from agent_runtime.resource_trials import (
    ProviderTrialMeasurement,
    ProviderTrialRequestError,
    ProviderTrialTarget,
)

NOW=datetime(2026,9,17,16,tzinfo=timezone.utc)


def _seed_reputation(state_root, scores):
    import json
    path=state_root/"application/model_reputation/latest.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    models=[]
    for model,score in scores.items():
        models.append({
            "canonical_name":model,
            "overall":score,
            "coding":score,
            "agentic":score,
            "human_preference":score,
            "popularity":score,
            "chinese":score,
            "confidence":1.0,
            "source_refs":["external:test"],
        })
    path.write_text(json.dumps({
        "schema_version":"external-model-reputation.v1",
        "observed_at":NOW.isoformat(),
        "model_count":len(models),
        "source_status":{"test":"OK"},
        "models":models,
    }))


class Runtime:
    def invoke(self, capability, payload):
        return SimpleNamespace(receipt_id="x", payload={"results":[]})


def _seed(scout):
    leads=[
        ProviderLead("lead:n","op:n","N","NVIDIA_API_KEY","https://build.nvidia.com/","nvidia.com",NOW.isoformat(),"r"),
        ProviderLead("lead:o","op:o","O","OPENROUTER_API_KEY","https://openrouter.ai/keys","openrouter.ai",NOW.isoformat(),"r"),
    ]
    ver=[
        ProviderVerification("lead:n","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE",rate_limit_evidence=True),
        ProviderVerification("lead:o","VERIFIED_RECURRING_FREE",NOW.isoformat(),free_status="RECURRING_FREE"),
    ]
    for row in leads: scout.scout._write_provider_lead(row)
    for row in ver: scout.scout._write_provider_verification(row)


def test_ready_trials_use_credentials_without_persisting_key(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    monkeypatch.setattr(resource_module,"resolve_trial_target",lambda env,**kwargs: ProviderTrialTarget(env,"https://example/v1","m-"+env.split("_")[0].lower()))
    def fake(plan, *, api_key, now, target):
        expected="n-secret" if plan.credential_env=="NVIDIA_API_KEY" else "o-secret"
        assert api_key==expected
        cost=0.0 if plan.credential_env=="OPENROUTER_API_KEY" else None
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="COMPLETED",observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10+i,
            observed_cost=cost,response_sha256="sha256:x",evidence_refs=("e",),
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    result=scout.run_ready_trials(now=NOW,provider_limit=2)
    assert result["status"]=="TRIALED" and result["executed_request_count"]==6
    assert len(result["providers"])==2
    raw=" ".join(path.read_text() for path in (tmp_path/"application/resource_discovery").rglob("*.json"))
    assert "n-secret" not in raw and "o-secret" not in raw
    assert any(row.status=="CREDENTIAL_ACQUIRED" for row in scout.acquisition_outcomes())
    assert any(row.status=="TRIAL_SUCCEEDED" for row in scout.acquisition_outcomes())
    # Only OpenRouter-like rows have observed cost for every request, so only that lead is routable by current profile contract.
    assert result["empirical_selection"]["lead_id"]=="lead:o"


def test_completed_plan_is_idempotent_and_does_not_repeat_requests(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    calls=[]
    monkeypatch.setattr(resource_module,"resolve_trial_target",lambda env,**kwargs: ProviderTrialTarget(env,"https://example/v1","m-o"))
    def fake(plan, *, api_key, now, target):
        calls.append(plan.lead_id)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="COMPLETED",observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10,observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    first=scout.run_ready_trials(now=NOW,provider_limit=2)
    second=scout.run_ready_trials(now=NOW,provider_limit=2)
    assert first["executed_request_count"]==3 and second["executed_request_count"]==0
    assert calls==["lead:o"]


def test_completed_model_rotates_to_new_candidate_during_total_free_pool_outage(tmp_path, monkeypatch):
    import json

    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    resolved=[]
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        model="known-model" if "known-model" not in excluded else "fresh-model"
        return ProviderTrialTarget(env,"https://example/v1",model)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=now.isoformat(),quality_score=1.0,latency_ms=10,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    first=scout.run_ready_trials(now=NOW,provider_limit=1)
    assert first["executed_request_count"]==3

    pool=tmp_path/"application/llm_resource_pool"
    runtime=pool/"runtime"
    runtime.mkdir(parents=True,exist_ok=True)
    (pool/"resources.json").write_text(json.dumps({
        "resources":[{"resource_id":"active-free","pricing_class":"FREE"}],
    }))
    (runtime/"health.json").write_text(json.dumps({
        "resources":{
            "active-free":{
                "cooldown_until":(NOW+timedelta(hours=1)).isoformat(),
            },
        },
    }))

    second=scout.run_ready_trials(
        now=NOW+timedelta(minutes=1),provider_limit=1
    )

    assert second["executed_request_count"]==3
    assert calls==["known-model","fresh-model"]
    assert resolved[0]==set()
    assert resolved[1]=={"known-model"}


def test_total_free_pool_outage_prioritizes_never_tried_provider_before_shortlist_cutoff(tmp_path, monkeypatch):
    import json

    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    for env,value in (
        ("NVIDIA_API_KEY","n-secret"),
        ("OPENROUTER_API_KEY","o-secret"),
        ("CEREBRAS_API_KEY","c-secret"),
    ):
        monkeypatch.setenv(env,value)
    for lead,label,model in (
        ("lead:n","N","old-nvidia"),
        ("lead:o","O","old-openrouter"),
    ):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:{lead}:old",
            opportunity_id=lead,
            task_class="simple_public_text",
            status="FAILED",
            quality_score=None,
            latency_ms=10.0,
            observed_cost=None,
            evidence_refs=("prior-outage",),
            provider_lead_id=lead,
            provider_label=label,
            model_id=model,
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="REQUEST_FAILED",
        ))

    pool=tmp_path/"application/llm_resource_pool"
    runtime=pool/"runtime"
    runtime.mkdir(parents=True,exist_ok=True)
    (pool/"resources.json").write_text(json.dumps({
        "resources":[{"resource_id":"active-free","pricing_class":"FREE"}],
    }))
    (runtime/"health.json").write_text(json.dumps({
        "resources":{"active-free":{
            "cooldown_until":(NOW+timedelta(hours=1)).isoformat(),
        }},
    }))

    monkeypatch.setattr(
        resource_module,
        "resolve_trial_target",
        lambda env,**kwargs: ProviderTrialTarget(env,"https://example/v1","fresh-"+env.lower()),
    )
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(plan.lead_id)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:fresh:{i}",
            plan_id=plan.plan_id,
            lead_id=plan.lead_id,
            provider_label=plan.provider_label,
            credential_env=plan.credential_env,
            model=target.model,
            task_id=f"t{i}",
            status="COMPLETED",
            observed_at=now.isoformat(),
            quality_score=1.0,
            latency_ms=10,
            observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.run_ready_trials(now=NOW,provider_limit=2)

    assert result["executed_request_count"]==6
    assert calls[0]=="lead:c"
    assert "lead:c" in calls


def test_never_tried_verified_provider_is_not_starved_by_static_priority(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    for env,value in (
        ("NVIDIA_API_KEY","n-secret"),
        ("OPENROUTER_API_KEY","o-secret"),
        ("CEREBRAS_API_KEY","c-secret"),
    ):
        monkeypatch.setenv(env,value)
    for lead,label,model in (
        ("lead:n","N","old-nvidia"),
        ("lead:o","O","old-openrouter"),
    ):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:{lead}:old",
            opportunity_id=lead,
            task_class="simple_public_text",
            status="COMPLETED",
            quality_score=1.0,
            latency_ms=10.0,
            observed_cost=0.0,
            evidence_refs=("prior-trial",),
            provider_lead_id=lead,
            provider_label=label,
            model_id=model,
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="",
        ))

    monkeypatch.setattr(
        resource_module,
        "resolve_trial_target",
        lambda env,**kwargs: ProviderTrialTarget(env,"https://example/v1","fresh-"+env.lower()),
    )
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(plan.lead_id)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:fresh:{i}",
            plan_id=plan.plan_id,
            lead_id=plan.lead_id,
            provider_label=plan.provider_label,
            credential_env=plan.credential_env,
            model=target.model,
            task_id=f"t{i}",
            status="COMPLETED",
            observed_at=now.isoformat(),
            quality_score=1.0,
            latency_ms=10,
            observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.run_ready_trials(now=NOW,provider_limit=2)

    assert result["executed_request_count"]>=3
    assert calls[0]=="lead:c"
    assert "lead:c" in calls


def test_target_resolution_failure_counts_as_first_provider_attempt(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    for env,value in (
        ("NVIDIA_API_KEY","n-secret"),
        ("OPENROUTER_API_KEY","o-secret"),
        ("CEREBRAS_API_KEY","c-secret"),
    ):
        monkeypatch.setenv(env,value)
    for lead,label,model in (
        ("lead:n","N","old-nvidia"),
        ("lead:o","O","old-openrouter"),
    ):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:{lead}:old",
            opportunity_id=lead,
            task_class="simple_public_text",
            status="COMPLETED",
            quality_score=1.0,
            latency_ms=10.0,
            observed_cost=0.0,
            evidence_refs=("prior-trial",),
            provider_lead_id=lead,
            provider_label=label,
            model_id=model,
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="",
        ))

    resolved=[]
    def resolve(env, **kwargs):
        resolved.append(env)
        if env=="CEREBRAS_API_KEY":
            raise resource_module.ProviderTrialRequestError("HTTP_403")
        return ProviderTrialTarget(env,"https://example/v1","fresh-"+env.lower())
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    def fake(plan, *, api_key, now, target):
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:fresh:{i}",
            plan_id=plan.plan_id,
            lead_id=plan.lead_id,
            provider_label=plan.provider_label,
            credential_env=plan.credential_env,
            model=target.model,
            task_id=f"t{i}",
            status="COMPLETED",
            observed_at=now.isoformat(),
            quality_score=1.0,
            latency_ms=10,
            observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    first=scout.run_ready_trials(now=NOW,provider_limit=2)
    second=scout.run_ready_trials(now=NOW+timedelta(minutes=1),provider_limit=2)

    assert first["providers"][0]["lead_id"]=="lead:c"
    assert first["providers"][0]["status"]=="TARGET_RESOLUTION_FAILED"
    assert first["providers"][0]["error_codes"]==["HTTP_403"]
    assert resolved.count("CEREBRAS_API_KEY")==1
    outcomes=[
        row for row in scout.acquisition_outcomes()
        if row.lead_id=="lead:c"
    ]
    assert any(row.status=="TRIAL_FAILED" for row in outcomes)
    assert second["status"] in {"TRIALED","ALREADY_TRIALED"}


def test_changed_resolved_model_retries_without_erasing_prior_trials(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    models=iter(["old-model","new-model"])
    monkeypatch.setattr(resource_module,"resolve_trial_target",lambda env,**kwargs: ProviderTrialTarget(env,"https://example/v1",next(models)))
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="FAILED",observed_at=NOW.isoformat(),latency_ms=1.0,error_code="HTTP_410",
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    first=scout.run_ready_trials(now=NOW,provider_limit=1)
    second=scout.run_ready_trials(now=NOW,provider_limit=1)
    assert first["executed_request_count"]==3 and second["executed_request_count"]==3
    assert calls==["old-model","new-model"]
    rows=scout.scout.model_trials()
    assert len(rows)==6
    assert {row.model_id for row in rows}=={"old-model","new-model"}


def test_openrouter_rotates_only_after_all_three_requests_are_rate_limited(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    resolved=[]
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        model="free-a" if "free-a" not in excluded else "free-b"
        return ProviderTrialTarget(env,"https://example/v1",model,True)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        failed=target.model=="free-a"
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}", plan_id=plan.plan_id, lead_id=plan.lead_id,
            provider_label=plan.provider_label, credential_env=plan.credential_env,
            model=target.model, task_id=f"t{i}", status="FAILED" if failed else "COMPLETED",
            observed_at=NOW.isoformat(), quality_score=None if failed else 1.0,
            latency_ms=10, observed_cost=None if failed else 0.0,
            error_code="HTTP_429" if failed else "",
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    first=scout.run_ready_trials(now=NOW,provider_limit=2)
    second=scout.run_ready_trials(now=NOW,provider_limit=2)
    third=scout.run_ready_trials(now=NOW,provider_limit=2)
    assert first["executed_request_count"]==3
    assert second["executed_request_count"]==3
    assert third["executed_request_count"]==0
    assert calls==["free-a","free-b"]
    assert resolved[0]==set()
    assert resolved[1]=={"free-a"}
    assert second["empirical_selection"]["lead_id"]=="lead:o"


def test_openrouter_stops_after_three_explicit_rate_limited_models(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    models=("free-a","free-b","free-c","free-d")
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        model=next(item for item in models if item not in excluded)
        return ProviderTrialTarget(env,"https://example/v1",model,True)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}", plan_id=plan.plan_id, lead_id=plan.lead_id,
            provider_label=plan.provider_label, credential_env=plan.credential_env,
            model=target.model, task_id=f"t{i}", status="FAILED", observed_at=NOW.isoformat(),
            latency_ms=10, error_code="HTTP_429",
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    results=[scout.run_ready_trials(now=NOW,provider_limit=2) for _ in range(5)]
    assert calls==["free-a","free-b","free-c"]
    assert [row["executed_request_count"] for row in results]==[3,3,3,0,0]
    assert results[-1]["providers"][0]["status"]=="FREE_TIER_CANDIDATES_EXHAUSTED"
    assert results[-1]["providers"][0]["models"]==["free-a","free-b","free-c"]
    assert results[-2]["replan_required"] is True
    assert results[-1]["replan_required"] is False
    assert any(row.status=="FREE_CANDIDATES_EXHAUSTED" for row in scout.acquisition_outcomes())


def test_openrouter_rotates_after_complete_low_quality_model(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    resolved=[]
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        model="weak-free" if "weak-free" not in excluded else "good-free"
        return ProviderTrialTarget(env,"https://example/v1",model,True)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        quality=0.2 if target.model=="weak-free" else 1.0
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}", plan_id=plan.plan_id, lead_id=plan.lead_id,
            provider_label=plan.provider_label, credential_env=plan.credential_env,
            model=target.model, task_id=f"t{i}", status="COMPLETED", observed_at=NOW.isoformat(),
            quality_score=quality, latency_ms=10, observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    first=scout.run_ready_trials(now=NOW,provider_limit=2)
    second=scout.run_ready_trials(now=NOW,provider_limit=2)
    assert first["executed_request_count"]==3
    assert first["empirical_selection"] is None
    assert second["executed_request_count"]==3
    assert second["empirical_selection"]["model_id"]=="good-free"
    assert calls==["weak-free","good-free"]
    assert resolved[1]=={"weak-free"}


def test_openrouter_rotates_when_quality_floor_is_mathematically_unreachable(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")
    models=("weak-mixed","next-free")
    resolved=[]
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        return ProviderTrialTarget(env,"https://example/v1",next(m for m in models if m not in excluded),True)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        if target.model=="weak-mixed":
            return [
                ProviderTrialMeasurement("trial:w:0",plan.plan_id,plan.lead_id,plan.provider_label,plan.credential_env,target.model,"t0","COMPLETED",NOW.isoformat(),quality_score=0.5,latency_ms=10,observed_cost=0.0),
                ProviderTrialMeasurement("trial:w:1",plan.plan_id,plan.lead_id,plan.provider_label,plan.credential_env,target.model,"t1","COMPLETED",NOW.isoformat(),quality_score=0.5,latency_ms=10,observed_cost=0.0),
                ProviderTrialMeasurement("trial:w:2",plan.plan_id,plan.lead_id,plan.provider_label,plan.credential_env,target.model,"t2","FAILED",NOW.isoformat(),latency_ms=10,error_code="REQUEST_FAILED"),
            ]
        return [ProviderTrialMeasurement(
            trial_id=f"trial:n:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="COMPLETED",observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10,observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)
    first=scout.run_ready_trials(now=NOW,provider_limit=2)
    second=scout.run_ready_trials(now=NOW,provider_limit=2)
    assert first["executed_request_count"]==3
    assert second["executed_request_count"]==3
    assert calls==["weak-mixed","next-free"]
    assert resolved[1]=={"weak-mixed"}
    assert second["empirical_selection"]["model_id"]=="next-free"


def test_nvidia_keeps_searching_after_two_incompatible_models(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    models=("old-model","new-model","current-model")
    resolved=[]
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        remaining=[model for model in models if model not in excluded]
        if not remaining:
            raise resource_module.ProviderTrialRequestError("NO_COMPATIBLE_MODEL")
        return ProviderTrialTarget(env,"https://example/v1",remaining[0])
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        if target.model in {"old-model","new-model"}:
            code="HTTP_410" if target.model=="old-model" else "HTTP_404"
            return [ProviderTrialMeasurement(
                trial_id=f"trial:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
                provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
                status="FAILED",observed_at=NOW.isoformat(),latency_ms=1.0,error_code=code,
            ) for i in range(3)]
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="COMPLETED",observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=2.0,observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    first=scout.run_ready_trials(now=NOW,provider_limit=1)
    second=scout.run_ready_trials(now=NOW,provider_limit=1)
    third=scout.run_ready_trials(now=NOW,provider_limit=1)
    assert [first["executed_request_count"],second["executed_request_count"],third["executed_request_count"]]==[3,3,3]
    assert calls==["old-model","new-model","current-model"]
    assert resolved[0]==set()
    assert resolved[1]=={"old-model"}
    assert resolved[2]=={"old-model","new-model"}
    assert third["providers"][0]["status"]=="TRIAL_SUCCEEDED"
    assert third["empirical_selection"]["model_id"]=="current-model"
    assert third["replan_required"] is False
    assert not any(row.status=="MODEL_TARGETS_INCOMPATIBLE" for row in scout.acquisition_outcomes())


def test_nvidia_marks_targets_exhausted_only_after_catalogue_has_no_remaining_model(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    models=("old-model","new-model")
    def resolve(env, **kwargs):
        excluded=set(kwargs.get("exclude_models") or ())
        remaining=[model for model in models if model not in excluded]
        if not remaining:
            raise resource_module.ProviderTrialRequestError("NO_COMPATIBLE_MODEL")
        return ProviderTrialTarget(env,"https://example/v1",remaining[0])
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    def fake(plan, *, api_key, now, target):
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{target.model}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,model=target.model,task_id=f"t{i}",
            status="FAILED",observed_at=NOW.isoformat(),latency_ms=1.0,error_code="HTTP_410",
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    scout.run_ready_trials(now=NOW,provider_limit=1)
    scout.run_ready_trials(now=NOW,provider_limit=1)
    third=scout.run_ready_trials(now=NOW,provider_limit=1)
    fourth=scout.run_ready_trials(now=NOW.replace(minute=1),provider_limit=1)

    assert third["executed_request_count"]==0
    assert third["providers"][0]["status"]=="PROTOCOL_TARGETS_EXHAUSTED"
    assert third["replan_required"] is True
    assert fourth["executed_request_count"]==0
    assert fourth["replan_required"] is False
    assert any(row.status=="MODEL_TARGETS_INCOMPATIBLE" for row in scout.acquisition_outcomes())
    assert third["providers"][0]["status"]=="PROTOCOL_TARGETS_EXHAUSTED"
    assert third["replan_required"] is True
    assert fourth["replan_required"] is False
    assert any(row.status=="MODEL_TARGETS_INCOMPATIBLE" for row in scout.acquisition_outcomes())


def test_high_capability_probe_adds_verified_frontier_free_resource(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    _seed_reputation(tmp_path, {"nemotron-3-super-120b-a12b":0.95})
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    monkeypatch.setattr(
        resource_module,
        "resolve_trial_target",
        lambda env, **kwargs: (
            ProviderTrialTarget(
                env,
                "https://integrate.api.nvidia.com/v1",
                "nvidia/nemotron-3-super-120b-a12b",
                False,
                128,
            )
            if kwargs.get("selection_mode")=="HIGH_CAPABILITY"
            else ProviderTrialTarget(env,"https://example/v1","openai/gpt-oss-20b")
        ),
    )
    calls=[]
    def fake(plan, *, api_key, now, target, **kwargs):
        calls.append((plan.max_requests, kwargs["timeout_seconds"]))
        assert plan.task_class=="capability_windfall"
        assert api_key=="n-secret"
        return [ProviderTrialMeasurement(
            trial_id=f"trial:windfall:{plan.plan_id}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=1200,
            observed_cost=0.0,response_sha256="sha256:x",
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.probe_capability_windfall(now=NOW)

    assert result["status"]=="AVAILABLE"
    assert result["preflight_status"]=="PASSED"
    assert result["benchmark_ran"] is True
    assert calls==[(1,12.0),(3,30.0)]
    assert result["capability_tier"]=="FRONTIER"
    assert result["windfall_eligible"] is True
    assert result["llm_resource_pool"]["windfall_count"]==1
    import json
    manifest=json.loads(
        (tmp_path/"application/llm_resource_pool/resources.json").read_text()
    )
    row=next(x for x in manifest["resources"] if x["provider_id"]=="nvidia")
    assert row["model"]=="nvidia/nemotron-3-super-120b-a12b"
    assert row["windfall_eligible"] is True
    assert "self_upgrade" in row["task_classes"]


def test_temporary_windfall_failure_is_cooled_down_instead_of_retried_immediately(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    _seed_reputation(tmp_path, {
        "nemotron-3-super-120b-a12b":0.95,
        "glm-5.3":0.90,
    })
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    excluded_seen=[]
    def resolve(env, **kwargs):
        excluded_seen.append(set(kwargs.get("exclude_models") or ()))
        candidates=[
            "nvidia/nemotron-3-super-120b-a12b",
            "z-ai/glm-5.3",
        ]
        model=next(x for x in candidates if x not in excluded_seen[-1])
        return ProviderTrialTarget(env,"https://example/v1",model,False,128)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target, **kwargs):
        calls.append((target.model,plan.max_requests,kwargs["timeout_seconds"]))
        if target.model.startswith("nvidia/"):
            return [ProviderTrialMeasurement(
                trial_id=f"trial:503:{plan.plan_id}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
                provider_label=plan.provider_label,credential_env=plan.credential_env,
                model=target.model,task_id=f"t{i}",status="FAILED",
                observed_at=NOW.isoformat(),error_code="HTTP_503",
            ) for i in range(plan.max_requests)]
        return [ProviderTrialMeasurement(
            trial_id=f"trial:ok:{plan.plan_id}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10,observed_cost=0.0,
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    first=scout.probe_capability_windfall(now=NOW)
    second=scout.probe_capability_windfall(now=NOW)

    assert first["status"]=="TEMPORARILY_UNAVAILABLE"
    assert first["preflight_status"]=="FAILED"
    assert first["benchmark_ran"] is False
    assert second["model"]=="z-ai/glm-5.3"
    assert second["preflight_status"]=="PASSED"
    assert second["benchmark_ran"] is True
    assert calls == [
        ("nvidia/nemotron-3-super-120b-a12b",1,12.0),
        ("z-ai/glm-5.3",1,12.0),
        ("z-ai/glm-5.3",3,30.0),
    ]
    assert "nvidia/nemotron-3-super-120b-a12b" in excluded_seen[1]


def test_dynamic_high_capability_ranker_starts_with_external_leaderboard_winner():
    from agent_runtime.llm.reputation import ModelReputation, ModelReputationBook
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    book=ModelReputationBook([
        ModelReputation("gpt-oss-20b",overall=.60,coding=.60,agentic=.60,confidence=1),
        ModelReputation("nemotron-3-super-120b-a12b",overall=.80,coding=.80,agentic=.80,confidence=1),
        ModelReputation("kimi-k3",overall=.90,coding=.90,agentic=.90,confidence=1),
        ModelReputation("glm-5.3",overall=.86,coding=.86,agentic=.86,confidence=1),
        ModelReputation("nemotron-3-ultra-550b-a55b",overall=.95,coding=.95,agentic=.95,confidence=1),
        ModelReputation("deepseek-v4-flash-0731",overall=.83,coding=.83,agentic=.83,confidence=1),
    ])
    chosen = rank_high_capability_model(
        [
            "openai/gpt-oss-20b",
            "nvidia/nemotron-3-super-120b-a12b",
            "moonshotai/kimi-k3",
            "z-ai/glm-5.3",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "deepseek-ai/deepseek-v4-flash-0731",
        ],
        frozenset(),
        reputation_book=book,
    )

    assert chosen == "nvidia/nemotron-3-ultra-550b-a55b"


def test_dynamic_high_capability_ranker_rotates_by_external_reputation_after_cooldown():
    from agent_runtime.llm.reputation import ModelReputation, ModelReputationBook
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    book=ModelReputationBook([
        ModelReputation("nemotron-3-ultra-550b-a55b",overall=.95,coding=.95,agentic=.95,confidence=1),
        ModelReputation("glm-5.3",overall=.90,coding=.90,agentic=.90,confidence=1),
        ModelReputation("kimi-k3",overall=.85,coding=.85,agentic=.85,confidence=1),
        ModelReputation("deepseek-v4-flash-0731",overall=.80,coding=.80,agentic=.80,confidence=1),
        ModelReputation("nemotron-3-super-120b-a12b",overall=.75,coding=.75,agentic=.75,confidence=1),
    ])
    models = [
        "nvidia/nemotron-3-ultra-550b-a55b",
        "z-ai/glm-5.3",
        "moonshotai/kimi-k3",
        "deepseek-ai/deepseek-v4-flash-0731",
        "nvidia/nemotron-3-super-120b-a12b",
    ]
    assert rank_high_capability_model(
        models,
        frozenset({"nvidia/nemotron-3-ultra-550b-a55b"}),
        reputation_book=book,
    ) == "z-ai/glm-5.3"
    assert rank_high_capability_model(
        models,
        frozenset({
            "nvidia/nemotron-3-ultra-550b-a55b",
            "z-ai/glm-5.3",
        }),
        reputation_book=book,
    ) == "moonshotai/kimi-k3"


def test_dynamic_high_capability_ranker_does_not_mistake_specialized_nemotron_for_chat_model():
    from agent_runtime.llm.reputation import ModelReputation, ModelReputationBook
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    book=ModelReputationBook([
        ModelReputation("nemotron-parse-2.0",overall=1,coding=1,agentic=1,confidence=1),
        ModelReputation("nemotron-3.5-content-safety",overall=1,coding=1,agentic=1,confidence=1),
        ModelReputation("nemotron-3-embed-1b",overall=1,coding=1,agentic=1,confidence=1),
        ModelReputation("nemotron-3-super-120b-a12b",overall=.80,coding=.80,agentic=.80,confidence=1),
    ])
    chosen = rank_high_capability_model(
        [
            "nvidia/nemotron-parse-2.0",
            "nvidia/nemotron-3.5-content-safety",
            "nvidia/nemotron-3-embed-1b",
            "nvidia/nemotron-3-super-120b-a12b",
        ],
        frozenset(),
        reputation_book=book,
    )

    assert chosen == "nvidia/nemotron-3-super-120b-a12b"


def test_windfall_timeout_enters_short_retry_window(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    _seed_reputation(tmp_path, {"kimi-k3":0.95})
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    monkeypatch.setattr(
        resource_module,
        "resolve_trial_target",
        lambda env, **kwargs: ProviderTrialTarget(
            env,
            "https://integrate.api.nvidia.com/v1",
            "moonshotai/kimi-k3",
            False,
            128,
        ),
    )
    calls=[]
    def fake(plan, *, api_key, now, target, **kwargs):
        calls.append((plan.max_requests,kwargs["timeout_seconds"]))
        return [ProviderTrialMeasurement(
            trial_id=f"trial:timeout:{plan.plan_id}:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="FAILED",
            observed_at=NOW.isoformat(),error_code="TIMEOUT",
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.probe_capability_windfall(now=NOW)

    assert result["status"]=="TEMPORARILY_UNAVAILABLE"
    assert result["preflight_status"]=="FAILED"
    assert result["benchmark_ran"] is False
    assert calls==[(1,12.0)]
    assert result["error_codes"]==["TIMEOUT"]
    retry=datetime.fromisoformat(result["retry_at"])
    assert retry - NOW == timedelta(hours=1)


def test_dynamic_ranker_uses_external_reputation_not_model_name_bonus():
    from agent_runtime.llm.reputation import ModelReputation, ModelReputationBook
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    book=ModelReputationBook([
        ModelReputation("deepseek-v4-flash-0731",overall=.90,coding=.90,agentic=.90,confidence=1),
        ModelReputation("glm-5.3-flash",overall=.75,coding=.75,agentic=.75,confidence=1),
        ModelReputation("kimi-k2.6",overall=.70,coding=.70,agentic=.70,confidence=1),
        ModelReputation("nemotron-3.5-lightning-30b-a3b",overall=.65,coding=.65,agentic=.65,confidence=1),
    ])
    chosen = rank_high_capability_model(
        [
            "deepseek-ai/deepseek-v4-flash-0731",
            "z-ai/glm-5.3-flash",
            "moonshotai/kimi-k2.6",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        ],
        frozenset(),
        reputation_book=book,
    )

    assert chosen == "deepseek-ai/deepseek-v4-flash-0731"


def test_free_model_market_picks_best_reputation_across_nvidia_and_openrouter(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    _seed_reputation(tmp_path, {
        "nvidia-daily":0.72,
        "openrouter-windfall":0.95,
    })
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY","o-secret")

    def resolve(env, **kwargs):
        if env=="NVIDIA_API_KEY":
            return ProviderTrialTarget(env,"https://nvidia.example/v1","nvidia-daily",False,128)
        return ProviderTrialTarget(env,"https://openrouter.example/v1","openrouter-windfall",True,128)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)

    seen=[]
    def fake(plan, *, api_key, now, target, **kwargs):
        seen.append((plan.credential_env,target.model,plan.max_requests))
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.credential_env}:{plan.plan_id}:{i}",
            plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10,
            observed_cost=0.0,
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.probe_free_model_market(now=NOW)

    assert result["status"]=="AVAILABLE"
    assert result["credential_env"]=="OPENROUTER_API_KEY"
    assert result["model"]=="openrouter-windfall"
    assert seen==[
        ("OPENROUTER_API_KEY","openrouter-windfall",1),
        ("OPENROUTER_API_KEY","openrouter-windfall",3),
    ]


def test_free_model_market_can_use_cerebras_when_key_becomes_available(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    lead=ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r"
    )
    verification=ProviderVerification(
        "lead:c","VERIFIED_RECURRING_FREE",NOW.isoformat(),
        free_status="RECURRING_FREE",rate_limit_evidence=True,
    )
    scout.scout._write_provider_lead(lead)
    scout.scout._write_provider_verification(verification)
    _seed_reputation(tmp_path, {
        "gpt-oss-120b":0.96,
        "nvidia-daily":0.70,
    })
    monkeypatch.setenv("CEREBRAS_API_KEY","c-secret")
    monkeypatch.setenv("NVIDIA_API_KEY","n-secret")

    def resolve(env, **kwargs):
        if env=="CEREBRAS_API_KEY":
            return ProviderTrialTarget(env,"https://api.cerebras.ai/v1","gpt-oss-120b",False,128)
        return ProviderTrialTarget(env,"https://nvidia.example/v1","nvidia-daily",False,128)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target, **kwargs):
        calls.append((plan.credential_env,api_key,target.model))
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.credential_env}:{plan.plan_id}:{i}",
            plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=5,
            observed_cost=0.0,
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.probe_free_model_market(now=NOW)

    assert result["status"]=="AVAILABLE"
    assert result["credential_env"]=="CEREBRAS_API_KEY"
    assert result["model"]=="gpt-oss-120b"
    assert all(row[1]=="c-secret" for row in calls)


def test_stale_target_resolution_failure_gets_one_first_trial_priority_after_resolver_change(
    tmp_path, monkeypatch
):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    for env,value in (
        ("NVIDIA_API_KEY","n-secret"),
        ("OPENROUTER_API_KEY","o-secret"),
        ("CEREBRAS_API_KEY","c-secret"),
    ):
        monkeypatch.setenv(env,value)
    for lead,label,model in (
        ("lead:n","N","old-nvidia"),
        ("lead:o","O","old-openrouter"),
    ):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:{lead}:old",
            opportunity_id=lead,
            task_class="simple_public_text",
            status="COMPLETED",
            quality_score=1.0,
            latency_ms=10.0,
            observed_cost=0.0,
            evidence_refs=("prior-trial",),
            provider_lead_id=lead,
            provider_label=label,
            model_id=model,
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="",
        ))
    scout.record_acquisition_outcome(
        plan_id="provider-trial:old-cerebras",
        lead_id="lead:c",
        status="TRIAL_FAILED",
        now=NOW-timedelta(minutes=10),
        source="RUNTIME",
        detail_summary="bounded provider trial target resolution failed with HTTP_403",
        evidence_refs=("target-resolution-error:HTTP_403",),
    )

    def resolve(env, **kwargs):
        return ProviderTrialTarget(
            env,
            "https://api.cerebras.ai/v1" if env=="CEREBRAS_API_KEY" else "https://example/v1",
            "gpt-oss-120b" if env=="CEREBRAS_API_KEY" else "existing-provider-model",
            False,
            128,
        )
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append(plan.lead_id)
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:retry:{i}",
            plan_id=plan.plan_id,
            lead_id=plan.lead_id,
            provider_label=plan.provider_label,
            credential_env=plan.credential_env,
            model=target.model,
            task_id=f"t{i}",
            status="COMPLETED",
            observed_at=now.isoformat(),
            quality_score=1.0,
            latency_ms=5.0,
            observed_cost=0.0,
        ) for i in range(plan.max_requests)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.run_ready_trials(now=NOW,provider_limit=1)

    assert result["executed_request_count"]==3
    assert calls==["lead:c"]


def test_target_resolution_failure_records_current_resolver_generation(
    tmp_path, monkeypatch
):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime())
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    monkeypatch.setenv("CEREBRAS_API_KEY","c-secret")

    def fail_resolution(env, **kwargs):
        raise ProviderTrialRequestError("HTTP_403")
    monkeypatch.setattr(resource_module,"resolve_trial_target",fail_resolution)
    expected_ref=resource_module._provider_trial_resolver_evidence_ref()

    result=scout.run_ready_trials(now=NOW,provider_limit=1)

    assert result["executed_request_count"]==0
    failed=[row for row in scout.acquisition_outcomes() if row.status=="TRIAL_FAILED"]
    assert len(failed)==1
    assert "target-resolution-error:HTTP_403" in failed[0].evidence_refs
    assert expected_ref in failed[0].evidence_refs



def test_newer_runtime_404_invalidates_old_success_and_rotates_model(tmp_path, monkeypatch):
    import json

    scout = ContentResourceScout(state_root=tmp_path, capability_runtime=Runtime())
    _seed(scout)
    monkeypatch.setenv("OPENROUTER_API_KEY", "o-secret")
    resolved = []

    def resolve(env, **kwargs):
        excluded = set(kwargs.get("exclude_models") or ())
        resolved.append(excluded)
        model = "old-free" if "old-free" not in excluded else "new-free"
        return ProviderTrialTarget(env, "https://example/v1", model)

    monkeypatch.setattr(resource_module, "resolve_trial_target", resolve)
    calls = []

    def fake(plan, *, api_key, now, target):
        calls.append(target.model)
        return [
            ProviderTrialMeasurement(
                trial_id=f"trial:{target.model}:{i}",
                plan_id=plan.plan_id,
                lead_id=plan.lead_id,
                provider_label=plan.provider_label,
                credential_env=plan.credential_env,
                model=target.model,
                task_id=f"t{i}",
                status="COMPLETED",
                observed_at=now.isoformat(),
                quality_score=1.0,
                latency_ms=10,
                observed_cost=0.0,
            )
            for i in range(3)
        ]

    monkeypatch.setattr(resource_module, "run_bounded_provider_trial", fake)

    first = scout.run_ready_trials(now=NOW, provider_limit=2)
    assert first["executed_request_count"] == 3
    manifest_path = tmp_path / "application/llm_resource_pool/resources.json"
    manifest = json.loads(manifest_path.read_text())
    old = next(row for row in manifest["resources"] if row["model"] == "old-free")
    health_path = tmp_path / "application/llm_resource_pool/runtime/health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps({
        "schema_version": "llm-resource-health.v1",
        "resources": {
            old["resource_id"]: {
                "successes": 3,
                "failures": 1,
                "consecutive_failures": 1,
                "cooldown_until": (NOW + timedelta(hours=7)).isoformat(),
                "last_error_code": "HTTP_404",
                "last_success_at": NOW.isoformat(),
                "last_failure_at": (NOW + timedelta(hours=1)).isoformat(),
                "consumed_quota": 0.0,
                "quota_window_signature": "",
            }
        },
    }))

    second = scout.run_ready_trials(now=NOW + timedelta(hours=2), provider_limit=2)

    assert second["executed_request_count"] == 3
    assert calls == ["old-free", "new-free"]
    assert resolved[1] == {"old-free"}
    assert second["empirical_selection"]["model_id"] == "new-free"


def test_old_executor_failed_provider_is_not_starved_by_successful_higher_priority_providers(tmp_path, monkeypatch):
    scout=ContentResourceScout(state_root=tmp_path,capability_runtime=Runtime()); _seed(scout)
    scout.scout._write_provider_lead(ProviderLead(
        "lead:c","op:c","Cerebras","CEREBRAS_API_KEY",
        "https://cloud.cerebras.ai/","cerebras.ai",NOW.isoformat(),"r",
    ))
    scout.scout._write_provider_verification(ProviderVerification(
        "lead:c","VERIFIED_TRIAL_CREDIT",NOW.isoformat(),free_status="TRIAL_CREDIT",
    ))
    for env,value in (
        ("NVIDIA_API_KEY","n-secret"),
        ("OPENROUTER_API_KEY","o-secret"),
        ("CEREBRAS_API_KEY","c-secret"),
    ):
        monkeypatch.setenv(env,value)

    for lead,label,model in (
        ("lead:n","N","n-model"),
        ("lead:o","O","o-model"),
    ):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:{lead}:success",
            opportunity_id=lead,
            task_class="simple_public_text",
            status="COMPLETED",
            quality_score=1.0,
            latency_ms=10.0,
            observed_cost=0.0,
            evidence_refs=("prior-success",),
            provider_lead_id=lead,
            provider_label=label,
            model_id=model,
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="",
        ))
    for i in range(3):
        scout.scout.record_model_trial(ModelTrial(
            trial_id=f"trial:lead:c:old:{i}",
            opportunity_id="lead:c",
            task_class="simple_public_text",
            status="FAILED",
            quality_score=None,
            latency_ms=10.0,
            observed_cost=None,
            evidence_refs=("old-executor",),
            provider_lead_id="lead:c",
            provider_label="Cerebras",
            model_id="gpt-oss-120b",
            started_at=(NOW-timedelta(days=1)).isoformat(),
            completed_at=(NOW-timedelta(days=1)).isoformat(),
            error_code="HTTP_403",
        ))

    def resolve(env, **kwargs):
        model = {
            "NVIDIA_API_KEY":"n-model",
            "OPENROUTER_API_KEY":"o-model",
            "CEREBRAS_API_KEY":"gpt-oss-120b",
        }[env]
        return ProviderTrialTarget(env,"https://example/v1",model)
    monkeypatch.setattr(resource_module,"resolve_trial_target",resolve)
    calls=[]
    def fake(plan, *, api_key, now, target):
        calls.append((plan.lead_id, plan.plan_id))
        return [ProviderTrialMeasurement(
            trial_id=f"trial:{plan.lead_id}:refresh:{i}",
            plan_id=plan.plan_id,
            lead_id=plan.lead_id,
            provider_label=plan.provider_label,
            credential_env=plan.credential_env,
            model=target.model,
            task_id=f"t{i}",
            status="COMPLETED",
            observed_at=now.isoformat(),
            quality_score=1.0,
            latency_ms=10,
            observed_cost=0.0,
            evidence_refs=tuple(plan.evidence_refs),
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.run_ready_trials(now=NOW,provider_limit=2)

    assert result["executed_request_count"]==3
    assert calls[0][0]=="lead:c"
    assert calls[0][1].endswith(":executor-refresh")


def test_failed_trial_from_old_executor_gets_one_refresh_retry():
    from agent_runtime.resource_discovery import ProviderTrialPlan
    plan = ProviderTrialPlan(
        plan_id="provider-trial:c",
        lead_id="lead:c",
        provider_label="Cerebras",
        credential_env="CEREBRAS_API_KEY",
        status="READY_TO_TRIAL",
        priority_score=1.0,
    )
    prior = [
        ModelTrial(
            trial_id=f"old:{i}",
            opportunity_id="lead:c",
            task_class="simple_public_text",
            status="FAILED",
            provider_lead_id="lead:c",
            provider_label="Cerebras",
            model_id="gpt-oss-120b",
            started_at=(NOW - timedelta(days=1)).isoformat(),
            completed_at=(NOW - timedelta(days=1)).isoformat(),
            error_code="HTTP_403",
            evidence_refs=("old-executor",),
        )
        for i in range(3)
    ]

    retry = resource_module._provider_trial_retry_plan(plan, prior, now=NOW)

    assert retry is not None
    assert retry.plan_id.endswith(":executor-refresh")
    assert resource_module.provider_trial_execution_evidence_ref() in retry.evidence_refs


def test_current_executor_failure_retries_only_after_bounded_cooldown():
    from agent_runtime.resource_discovery import ProviderTrialPlan
    current_ref = resource_module.provider_trial_execution_evidence_ref()
    plan = ProviderTrialPlan(
        plan_id="provider-trial:c",
        lead_id="lead:c",
        provider_label="Cerebras",
        credential_env="CEREBRAS_API_KEY",
        status="READY_TO_TRIAL",
        priority_score=1.0,
    )
    prior = [
        ModelTrial(
            trial_id=f"current:{i}",
            opportunity_id="lead:c",
            task_class="simple_public_text",
            status="FAILED",
            provider_lead_id="lead:c",
            provider_label="Cerebras",
            model_id="gpt-oss-120b",
            started_at=NOW.isoformat(),
            completed_at=NOW.isoformat(),
            error_code="REQUEST_FAILED",
            evidence_refs=(current_ref,),
        )
        for i in range(3)
    ]

    assert resource_module._provider_trial_retry_plan(
        plan, prior, now=NOW + timedelta(hours=1)
    ) is None
    retry = resource_module._provider_trial_retry_plan(
        plan, prior, now=NOW + timedelta(hours=7)
    )
    assert retry is not None
    assert ":retry:" in retry.plan_id
