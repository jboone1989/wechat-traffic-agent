"""Unit checks run actual control methods with browser/model I/O substituted."""
import ast, asyncio, json, sys, tempfile, types, unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

SRC=Path(__file__).resolve().parents[1]/"src/wechat_traffic_agent"
def load(name,extra=None):
    tree=ast.parse((SRC/(name+".py")).read_text())
    tree.body=[n for n in tree.body if not(isinstance(n,ast.ImportFrom) and n.level)]
    module=types.ModuleType("_owner_progress_"+name)
    module.__package__="wechat_traffic_agent";module.__dict__.update(extra or {})
    sys.modules[module.__name__]=module
    exec(compile(tree,str(SRC/(name+".py")),"exec"),module.__dict__)
    return module
tree=ast.parse((SRC/"content_service.py").read_text())
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_extract_json")
scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),"extract","exec"),scope)
owner=load("owner_agent_episode",{"_extract_json":scope["_extract_json"]})
tasks=load("wakeful_tasks")
progress=load("x_goal_progress")
NOW=datetime(2026,10,2,16,tzinfo=timezone.utc)
class Runtime:
    def __init__(self,choices):self.choices=list(choices);self.prompts=[]
    async def cognitive_probe(self,prompt,**kw):
        self.prompts.append(prompt);return json.dumps(self.choices.pop(0))
