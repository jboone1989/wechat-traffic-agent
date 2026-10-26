#!/usr/bin/env python3
"""Smoke: a changed EventGraph story becomes usable research evidence.

Reads only: hot-topics poll + one story fetch, against the local dev API.
Uses a temp checkpoint and a no-op bus so real agent state is untouched.
No model calls, no publication. Stdlib only (fetch injected as callables
so eventgraph_context never imports httpx).
"""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.eventgraph_context import load_eventgraph_context, poll_new_stories

DEFAULT_BASE_URL = "http://127.0.0.1:3000"


def stdlib_get_json(base_url, path):
    # Accepts the optional identifier eventgraph_context passes to story fetches.
    def fetch(_identifier=None):
        request = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=12.0) as response:
            return json.loads(response.read().decode("utf-8"))
    return fetch


def run_smoke(base_url):
    report = {"base_url": base_url, "steps": {}}
    with tempfile.TemporaryDirectory(prefix="eventgraph-smoke-") as tmp:
        # Step 1: event side — poll changed stories into queue refs (temp state, no-op bus).
        poll = poll_new_stories(
            Path(tmp), SimpleNamespace(emit=lambda *a, **k: None),
            fetch=stdlib_get_json(base_url, "/api/v1/hot-topics"),
        )
        report["steps"]["poll"] = poll
        if not poll["queued"]:
            raise RuntimeError("hot-topics poll queued no story; nothing to smoke")

        # Replay on the same checkpoint: unchanged stories must not re-queue.
        replay = poll_new_stories(
            Path(tmp), SimpleNamespace(emit=lambda *a, **k: None),
            fetch=stdlib_get_json(base_url, "/api/v1/hot-topics"),
        )
        if replay["queued"]:
            raise RuntimeError("replayed poll re-queued unchanged stories: " + ", ".join(replay["queued"]))
        report["steps"]["replay"] = replay

        # Step 2: evidence side — each queued event ref must load source-bound evidence.
        evidence_rows = []
        for ref in poll["queued"]:
            story_id = ref.split(":", 1)[1]
            context = load_eventgraph_context(
                (ref,), base_url=base_url,
                fetch=stdlib_get_json(base_url, "/api/v1/stories/" + story_id),
            )
            if not context or context["story_ref"] != ref or not context["topic"]:
                raise RuntimeError("context load returned unusable result for " + ref)
            for row in context["evidence"]:
                if not row["url"].startswith(("https://", "http://")) or ref not in row["evidence_refs"]:
                    raise RuntimeError("evidence row is not source-bound to " + ref)
                evidence_rows.append(row)
            report["steps"][ref] = {"topic": context["topic"], "source_count": context["source_count"]}
        if not evidence_rows:
            raise RuntimeError("no source-linked evidence produced")
        report["steps"]["evidence_total"] = len(evidence_rows)
    report["status"] = "PASS"
    return report


def main():
    base_url = (os.environ.get("EVENTGRAPH_API_BASE_URL") or DEFAULT_BASE_URL).strip()
    try:
        report = run_smoke(base_url)
    except (RuntimeError, ValueError, urllib.error.URLError, OSError) as error:
        report = {"base_url": base_url, "status": "FAIL", "reason": f"{type(error).__name__}: {error}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
