from datetime import datetime, timezone
from types import SimpleNamespace

import wechat_traffic_agent.resource_scout as resource_module
from wechat_traffic_agent.resource_scout import ContentResourceScout
from agent_runtime.resource_discovery import ProviderLead, ProviderVerification
from agent_runtime.resource_trials import ProviderTrialMeasurement, ProviderTrialTarget

NOW=datetime(2026,9,17,16,tzinfo=timezone.utc)

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
    def fake(plan, *, api_key, now, target, **kwargs):
        assert kwargs["timeout_seconds"]==30.0
        assert plan.task_class=="capability_windfall"
        assert api_key=="n-secret"
        return [ProviderTrialMeasurement(
            trial_id=f"trial:windfall:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=1200,
            observed_cost=0.0,response_sha256="sha256:x",
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    result=scout.probe_capability_windfall(now=NOW)

    assert result["status"]=="AVAILABLE"
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
    def fake(plan, *, api_key, now, target, **kwargs):
        assert kwargs["timeout_seconds"]==30.0
        if target.model.startswith("nvidia/"):
            return [ProviderTrialMeasurement(
                trial_id=f"trial:503:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
                provider_label=plan.provider_label,credential_env=plan.credential_env,
                model=target.model,task_id=f"t{i}",status="FAILED",
                observed_at=NOW.isoformat(),error_code="HTTP_503",
            ) for i in range(3)]
        return [ProviderTrialMeasurement(
            trial_id=f"trial:ok:{i}",plan_id=plan.plan_id,lead_id=plan.lead_id,
            provider_label=plan.provider_label,credential_env=plan.credential_env,
            model=target.model,task_id=f"t{i}",status="COMPLETED",
            observed_at=NOW.isoformat(),quality_score=1.0,latency_ms=10,observed_cost=0.0,
        ) for i in range(3)]
    monkeypatch.setattr(resource_module,"run_bounded_provider_trial",fake)

    first=scout.probe_capability_windfall(now=NOW)
    second=scout.probe_capability_windfall(now=NOW)

    assert first["status"]=="TEMPORARILY_UNAVAILABLE"
    assert second["model"]=="z-ai/glm-5.3"
    assert "nvidia/nemotron-3-super-120b-a12b" in excluded_seen[1]


def test_dynamic_high_capability_ranker_prefers_kimi_k3_over_known_frontier_models():
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    chosen = rank_high_capability_model(
        [
            "openai/gpt-oss-20b",
            "nvidia/nemotron-3-super-120b-a12b",
            "moonshotai/kimi-k3",
            "z-ai/glm-5.3",
        ],
        frozenset(),
    )

    assert chosen == "moonshotai/kimi-k3"


def test_dynamic_high_capability_ranker_moves_to_next_challenger_after_kimi_cooldown():
    from wechat_traffic_agent.resource_scout import rank_high_capability_model

    chosen = rank_high_capability_model(
        [
            "moonshotai/kimi-k3",
            "nvidia/nemotron-3-super-120b-a12b",
            "z-ai/glm-5.3",
        ],
        frozenset({"moonshotai/kimi-k3"}),
    )

    assert chosen == "nvidia/nemotron-3-super-120b-a12b"
