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
