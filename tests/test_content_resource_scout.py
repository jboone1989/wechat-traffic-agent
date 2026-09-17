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
    assert len(runtime.calls)==3
    assert all(cap=="web.search" for cap,_ in runtime.calls)
    assert all("provider.example" not in payload["query"] for _,payload in runtime.calls)
    second=scout.maybe_discover(now=NOW)
    assert second["status"]=="NOT_DUE" and len(runtime.calls)==3
