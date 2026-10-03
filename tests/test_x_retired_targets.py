import json
from types import SimpleNamespace
import pytest
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
from wechat_traffic_agent.browser_runtime import BrowserRuntimeUnavailable
def transport(tmp_path):
    t=LocalHarnessXTransport.__new__(LocalHarnessXTransport)
    t.state_root=tmp_path/"state";t.account_id="default"
    t.browser=SimpleNamespace(runtime=SimpleNamespace(state_dir=tmp_path/"bindings"),_RESULT_PREFIX="RESULT:")
    t.browser.runtime.state_dir.mkdir();t.browser._load_target_id=lambda session:"new"
    return t
@pytest.mark.parametrize("case",["present","already_closed","referenced","active"])
def test_retirement_resumes_and_preserves_live_bindings(tmp_path,case):
    t=transport(tmp_path);path=t._retirement_path();path.parent.mkdir()
    old="new" if case=="active" else "old";path.write_text(json.dumps({"target_ids":[old]}))
    if case=="referenced":(t.browser.runtime.state_dir/"other.json").write_text(json.dumps({"target_id":"old"}))
    closed=[]
    def run(session,script,**kw):
        rows=[{"targetId":"new","url":"https://x.com/home"},{"targetId":"operator","url":"https://x.com/home"},
              {"targetId":"model","url":"https://chatgpt.com/c/owned"}]
        if case!="already_closed":rows.append({"targetId":"old","url":"https://x.com/home"})
        def cdp(method,**params):
            assert method=="Target.closeTarget";closed.append(params["targetId"]);return {"success":True}
        out=[];exec(script,{"json":json,"list_tabs":lambda **kw:rows,"cdp":cdp,"print":out.append})
        return json.loads(out[-1][len("RESULT:"):])
    t.browser._run=run
    if case in {"referenced","active"}:
        with pytest.raises((BrowserRuntimeUnavailable,AssertionError)):t._retire_replaced_x_tabs()
        assert closed==[] and json.loads(path.read_text())["target_ids"]==[old]
    else:
        t._retire_replaced_x_tabs();t._retire_replaced_x_tabs()
        assert closed==(["old"] if case=="present" else [])
        assert json.loads(path.read_text())["target_ids"]==[]
def test_retirement_record_precedes_new_binding(tmp_path):
    t=transport(tmp_path);t.browser._load_target_id=lambda session:"old"
    t.browser._run=lambda *a,**kw:{"target_id":"new"};saved=[]
    def save(session,target):
        assert json.loads(t._retirement_path().read_text())["target_ids"]==["old"];saved.append(target)
    t.browser._save_target_id=save;t._rebind_x_tab();assert saved==["new"]
def test_cleanup_after_successful_transaction(tmp_path):
    t=transport(tmp_path);calls=[]
    t._rebind_x_tab=lambda:calls.append("replace")
    t._run_jev_locked=lambda script:calls.append("transaction") or {"status":"POSTED"}
    t._retire_replaced_x_tabs=lambda:calls.append("cleanup")
    assert t._run_jev_sync("script",refresh_target=True)=={"status":"POSTED"}
    assert calls==["replace","transaction","cleanup"]
