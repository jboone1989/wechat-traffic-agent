"""Ferro must not store completed reasoning inputs or duplicate metric snapshots."""
from wechat_traffic_agent.publication.cycle_records import read_cycle_record, cycle_record_exists
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import runpy

from wechat_traffic_agent.website_engagement import (
    WebsiteEngagementOutcomeCollector, WebsiteEngagementStore,
)

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def _touch_old(path, days=9):
    old = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (old, old))


def test_prune_keeps_unfinished_work_and_referenced_source(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/prune_redundant_ferro_state.py"
    prune = runpy.run_path(str(script))["prune"]
    cycles = tmp_path / "content/continuous/cycles"
    for name, status in (("done", "PUBLISHED"), ("pending", None),
                         ("error", "ERROR"), ("young", "PUBLISHED")):
        folder = cycles / name
        folder.mkdir(parents=True)
        input_path = folder / "inputs.json"
        input_path.write_text('{"large":"unused context"}')
        if status:
            result = folder / "result.json"
            result.write_text(json.dumps({"status": status}))
            _touch_old(result)
        if name != "young":
            _touch_old(input_path)
    source = tmp_path / "content/channel_evidence_sources"
    source.mkdir(parents=True)
    for name in ("keep", "discard", "fresh", "unknown"):
        path = source / (name + ".json")
        schema = "website-engagement-snapshot.v1" if name != "unknown" else "other.raw-evidence.v1"
        path.write_text(json.dumps({"schema_version": schema}))
        if name != "fresh":
            _touch_old(path)
    from test_channel_evidence import row
    from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore
    evidence = tmp_path / "content/channel_evidence"
    ChannelEvidenceStore(evidence).record(
        row(evidence_refs=("website-engagement-snapshot:sha256:keep",))
    )

    first = prune(tmp_path, now=NOW, limit=99, apply=False)
    assert first["file_count"] == {
        "finished_cycle_inputs": 2,
        "unused_website_metric_snapshots": 1,
    }
    assert (cycles / "done/inputs.json").exists()
    changed = prune(tmp_path, now=NOW, limit=99, apply=True)
    assert changed["file_count"] == first["file_count"]
    assert not (cycles / "done/inputs.json").exists()
    assert not (cycles / "error/inputs.json").exists()
    assert (cycles / "pending/inputs.json").exists()
    assert (cycles / "young/inputs.json").exists()
    assert (source / "keep.json").exists()
    assert (source / "fresh.json").exists()
    assert (source / "unknown.json").exists()
    assert not (source / "discard.json").exists()


def test_engagement_outcome_collector_never_writes_duplicate_source_snapshot(tmp_path):
    root = tmp_path / "state"
    T = datetime(2026, 9, 15, tzinfo=timezone.utc)
    WebsiteEngagementStore(root / "content/website_engagement").mark_started(
        observed_at=T - timedelta(days=1),
    )
    cycle = root / "content/continuous/cycles/c1"
    cycle.mkdir(parents=True)
    (cycle / "publication.json").write_text(json.dumps({
        "revision": "pub-1", "artifact_id": "a",
        "outcome_attribution": {
            "published_at": T.isoformat(), "public_route": "/articles/a/",
            "published_content_sha256": "a" * 64,
            "agent_candidate_sha256": "a" * 64,
            "topic": "agents", "title_version": "title:v1",
            "structure_version": "structure:v1",
            "strategy_version": "strategy:v1",
        },
    }))
    collector = WebsiteEngagementOutcomeCollector(
        state_root=root, account="https://livingruntime.com"
    )
    initial = collector.collect(now=T + timedelta(days=8))
    sources = root / "content/channel_evidence_sources"
    count = len(list(sources.glob("*.json")))
    assert initial["status"] == "RECORDED"
    assert count == 3
    replay = collector.collect(now=T + timedelta(days=8, hours=1))
    assert replay["status"] == "NO_MATURE_WINDOWS"
    assert len(list(sources.glob("*.json"))) == count


def test_finished_content_cycle_discards_its_large_input_snapshot(tmp_path):
    import asyncio
    from test_content_loop import setup
    _, loop, publisher = setup(tmp_path)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    cycle = tmp_path / "state/content/continuous/cycles" / result["cycle"]
    assert (cycle / "result.json").is_file()
    assert not (cycle / "inputs.json").exists()
    assert cycle_record_exists(cycle / "publication.json")


def test_old_retryable_error_drops_input_but_keeps_diagnostic(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/prune_redundant_ferro_state.py"
    prune = runpy.run_path(str(script))["prune"]
    for label, days in (("abandoned", 9), ("recent", 1)):
        folder = tmp_path / "content/continuous/cycles" / label
        folder.mkdir(parents=True)
        (folder / "inputs.json").write_text("large transient context")
        (folder / "result.json").write_text(json.dumps({"status": "ERROR", "retryable": True}))
        (folder / "error_diagnostic.json").write_text(json.dumps({"error_type": "TransientFailure"}))
        for name in ("inputs.json", "result.json"):
            _touch_old(folder / name, days=days)
    result = prune(tmp_path, now=NOW, limit=10, apply=True)
    assert result["file_count"]["finished_cycle_inputs"] == 1
    assert not (tmp_path / "content/continuous/cycles/abandoned/inputs.json").exists()
    assert (tmp_path / "content/continuous/cycles/abandoned/result.json").exists()
    assert (tmp_path / "content/continuous/cycles/abandoned/error_diagnostic.json").exists()
    assert (tmp_path / "content/continuous/cycles/recent/inputs.json").exists()
