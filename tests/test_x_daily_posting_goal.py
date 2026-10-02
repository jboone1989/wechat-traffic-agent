import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
from wechat_traffic_agent.x_social import XHistoryLedger
from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW = datetime(2026, 10, 2, 20, tzinfo=timezone.utc)

def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")

def configured(root):
    goal = DailyPostingGoal(root)
    put(goal.config_path, {"enabled":True,"minimum_original_posts":20,"timezone":"Asia/Shanghai",
        "activated_at":NOW.isoformat(),"notification_since":(NOW-timedelta(hours=1)).isoformat(),
        "notifications_enabled":True,"operator_state_dir":str(root/"operator")})
    return goal

def post(root, number, now=NOW, fmt="SHORT_POST", status="POSTED", event="EXPRESSION"):
    cycle=f"cycle-{number}"
    receipt={"status":status,"format":fmt,"post_id":str(number),"url":f"https://x.com/ferro/status/{number}",
        "published_at":now.isoformat()}
    put(root/"content/continuous/cycles"/cycle/"x_browser_publication.json", receipt)
    XHistoryLedger(root).append({"cycle":cycle,"event_type":event,"recorded_at":now.isoformat(),
        "x_post_id":str(number),"x_url":receipt["url"]})
    return receipt

def test_daily_count_uses_verified_originals_unique_ids_and_shanghai_midnight(tmp_path):
    goal=configured(tmp_path)
    post(tmp_path,1,NOW,fmt="ARTICLE")
    post(tmp_path,1,NOW,fmt="ARTICLE")
    post(tmp_path,2,NOW,status="PUBLICATION_UNVERIFIED")
    post(tmp_path,3,NOW,event="REPLY")
    post(tmp_path,4,datetime(2026,10,2,15,59,tzinfo=timezone.utc))
    assert goal.status(NOW)["published"]==1
    assert goal.status(NOW)["date"]=="2026-10-03"
    assert goal.status(datetime(2026,10,3,16,tzinfo=timezone.utc))["published"]==0

def test_mailbox_queue_is_not_delivery_and_replay_does_not_duplicate(tmp_path):
    goal=configured(tmp_path)
    post(tmp_path,1)
    first=goal.reconcile(NOW)
    assert first["deliveries"][0]["status"]=="ENQUEUED"
    note_id=first["deliveries"][0]["notification_id"]
    notes=tmp_path/"operator/mailbox/notifications"
    assert len(list(notes.glob("*.json")))==1
    note=json.loads(next(notes.glob("*.json")).read_text())
    assert "https://x.com/ferro/status/1" in note["message"]
    goal.reconcile(NOW+timedelta(seconds=60))
    assert len(list(notes.glob("*.json")))==1
    put(tmp_path/"operator/operator/deliveries"/f"notification__{note_id}__wecom.json",
        {"status":"DELIVERED","delivered_at":NOW.isoformat()})
    assert goal.reconcile(NOW+timedelta(seconds=90))["deliveries"][0]["status"]=="DELIVERED"
    assert goal.reconcile(NOW+timedelta(seconds=120))["deliveries"]==[]

def test_minimum_met_resets_next_day_without_stopping_autonomous_posts(tmp_path, monkeypatch):
    # Only the external wake envelope is replaced; exercise the real quota scheduler.
    class Wake:
        def __init__(self, **values): self.__dict__.update(values)
    monkeypatch.setitem(sys.modules, "wechat_traffic_agent.wakeful_runtime", SimpleNamespace(PlannedWake=Wake))
    goal=configured(tmp_path)
    class Core:
        def __init__(self): self.plans=[]
        def snapshot(self): return SimpleNamespace(pending_plans=self.plans)
        def schedule(self,wake): self.plans=[wake]
        def cancel(self,_): self.plans=[]
    core=Core()
    wakeful=SimpleNamespace(core=core)
    goal.schedule(wakeful,NOW)
    assert core.plans[0].at==NOW
    assert not core.plans[0].preemptible
    for n in range(1,22): post(tmp_path,n)
    assert goal.owner_goal(NOW) is None
    goal.schedule(wakeful,NOW)
    assert core.plans[0].at==datetime(2026,10,3,16,tzinfo=timezone.utc)
    assert goal.status(NOW)["published"]==21

def test_disabling_temporary_minimum_preserves_link_notifications(tmp_path):
    goal=configured(tmp_path)
    config=json.loads(goal.config_path.read_text());config["enabled"]=False;put(goal.config_path,config)
    assert goal.owner_goal(NOW) is None
    post(tmp_path,9)
    assert goal.reconcile(NOW)["deliveries"][0]["status"]=="ENQUEUED"

