from unittest.mock import patch
from wechat_traffic_agent import x_jev_transaction as transaction
import ast
import socket
import subprocess
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
import tempfile

def install_clock(test):
    import time
    tick = [0.0]
    test.enterContext(patch.object(time, 'monotonic', lambda: tick[0]))
    test.enterContext(patch.object(time, 'sleep', lambda seconds: tick.__setitem__(0, tick[0] + seconds)))
    module = types.ModuleType('jev_ultrafast.browser')
    module.StalePage = type('StalePage', (ValueError,), {})
    test.enterContext(patch.dict(sys.modules, {'jev_ultrafast.browser': module}))
    return tick

class Browser:

    def __init__(self, *, handle='owner', existing=False, fill_ok=True, verify=True, late=False):
        self.handle, self.existing, self.fill_ok, self.verify, self.late = (handle, existing, fill_ok, verify, late)
        self.url = 'https://x.com/home'
        self.text = ''
        self.submitted = False
        self.actions = []

    def call(self, method, **params):
        assert method == 'Page.navigate'
        self.url = params['url']
        if self.late:
            raise TimeoutError('late navigation acknowledgement')
        return {}

    def evaluate(self, expression):
        if 'AppTabBar_Profile_Link' in expression:
            return '/' + self.handle
        if 'ready:document.readyState' in expression:
            return {'url': self.url, 'ready': 'complete'}
        if 'Boolean(document.querySelector("article"))' in expression:
            return True
        if 'slice(0,20)' in expression:
            if self.existing or (self.submitted and self.verify):
                return [{'text': 'Hello from Ferro', 'hrefs': ['/owner/status/123']}]
            return []
        if '__jevFast?.nodes.get' in expression:
            return self.text if self.fill_ok else ''
        if '/verified_followers' in expression:
            return '1,796 Followers'
        raise AssertionError('unexpected read: ' + expression)

    def observe(self, screenshot=False):
        assert self.url == 'https://x.com/compose/post'
        return {'page_key': 'live', 'actions': [{'kind': 'fill', 'role': 'textbox', 'label': 'Search', 'node': 1}, {'kind': 'fill', 'role': 'textbox', 'label': 'Post text', 'node': 9}, {'kind': 'click', 'role': 'button', 'label': 'Post', 'node': 20}]}

    def act(self, action, page, text=None):
        self.actions.append((action['kind'], action['node']))
        if action['kind'] == 'fill':
            assert action['node'] == 9
            self.text = text
        else:
            assert action['node'] == 20
            self.submitted = True

def run(browser, script):
    scope = {'browser': browser}
    exec(compile(script, 'jev-transaction', 'exec'), scope)
    return scope['result']

class TestJevXRepair(unittest.TestCase):

    def setUp(self):
        self.clock = install_clock(self)

    def test_observed_full_page_composer_publishes_and_verifies(self):
        clock = self.clock
        browser = Browser()
        row = run(browser, transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'POSTED' and row['url'] == 'https://x.com/owner/status/123'
        assert row['executor'] == 'JEV_AGENT_RUNTIME'
        assert browser.actions == [('fill', 9), ('click', 20)]

    def test_navigation_acknowledgement_timeout_reconciles_destination(self):
        clock = self.clock
        row = run(Browser(late=True), transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'POSTED'

    def test_wrong_account_cannot_submit(self):
        clock = self.clock
        browser = Browser(handle='someone_else')
        row = run(browser, transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'FAILED' and row['stage'] == 'identity'
        assert browser.actions == []

    def test_unpreserved_text_cannot_submit(self):
        clock = self.clock
        browser = Browser(fill_ok=False)
        row = run(browser, transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'FAILED' and row['stage'] == 'fill'
        assert not browser.submitted

    def test_duplicate_is_reconciled_without_second_submit(self):
        clock = self.clock
        browser = Browser(existing=True)
        row = run(browser, transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['duplicate_found'] and row['status'] == 'POSTED'
        assert browser.actions == []

    def test_submit_without_public_url_is_explicitly_unverified(self):
        clock = self.clock
        row = run(Browser(verify=False), transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'PUBLICATION_UNVERIFIED' and row['attempted']
        assert 'url' not in row

    def test_identity_reads_live_browser_profile_without_api(self):
        clock = self.clock
        row = run(Browser(), transaction.jev_x_identity('owner'))
        assert row['status'] == 'IDENTITY_OBSERVED'
        assert row['user']['public_metrics']['followers_count'] == 1796

    def test_stale_observation_is_read_again_before_input(self):
        browser = Browser()
        original = browser.act
        stale = sys.modules['jev_ultrafast.browser'].StalePage
        calls = [0]

        def act(action, page, text=None):
            calls[0] += 1
            if calls[0] == 1:
                raise stale('page changed before dispatch')
            return original(action, page, text)
        browser.act = act
        row = run(browser, transaction.jev_x_transaction('Hello from Ferro', 'owner'))
        assert row['status'] == 'POSTED'
        assert browser.actions == [('fill', 9), ('click', 20)]

    def test_folded_long_post_is_verified_on_its_observed_status_without_resubmit(self):
        text = "A supported recovery explanation: " + "aligned states and preserved failure knowledge " * 8
        class FoldedBrowser(Browser):
            def evaluate(self, expression):
                if 'slice(0,20)' in expression:
                    return [{"text": text[:260], "hrefs": ["/owner/status/long-incorrect", "/owner/status/456"]}]
                if 'const path=' in expression and 'const normalized=text.replace' in expression:
                    assert self.url == "https://x.com/owner/status/456"
                    return text
                return super().evaluate(expression)
        browser = FoldedBrowser(existing=True)
        row = run(browser, transaction.jev_x_reconcile(text, "owner"))
        assert row["status"] == "POSTED" and row["post_id"] == "456"
        assert row["verification_only"] and browser.actions == []

    def test_matching_folded_prefix_does_not_accept_different_full_text(self):
        text = "A supported recovery explanation: " + "aligned states and preserved failure knowledge " * 8
        class WrongFullText(Browser):
            def evaluate(self, expression):
                if 'slice(0,20)' in expression:
                    return [{"text": text[:260], "hrefs": ["/owner/status/456"]}]
                if 'const path=' in expression and 'const normalized=text.replace' in expression:
                    return None
                return super().evaluate(expression)
        browser = WrongFullText(existing=True)
        row = run(browser, transaction.jev_x_reconcile(text, "owner"))
        assert row["status"] == "PUBLICATION_UNVERIFIED"
        assert browser.actions == [] and "url" not in row