class TestOwnerXProgress(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def save(self,p,value):
        path=self.root/p;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return path
    def test_wait_is_recalled_after_restart_and_original_archived(self):
        episode=owner.OwnerAgentEpisode(self.root);goal={"goal_id":"grow-x"}
        async def no_tool(_):raise AssertionError("WAIT cannot invoke a write")
        asyncio.run(episode.run(runtime=Runtime([{"action":"WAIT","wait_seconds":300,"wait_for":"Snapshot due","next_action":"CHECK_OUTCOMES","rationale":"Wait for the due snapshot."}]),owner_goal=goal,mission_context={},tool_executor=no_tool,now=NOW))
        episode.complete(outcome={"status":"WAIT_SCHEDULED","wait_for":"Snapshot due"},now=NOW,goal_id="grow-x")
        original=episode.projection_for("grow-x")
        runtime=Runtime([{"action":"RUN_CONTENT_PIPELINE","topic":"agent-recovery","publication_surface":"X_ARTICLE","rationale":"Useful evidence supports an Article now."}])
        result=asyncio.run(owner.OwnerAgentEpisode(self.root).run(runtime=runtime,owner_goal=goal,mission_context={},tool_executor=no_tool,now=NOW+timedelta(minutes=40)))
        self.assertEqual(result["decision"]["publication_surface"],"X_ARTICLE")
        self.assertIn('"consecutive_waits": 1',runtime.prompts[0]);self.assertIn("Snapshot due",runtime.prompts[0])
        archives=list((self.root/"application/owner_agent_episodes/history").rglob("*.json"))
        self.assertEqual(len(archives),1);self.assertEqual(json.loads(archives[0].read_text()),original)
    def test_new_commitment_has_its_own_history(self):
        episode=owner.OwnerAgentEpisode(self.root)
        episode._state_for({"goal_id":"grow-x","commitment_id":"old"},NOW)
        episode.complete(outcome={"status":"WAIT_SCHEDULED"},now=NOW,goal_id="grow-x")
        state=episode._state_for({"goal_id":"grow-x","commitment_id":"new"},NOW)
        self.assertIsNone(state["previous_episode"]);self.assertEqual(state["consecutive_waits"],0)
    def dispatch(self,action):
        runtime=Runtime([{"action":action,"rationale":"Use a useful X action for this growth goal."},{"action":"WAIT","wait_seconds":300,"wait_for":"Revisit the observed discussion","next_action":"SEARCH_WORLD","rationale":"No useful reply was selected in this sample."}])
        observer=AsyncMock(return_value={"status":"OK","new_mentions":0,"reply_count":0,"replies":[],"engagement_outcome":"NO_REPLY_SELECTED"})
        runner=tasks.WakefulContentTaskRunner(app=types.SimpleNamespace(domain_runtime=object()),owner_agent_episode=owner.OwnerAgentEpisode(self.root),owner_cognition_runtime=runtime,wakeful=types.SimpleNamespace(emit_x_mentions=lambda **kw:None),x_loop=types.SimpleNamespace(observe=observer))
        runner.state_root=self.root;runner._owner_agent_mission_context=lambda goal,now:{}
        context=types.ModuleType("wechat_traffic_agent.cognition_context");context.owner_cognition_scope=lambda value:nullcontext()
        with patch.dict(sys.modules,{"wechat_traffic_agent.cognition_context":context,"wechat_traffic_agent.x_goal_progress":progress}):
            result=runner._owner_agent_control(owner_goal={"goal_id":"grow-x"},now=NOW)
        return result,observer,runtime
    def test_engage_uses_reply_executor_without_forcing_a_reply(self):
        result,observer,runtime=self.dispatch("ENGAGE_X")
        observer.assert_awaited_once_with(now=NOW,allow_remote_actions=True)
        self.assertEqual(result["steps"][0]["result"]["x_social_observation"]["reply_count"],0)
        self.assertEqual(result["decision"]["next_action"],"SEARCH_WORLD")
        self.assertIn("JEV_AGENT_RUNTIME",runtime.prompts[0]);self.assertIn('"quotas_required": false',runtime.prompts[0])
    def test_check_remains_read_only(self):
        _,observer,_=self.dispatch("CHECK_X");observer.assert_awaited_once_with(now=NOW,allow_remote_actions=False)
    def write_browser_receipt(self,status):
        path=self.save("content/x/history.jsonl",{})
        path.write_text(json.dumps({"event_type":"EXPRESSION","cycle":"article-1","x_post_id":"123","runtime_receipt_id":None})+"\n")
        self.save("content/continuous/cycles/article-1/x_browser_publication.json",{"status":status,"post_id":"123"})
    def test_browser_receipt_is_visible_without_runtime_api_id(self):
        self.write_browser_receipt("POSTED")
        result=progress.owner_x_execution_context(self.root,configured=True)
        self.assertEqual(result["recent_publication_receipts"][0]["runtime_receipt_id"],"x-browser-publication:article-1:123")
    def test_unverified_is_not_progress(self):
        self.write_browser_receipt("PUBLICATION_UNVERIFIED")
        self.assertEqual(progress.owner_x_execution_context(self.root,configured=True)["recent_publication_receipts"],[])
    def test_advice_remains_optional_and_disabled_execution_is_visible(self):
        advice={"suggested_posts_per_day":20,"suggested_replies_per_day":200,"authority":"ADVISORY_ONLY","quotas_required":False}
        self.save("content/x/strategy_advice.json",advice)
        result=progress.owner_x_execution_context(self.root,configured=False)
        self.assertEqual(result["owner_strategy_advice"],advice);self.assertFalse(result["quotas_required"]);self.assertEqual(result["available_actions"],[])
    def test_corrupt_state_is_explicit_failure(self):
        self.save("content/x/status.json",{}).write_text("broken")
        with self.assertRaises(json.JSONDecodeError):progress.owner_x_execution_context(self.root,configured=True)

    def observe_case(self, publish):
        social=load("x_social",{
            "normalize_posts":lambda rows,observed_at:rows,
            "x_growth_mission_context":lambda root:{},
            "public_persona_projection":lambda root:{},
        })
        post={"evidence_id":"x-post:111","summary":"How do agents recover?","url":"https://x.com/reader/status/111"}
        loop=social.XAutonomyLoop(runtime=object(),model_runtime=object(),state_root=self.root,base_url="https://example.org",
            browser_mentions_observer=types.SimpleNamespace(observe=AsyncMock(return_value=[post])))
        loop._publication_identity=AsyncMock(return_value=({"username":"ferro"},"identity-receipt"))
        loop._activity=lambda **kw:None
        async def handler(**kw):
            if publish:
                loop.history.append({"event_type":"REPLY","cycle":kw["cycle"],"x_post_id":"999",
                    "x_url":"https://x.com/ferro/status/999","target_post_id":"111",
                    "runtime_receipt_id":"x-jev-reply:111:999","publication_verified":True})
            return True
        loop._handle_mention=handler
        return asyncio.run(loop.observe(now=NOW,allow_remote_actions=True))
    def test_observation_reports_actual_verified_reply(self):
        result=self.observe_case(True)
        self.assertEqual(result["reply_count"],1);self.assertEqual(result["engagement_outcome"],"REPLIED")
        self.assertEqual(result["replies"][0]["x_post_id"],"999")
        self.assertEqual(result["replies"][0]["target_post_id"],"111")
    def test_reviewed_discussion_is_not_reported_as_reply(self):
        result=self.observe_case(False)
        self.assertEqual(result["new_mentions"],1);self.assertEqual(result["reply_count"],0)
        self.assertEqual(result["engagement_outcome"],"NO_REPLY_SELECTED")

if __name__=="__main__":unittest.main()
