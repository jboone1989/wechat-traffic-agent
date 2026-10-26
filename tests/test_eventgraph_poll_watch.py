"""Regression for the dev-only bounded EventGraph poll watch.

Stdlib only: drives scripts/poll_eventgraph_watch.py as a subprocess against a
local fake EventGraph API. Verifies isolation (explicit state root), the real
FerroStateEventBus enqueue, checkpoint dedup across checks, the --max-checks
bound, and prompt SIGTERM termination between checks.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "poll_eventgraph_watch.py"
STORY_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REF = "eventgraph:" + STORY_ID
FEED = {"items": [{
    "title": "Shipping route closed",
    "latestAt": "2026-10-26T03:00:00Z",
    "links": {"story": "https://example.org/stories/" + STORY_ID},
}]}


class FakeApi(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/v1/hot-topics":
            self.send_error(404)
            return
        body = json.dumps(FEED).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class EventGraphPollWatchTests(unittest.TestCase):
    def run_watch(self, state_root, *, max_checks, interval, timeout=60):
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--state-root", str(state_root),
             "--max-checks", str(max_checks), "--interval", str(interval),
             "--base-url", self.base_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        return process.returncode, stdout, stderr

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_two_checks_queue_once_into_real_isolated_bus(self):
        with TemporaryDirectory(prefix="watch-test-") as folder:
            root = Path(folder)
            code, stdout, stderr = self.run_watch(root, max_checks=2, interval=0)
            self.assertEqual(code, 0, stderr)
            lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            checks = [row for row in lines if "check" in row]
            summary = [row for row in lines if row.get("summary")][0]
            self.assertEqual(len(checks), 2)
            self.assertEqual(checks[0]["queued"], [REF])
            self.assertEqual(checks[0]["status"], "QUEUED")
            self.assertEqual(checks[1]["queued"], [])  # same story, no re-queue
            self.assertEqual(checks[1]["status"], "UNCHANGED")
            self.assertEqual(summary["checks_completed"], 2)
            self.assertEqual(summary["stop_reason"], "completed")
            self.assertEqual(summary["unique_queued"], [REF])
            self.assertEqual(summary["events_enqueued"], 1)

            # Real FerroStateEventBus files exist only inside the isolated root.
            pending = sorted((root / "application" / "state_events" / "pending").glob("*.json"))
            self.assertEqual(len(pending), 1)
            event = json.loads(pending[0].read_text(encoding="utf-8"))
            self.assertEqual(event["kind"], "NETWORK_EVIDENCE")
            self.assertEqual(event["payload"]["evidence_ref"], REF)
            seen = json.loads((root / "application" / "eventgraph_seen.json").read_text(encoding="utf-8"))
            self.assertEqual(seen, {STORY_ID: "2026-10-26T03:00:00Z"})

    def test_sigterm_stops_between_checks_and_reports(self):
        with TemporaryDirectory(prefix="watch-term-") as folder:
            root = Path(folder)
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--state-root", str(root),
                 "--max-checks", "100", "--interval", "30",
                 "--base-url", self.base_url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            lines = []
            reader = threading.Thread(
                target=lambda: lines.extend(
                    line for line in process.stdout.readline().splitlines() if line),
                daemon=True,
            )
            reader.start()
            deadline = time.monotonic() + 30
            while not lines and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(lines, "watch printed no first-check line")
            process.send_signal(signal.SIGTERM)
            stdout_rest, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr + (stdout_rest or ""))
            reader.join(timeout=5)
            parsed = [json.loads(line) for line in lines if line.strip()] + \
                     [json.loads(line) for line in (stdout_rest or "").splitlines() if line.strip()]
            summary = [row for row in parsed if row.get("summary")][0]
            self.assertEqual(summary["stop_reason"], "interrupted")
            self.assertLess(summary["checks_completed"], 100)
            self.assertGreaterEqual(summary["checks_completed"], 1)
            self.assertTrue((root / "application").exists(), "isolated state must survive for inspection")


if __name__ == "__main__":
    unittest.main()
