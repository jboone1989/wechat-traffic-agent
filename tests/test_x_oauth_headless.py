from __future__ import annotations

import subprocess

from wechat_traffic_agent.x_oauth_headless import XOAuthHeadlessManager


class StubbornProcess:
    def __init__(self):
        self.alive = True
        self.terminated = 0
        self.killed = 0
        self.waits = 0

    def poll(self):
        return None if self.alive else -9

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        self.waits += 1
        if self.alive:
            raise subprocess.TimeoutExpired("xurl", timeout)
        return -9

    def kill(self):
        self.killed += 1
        self.alive = False
def test_stop_process_escalates_to_kill_when_xurl_ignores_terminate():
    process = StubbornProcess()

    XOAuthHeadlessManager._stop_process(process)

    assert process.terminated == 1
    assert process.killed == 1
    assert process.alive is False
    assert process.waits == 2
