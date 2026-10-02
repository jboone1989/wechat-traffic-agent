from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace
import pytest
from wechat_traffic_agent.content_loop import _put
from wechat_traffic_agent.x_owner_execution import SCHEMA, owner_x_publication_request, prepare_owner_x_expression
from wechat_traffic_agent.x_social import XAutonomyLoop
from wechat_traffic_agent.x_growth_mission import XGrowthMissionCoordinator
from test_x_social import _ModelRuntime, _Runtime, _x_strategy, NOW
from test_x_growth_mission import _runtime, _user


def cycle_context(root, mission_id="mission-x", cycle="v2-owner-x"):
    folder = root / "content/continuous/cycles" / cycle
    plan = {"goal":{"goal_id":"goal-owner","origin":"USER"},
            "required_topic":"durable-agent-work","external_mission_id":mission_id}
    request = {"schema_version":SCHEMA,"cycle":cycle,"episode_id":"episode-1",
               "owner_goal_id":"goal-owner","mission_id":mission_id,
               "decision":{"action":"RUN_CONTENT_PIPELINE","topic":"durable-agent-work",
                           "publication_surface":"X_SHORT_POST"}}
    cognition = {"worth_saying":True,"expression_kind":"THOUGHT","what_changed":"Checkpoints preserve durable jobs."}
    source = {"evidence_id":"source-1","summary":"The project documents durable jobs and checkpoints.",
              "evidence_refs":["web:source-1"],"url":"https://example.org/source"}
    _put(folder / "mind_plan.json",plan)
    _put(folder / "owner_publication_request.json",request)
    _put(folder / "cognition.json",cognition)
    result = prepare_owner_x_expression(folder=folder,source_evidence=source,cognition=cognition,
                                       worldview={},prediction=None,mind_plan=plan)
    _put(folder / "result.json",result)
    return folder,result


def test_request_must_match_owner_cycle_and_topic(tmp_path):
    folder,_ = cycle_context(tmp_path)
    request=json.loads((folder/"owner_publication_request.json").read_text())
    request["owner_goal_id"]="other-goal"
    (folder/"owner_publication_request.json").write_text(json.dumps(request))
    assert owner_x_publication_request(folder) == {}
    with pytest.raises(ValueError,match="does not match"):
        prepare_owner_x_expression(folder=folder,source_evidence={},cognition={"worth_saying":True},
                                   worldview={},prediction=None,mind_plan={})


