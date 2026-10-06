import json,subprocess,hashlib
from pathlib import Path
import pytest
from wechat_traffic_agent.x_jev_transaction import jev_x_reconcile
from test_x_jev_transaction_native import Browser,run,install_clock
import unittest

class TestSourceCard(unittest.TestCase):
    def setUp(self):install_clock(self)

    def test_exact_body_requires_exact_source_destination_and_only_retires_own_target(self):
        source="https://source.example/primary-data"
        body="A concrete worked calculation with definitions and explained comparisons. "*8
        text=body+"\n"+source
        for case in ["valid","other_page","missing_card","changed_body","foreign_opener"]:
            class CardBrowser(Browser):
                target="x-owned"
                def __init__(self):super().__init__(existing=True);self.opened=False;self.closed=[]
                def evaluate(self,expr):
                    if "slice(0,20)" in expr:return [{"text":body[:260],"hrefs":["/owner/status/456"]}]
                    if "const normalized=text.replace" in expr:return body+(" extra claim" if case=="changed_body" else "")
                    if "scrollIntoView" in expr:return case!="missing_card"
                    if "__jevFast?.nodes.get" in expr:return {"href":"https://t.co/observed"}
                    return super().evaluate(expr)
                def observe(self,screenshot=False):return {"page_key":"card","actions":[{"kind":"click","node":38,"role":"link"}]}
                def act(self,action,page,text=None):
                    assert action=={"kind":"click","node":38,"role":"link"}
                    self.opened=True
                def call(self,method,**params):
                    if method=="Target.getTargets":
                        rows=[{"targetId":"x-owned","url":self.url},{"targetId":"operator","url":source}]
                        if self.opened:rows.append({"targetId":"source-created",
                            "openerId":"operator" if case=="foreign_opener" else self.target,
                            "url":"https://source.example/another-page" if case=="other_page" else source})
                        return {"targetInfos":rows}
                    if method=="Target.closeTarget":
                        assert params["targetId"]=="source-created"
                        self.closed.append(params["targetId"]);return {"success":True}
                    return super().call(method,**params)
            browser=CardBrowser()
            row=run(browser,jev_x_reconcile(text,"owner"))
            if case=="valid":
                assert row["status"]=="POSTED" and row["published_text"]==body
                assert row["verified_links"]==[{"url":source,"href":"https://t.co/observed","rendering":"LINK_CARD"}]
            else:assert row["status"]=="PUBLICATION_UNVERIFIED"
            assert browser.closed==(["source-created"] if case in {"valid","other_page"} else [])
            assert browser.submitted is False

def test_transport_timeout_after_submit_only_reconciles(tmp_path,monkeypatch):
    from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
    transport=LocalHarnessXTransport.__new__(LocalHarnessXTransport)
    transport.state_root=tmp_path;transport.account_id="default";transport.expected_handle="owner"
    text="original accepted post";calls=[]
    digest=hashlib.sha256(json.dumps(["default","owner",text],ensure_ascii=False).encode()).hexdigest()
    checkpoint=tmp_path/"content/x/post-transactions"/(digest+".json")
    def execute(script,refresh_target=False):
        calls.append((script,refresh_target))
        if len(calls)==1:
            value=json.loads(checkpoint.read_text());value.update(status="SUBMIT_PENDING",attempted=True)
            checkpoint.write_text(json.dumps(value))
            raise subprocess.TimeoutExpired("browser-harness",75)
        assert refresh_target is True
        assert "verification_only" in script and "stage = 'composer'" not in script
        return {"status":"POSTED","post_id":"456","url":"https://x.com/owner/status/456","handle":"owner",
                "published_text":text,"verified_links":[],"verification_only":True}
    monkeypatch.setattr(transport,"_run_jev_sync",execute)
    result=transport._publish_transaction_sync(text)
    assert result.post_id=="456" and result.observed_text==text
    assert len(calls)==2 and json.loads(checkpoint.read_text())["attempted"]

def test_publisher_history_keeps_observed_body_and_verified_links(tmp_path):
    import asyncio
    from datetime import datetime,timezone
    from test_x_browser import _draft
    from wechat_traffic_agent.x_browser import XBrowserPublisher,XBrowserPublishResult
    from wechat_traffic_agent.x_social import XHistoryLedger
    folder=_draft(tmp_path,text="Actual readable body.\nhttps://source.example/primary")
    class Transport:
        async def publish(self,**kwargs):
            return XBrowserPublishResult(post_id="456",url="https://x.com/owner/status/456",handle="owner",
                observed_text="Actual readable body.",verified_links=({"url":"https://source.example/primary","rendering":"LINK_CARD"},))
    result=asyncio.run(XBrowserPublisher(state_root=tmp_path,transport=Transport(),enabled=True,writes_enabled=True)
        .after_content_cycle(cycle="v2-browser",now=datetime.now(timezone.utc)))
    assert result["publication"]["published_text"]=="Actual readable body."
    assert result["publication"]["verified_links"][0]["url"]=="https://source.example/primary"
    assert XHistoryLedger(tmp_path).recent(limit=1)[0]["text"]=="Actual readable body."
