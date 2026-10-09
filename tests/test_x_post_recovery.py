from wechat_traffic_agent.publication.cycle_records import read_cycle_record, cycle_record_exists
import json
import unittest
from pathlib import Path
import test_x_jev_transaction_native as native
from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
from wechat_traffic_agent.x_browser import XBrowserPublicationUnverified

class TestDurableShortPosts(unittest.TestCase):
    def setUp(self):
        native.install_clock(self)
        import tempfile
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)

    def transport(self, browser):
        t = LocalHarnessXTransport.__new__(LocalHarnessXTransport)
        t.state_root = self.root
        t.account_id = "default"
        t.expected_handle = "owner"
        t._run_jev_sync = lambda script, **kwargs: native.run(browser, script)
        return t

    def test_process_loss_after_submit_recovers_without_second_input(self):
        browser = native.Browser()
        original = browser.act
        def crash(action, page, text=None):
            if action["kind"] == "click":
                receipt = next((self.root / "content/x/post-transactions").glob("*.json"))
                saved = json.loads(receipt.read_text())
                assert saved["status"] == "SUBMIT_PENDING" and saved["attempted"]
                original(action, page, text)
                raise SystemExit("caller dies after click")
            original(action, page, text)
        browser.act = crash
        with self.assertRaises(SystemExit):
            self.transport(browser)._publish_sync("Hello from Ferro")
        resumed = self.transport(browser)._publish_sync("Hello from Ferro")
        assert resumed.post_id == "123"
        assert browser.actions == [("fill", 9), ("click", 20)]

    def test_unverified_publication_keeps_retries_observation_only(self):
        browser = native.Browser(verify=False)
        for _ in range(3):
            with self.assertRaises(XBrowserPublicationUnverified):
                self.transport(browser)._publish_sync("Hello from Ferro")
        assert browser.actions == [("fill", 9), ("click", 20)]

    def test_verified_publication_receipt_needs_no_more_browser_actions(self):
        browser = native.Browser()
        first = self.transport(browser)._publish_sync("Hello from Ferro")
        browser.evaluate = lambda _: self.fail("cached post must not touch browser")
        second = self.transport(browser)._publish_sync("Hello from Ferro")
        assert first.post_id == second.post_id == "123"

def test_interrupted_social_model_stage_is_retired_from_replay(tmp_path):
    from datetime import datetime, timezone
    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner
    runner = WakefulContentTaskRunner.__new__(WakefulContentTaskRunner)
    runner.state_root = tmp_path
    cycle = "v2-123"
    folder = tmp_path / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps({"status":"PUBLISHED", "cycle":cycle}))
    (folder / "cognition.json").write_text(json.dumps({"worth_saying":True}))
    result = runner._social_stage_error(
        ValueError("model call interrupted without receipt; skip this cycle"),
        stage="social_expression_drafts", continuous={"cycle":cycle},
        now=datetime.now(timezone.utc))
    assert result["status"] == "RECOVERY_INVALIDATED"
    assert runner._social_stage_backlog_candidate() is None
    assert (folder / "social_expression_drafts.error.json").exists()

def test_draft_history_keeps_full_public_text_without_private_lineage(tmp_path):
    from wechat_traffic_agent.x_social import XHistoryLedger
    history = XHistoryLedger(tmp_path)
    text = "A complete supported public observation."
    history.append({"event_type":"EXPRESSION", "cycle":"v2-1", "text":text,
                    "x_url":"https://x.com/owner/status/1",
                    "lineage":{"internal_context":"x"*70000}})
    projected = history.recent_public_shares()
    assert projected[0]["text"] == text
    assert "lineage" not in projected[0]
    assert len(json.dumps(projected)) < 1000
    assert history.recent()[0]["lineage"]["internal_context"] == "x"*70000

def test_long_share_history_has_an_explicit_budget_without_truncating_posts(tmp_path):
    from wechat_traffic_agent.x_social import XHistoryLedger
    history = XHistoryLedger(tmp_path)
    for number in range(3):
        history.append({"text":str(number)*9000, "x_url":f"https://x.com/owner/status/{number}"})
    rows = history.recent_public_shares()
    assert sum(len(row.get("text", "")) for row in rows) <= 16000
    assert rows[-1]["text"] == "2"*9000
    assert rows[0]["text_omitted_for_budget"] and rows[0]["text_sha256"]
    assert rows[0]["x_url"] == "https://x.com/owner/status/0"
    assert all(len(row["text"]) == 9000 for row in history.recent())


def test_unverified_publication_leaves_cognition_backlog_and_reconciles_in_check_x(tmp_path):
    from datetime import datetime, timezone
    from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

    cycle = "v2-unverified-x"
    folder = tmp_path / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps({"status":"HELD_BY_CHECKS","cycle":cycle}))
    (folder / "cognition.json").write_text(json.dumps({"worth_saying":True}))
    (folder / "x_social_expression.json").write_text(
        json.dumps({"status":"PUBLICATION_UNVERIFIED","cycle":cycle})
    )
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "status":"DRAFT_READY","cycle":cycle,
        "drafts":{"x":{"channel":"X","format":"SHORT_POST","text":"exact persisted text"}},
        "x_strategy":{"post_timing":"POST_NOW","interaction_mode":"ORIGINAL"},
    }))

    class Growth:
        def publication_binding(self, *, cycle):
            return {"status":"BOUND_X_EXPRESSION","bound":True,"cycle":cycle}

    class XLoop:
        growth_mission = Growth()

    class Publisher:
        def __init__(self): self.calls=[]
        async def after_content_cycle(self, *, cycle, now):
            self.calls.append((cycle, now))
            return {"status":"POSTED","cycle":cycle,"publication":{
                "post_id":"123","url":"https://x.com/owner/status/123","source":"JEV_AGENT_RUNTIME"
            }}

    publisher=Publisher()
    runner=WakefulContentTaskRunner.__new__(WakefulContentTaskRunner)
    runner.state_root=tmp_path
    runner.x_loop=XLoop()
    runner.x_browser_publisher=publisher

    assert runner._social_stage_backlog_candidate() is None
    now=datetime.now(timezone.utc)
    reconciled=runner._reconcile_x_publication_backlog(now=now)
    assert reconciled["status"]=="RECONCILED"
    assert publisher.calls==[(cycle,now)]
    receipt=read_cycle_record(folder/"x_social_expression.json")
    assert receipt["status"]=="POSTED"
    assert receipt["post_id"]=="123"
