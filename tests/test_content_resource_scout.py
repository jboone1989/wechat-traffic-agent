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