def test_explicit_owner_short_post_executes_with_one_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    _, result = cycle_context(tmp_path)
    model = _ModelRuntime([{"post_text": "I'm Ferro: the project documents jobs and checkpoints that outlive a chat turn.",
        "rationale": "A source-attributed architecture observation."}])
    class AdvisoryL1:
        def choose_x_strategy(self, **kwargs):
            return _x_strategy(post_timing="HOLD", x_format="LONG_POST")
    model.l1_decisions = AdvisoryL1()
    runtime = _Runtime(post_ok=False, post_status="X_CREDITS_DEPLETED")
    calls = []
    class Browser:
        async def identity(self):
            return {"username": "agent", "public_metrics": {"followers_count": 2000}}
        async def publish(self, *, text):
            calls.append(text)
            return SimpleNamespace(post_id="new-123", url="https://x.com/agent/status/new-123", provider="JEV_AGENT_RUNTIME")
    loop = XAutonomyLoop(runtime=runtime, model_runtime=model, state_root=tmp_path, base_url="",
        browser_publish_transport=Browser())
    outcome = asyncio.run(loop.after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "POSTED"
    assert outcome["transport"] == "JEV_AGENT_RUNTIME"
    assert runtime.calls == []
    assert outcome["owner_execution_request"]["owner_goal_id"] == "goal-owner"
    assert len(model.tasks) == 1
    assert len(calls) == 1
    duplicate = asyncio.run(loop.after_content_cycle(result=result, now=NOW))
    assert duplicate["status"] == "ALREADY_EXPRESSED"
    assert len(calls) == 1
    assert len(model.tasks) == 1


def test_owner_short_post_still_requires_publication_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED","0")
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED","0")
    _,result=cycle_context(tmp_path)
    runtime=_Runtime()
    loop=XAutonomyLoop(runtime=runtime,model_runtime=_ModelRuntime([]),state_root=tmp_path,base_url="")
    outcome=asyncio.run(loop.after_content_cycle(result=result,now=NOW))
    assert outcome["status"] == "WRITE_DISABLED"
    assert runtime.calls == []


def test_mission_binds_owner_x_cognition_without_website_article(tmp_path):
    runtime,clock,_ = _runtime(tmp_path)
    coordinator=XGrowthMissionCoordinator(runtime=runtime,state_root=tmp_path,target=5000,enabled=True)
    mission=asyncio.run(coordinator.observe_identity(user=_user(2000),identity_receipt_id="initial",now=clock.now()))
    folder,result=cycle_context(tmp_path,mission["mission_id"])
    assert not (folder/"mission_discovery.json").exists()
    assert not (folder/"publication.json").exists()
    binding=coordinator.publication_binding(cycle=result["cycle"])
    assert binding["status"] == "BOUND_X_EXPRESSION"
    assert binding["admission"]["source_evidence_id"] == "source-1"
    baseline=coordinator.record_publication_baseline(cycle=result["cycle"],user=_user(2000),
                                                  identity_receipt_id="pre",now=clock.now())
    assert baseline["status"] == "RECORDED"
    transition=coordinator.start_experiment_from_publication(
        cycle=result["cycle"],publication_receipt_id="browser:new",post_id="new-123",now=clock.now())
    assert transition["status"] == "STARTED"
    context=json.loads((folder/"x_expression_context.json").read_text())
    context["source_evidence"]["evidence_refs"]=[]
    (folder/"x_expression_context.json").write_text(json.dumps(context))
    assert coordinator.publication_binding(cycle=result["cycle"])["bound"] is False


def test_content_executor_stops_after_real_cognition_for_owner_x(tmp_path):
    from test_content_explorer_loop import BeliefRuntime, BeliefMind, Explorer, v2
    from test_content_loop import setup, NOW as CONTENT_NOW
    from test_content_explorer import candidate
    _,legacy,publisher=setup(tmp_path)
    runtime=BeliefRuntime()
    class OwnerExplorer(Explorer):
        async def collect(self,*,now,active):
            row=candidate()
            row["title"]="Rare evidence about validation"
            row["summary"]="Rare validation evidence reduces uncertainty about model reliability."
            return {"items":[row],"failures":[],"observed_at":now.isoformat()}
        async def filter_for_goal(self,**kwargs):
            row=candidate()
            row["title"]="Rare evidence about validation"
            row["summary"]="Rare validation evidence reduces uncertainty about model reliability."
            return {"items":[row],"matches":[],"candidate_count":1}
    worker=v2(legacy,runtime,publisher,mind=BeliefMind(),explorer=OwnerExplorer())
    cycle="v2-owner-cognition-1"
    folder=legacy.state/"content/continuous/cycles"/cycle
    _put(folder/"owner_publication_request.json",{
        "schema_version":SCHEMA,"cycle":cycle,"episode_id":"episode-1",
        "owner_goal_id":"goal-owner","mission_id":None,
        "decision":{"action":"RUN_CONTENT_PIPELINE","topic":"rare","publication_surface":"X_SHORT_POST"}})
    result=asyncio.run(worker.run_once(
        now=CONTENT_NOW,cycle_id=cycle,focus_topic="rare",
        owner_goal={"goal_id":"goal-owner","origin":"USER","description":"Publish a grounded X post"},
        publication_surface="X_SHORT_POST"))
    assert result["status"] == "X_EXPRESSION_READY", result
    assert (folder/"cognition.json").exists()
    assert (folder/"x_expression_context.json").exists()
    assert not (folder/"lesson.json").exists()
    assert not (folder/"publication.json").exists()
    assert not any("Create one original, useful web-ready content artifact" in task for task in runtime.calls)


def test_runner_executes_owner_selected_surface_and_settles_real_x_result(tmp_path):
    from test_wakeful_content_recovery import App, Economy, Wakeful, Loop
    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
    state=tmp_path/"state"
    app=App()
    class OwnerRuntime:
        active_goals=[SimpleNamespace(goal_id="goal-owner",description="Publish a real X experiment",
                                      reason="owner request",priority=0.9,origin="USER",
                                      status="ACTIVE",created_at=NOW)]
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            return json.dumps({"action":"RUN_CONTENT_PIPELINE","topic":"durable-agent-work",
                               "publication_surface":"X_SHORT_POST",
                               "rationale":"Complete the explicit owner X publication commitment."})
    app.domain_runtime=OwnerRuntime()
    class DirectLoop(Loop):
        async def run_once(self,*,publication_surface="AUTO",**kwargs):
            assert publication_surface == "X_SHORT_POST"
            result={"status":"X_EXPRESSION_READY","cycle":kwargs["cycle_id"],
                    "publication_surface":publication_surface}
            _put(self._result_path(kwargs["cycle_id"]),result)
            return result
    class X:
        async def after_content_cycle(self,**kwargs):
            return {"status":"HELD_BY_X_SAFETY","cycle":kwargs["result"]["cycle"]}
    runner=WakefulContentTaskRunner(app=app,state_root=state,wakeful=Wakeful(),web_worker=None,
             network_service=None,gsc_collector=None,continuous_loop=DirectLoop(state),
             owner_cognition_runtime=app.domain_runtime,resource_economy=Economy(),
             social_expression_loop=None,x_browser_publisher=None,okx_orbit_publisher=None,x_loop=X())
    result=runner.run("CONTENT_COGNITION",now=NOW)
    cycle=result["continuous_content"]["cycle"]
    request=json.loads((state/"content/continuous/cycles"/cycle/"owner_publication_request.json").read_text())
    assert request["decision"]["publication_surface"] == "X_SHORT_POST"
    episode=runner.owner_agent_episode.projection_for("goal-owner")
    assert episode["outcome"]["status"] == "HELD_BY_X_SAFETY"


def test_owner_x_pending_receipt_is_recovered_without_new_article(tmp_path):
    from test_wakeful_content_recovery import Wakeful
    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
    folder,result=cycle_context(tmp_path)
    class X:
        growth_mission=None
        async def after_content_cycle(self,**kwargs):
            return {"status":"POSTED","cycle":kwargs["result"]["cycle"],"url":"https://x.com/agent/status/new"}
    runner=WakefulContentTaskRunner(state_root=tmp_path,wakeful=Wakeful(),x_loop=X())
    rows={}
    recovery=runner._recover_social_stage_backlog(rows=rows,now=NOW)
    assert recovery["cycle"] == result["cycle"]
    assert recovery["results"]["x_social_expression"]["status"] == "POSTED"
    assert runner._social_stage_backlog_candidate() is None
