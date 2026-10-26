"""Smoke script test: event poll -> story fetch -> source-bound evidence, stdlib HTTP only."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_eventgraph_evidence as smoke

STORY_ID = "fb2ad078-6323-4b9a-a0d6-9b17d2680001"
REF = "eventgraph:" + STORY_ID
HOT = {
    "schemaVersion": 1, "count": 1,
    "items": [{
        "rank": 1, "title": "Keystone data breach",
        "links": {"story": "http://localhost/story/" + STORY_ID},
        "latestAt": "2026-10-26T02:24:04.000Z",
    }],
}
STORY = {
    "story": {
        "publicId": STORY_ID, "title": "Keystone data breach",
        "latestAt": "2026-10-26T02:24:04.000Z",
        "reports": [{
            "id": "report-1", "title": "Keystone data breach",
            "summary": "Breach exposed data promised deleted.",
            "source": {"name": "The Block"},
            "links": {"original": "https://www.theblock.co/news/keystone"},
            "publishedAt": "2026-10-26T02:24:04.000Z",
        }],
    },
}


class Handler(BaseHTTPRequestHandler):
    story_status = 200

    def do_GET(self):
        if self.path == "/api/v1/hot-topics":
            body, status = json.dumps(HOT).encode(), 200
        elif self.path == "/api/v1/stories/" + STORY_ID:
            body, status = json.dumps(STORY).encode(), Handler.story_status
        else:
            body, status = b"not found", 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SmokeEventgraphEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_event_becomes_source_bound_evidence(self):
        report = smoke.run_smoke(self.base_url)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["steps"]["poll"]["queued"], [REF])
        self.assertEqual(report["steps"]["replay"], {"status": "UNCHANGED", "queued": []})
        step = report["steps"][REF]
        self.assertEqual(step["topic"], "Keystone data breach")
        self.assertEqual(step["source_count"], 1)
        self.assertEqual(report["steps"]["evidence_total"], 1)

    def test_story_read_failure_reports_fail_reason(self):
        Handler.story_status = 500
        try:
            with self.assertRaises(Exception) as raised:
                smoke.run_smoke(self.base_url)
            self.assertIn("HTTP Error 500", str(raised.exception))
        finally:
            Handler.story_status = 200


if __name__ == "__main__":
    unittest.main()