def test_unproductive_retries_become_a_human_alert(tmp_path):
    goal=configured(tmp_path)
    for n in range(3):
        goal.observe("CONTENT_COGNITION",{"status":"OK","continuous_content":{"status":"COGNITION_ONLY"}},NOW+timedelta(minutes=n*5))
    goal.reconcile(NOW+timedelta(minutes=10))
    notes=list((tmp_path/"operator/mailbox/notifications").glob("*.json"))
    assert len(notes)==1
    assert "需要协助" in json.loads(notes[0].read_text())["title"]
    progress=json.loads((goal.root/"progress.json").read_text())
    assert progress["consecutive_no_publication"]==3
    assert progress["next_attempt_at"]==(NOW+timedelta(minutes=15)).isoformat()

def test_blocking_error_is_not_lost_when_outer_result_is_ok(tmp_path):
    goal=configured(tmp_path)
    goal.observe("CONTENT_COGNITION",{"status":"OK","x_social_expression":
        {"status":"X_PUBLICATION_CAPABILITY_UNAVAILABLE","detail":"X session needs login"}},NOW)
    result=goal.reconcile(NOW)
    assert len(result["deliveries"])==1

def test_independent_monitor_detects_a_dead_service(tmp_path):
    goal=configured(tmp_path)
    put(tmp_path/"application/ferro-heartbeat.json",{"observed_at":(NOW-timedelta(minutes=6)).isoformat()})
    goal.reconcile(NOW,watch_heartbeat=True)
    notes=list((tmp_path/"operator/mailbox/notifications").glob("*.json"))
    assert len(notes)==1
    assert "心跳" in json.loads(notes[0].read_text())["message"]

def test_required_daily_goal_supersedes_older_promotional_goal(tmp_path):
    configured(tmp_path)
    runner=WakefulContentTaskRunner.__new__(WakefulContentTaskRunner)
    runner.state_root=tmp_path
    runner.app=SimpleNamespace(domain_runtime=SimpleNamespace(active_goals=[]))
    goal=runner._active_owner_goal_context(now=NOW)
    assert goal["goal_id"]=="ferro-daily-post:2026-10-03"
    assert goal["daily_posting_contract"]["minimum"]==20
    assert "加密货币" in goal["description"]

def test_reusing_an_existing_post_does_not_reset_stalled_production(tmp_path):
    goal=configured(tmp_path)
    for n in range(3):
        goal.observe("CONTENT_COGNITION", {"status":"OK", "x_social_expression":{"status":"ALREADY_EXPRESSED"}}, NOW+timedelta(minutes=5*n))
    goal.reconcile(NOW+timedelta(minutes=10))
    note=json.loads(next((tmp_path/"operator/mailbox/notifications").glob("*.json")).read_text())
    assert "需要协助" in note["title"]

def test_publication_survives_more_than_500_later_history_events(tmp_path):
    goal=configured(tmp_path)
    post(tmp_path,1)
    for n in range(501):
        XHistoryLedger(tmp_path).append({"cycle":"reply-"+str(n),"event_type":"REPLY","x_post_id":str(100+n)})
    assert goal.status(NOW)["published"]==1
    assert len(goal.reconcile(NOW)["deliveries"])==1

def test_quantity_rule_off_keeps_real_error_notifications(tmp_path):
    goal=configured(tmp_path)
    config=json.loads(goal.config_path.read_text()); config["enabled"]=False; put(goal.config_path,config)
    goal.observe("CHECK_OUTCOMES",{"status":"ERROR","message":"X browser session unavailable"},NOW)
    notes=goal.reconcile(NOW)["deliveries"]
    assert len(notes)==1
    note=json.loads(next((tmp_path/"operator/mailbox/notifications").glob("*.json")).read_text())
    assert "X browser session unavailable" in note["message"]

def test_met_goal_does_not_raise_shortfall_from_later_unproductive_cognition(tmp_path):
    goal=configured(tmp_path)
    for n in range(20): post(tmp_path,n)
    for n in range(3): goal.observe("CONTENT_COGNITION",{"status":"OK"},NOW+timedelta(minutes=5*n))
    assert not list((goal.root/"notifications").glob("*.json"))

def test_provider_unavailable_notifies_on_first_failed_attempt(tmp_path):
    goal=configured(tmp_path)
    goal.observe("CONTENT_COGNITION",{"status":"CHATGPT_UNAVAILABLE","error_type":"AgentRuntimeChatGPTProviderError","message":"model browser request timed out"},NOW)
    assert len(goal.reconcile(NOW)["deliveries"])==1
