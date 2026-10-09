from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('square_monitor', Path(__file__).parents[1] / 'scripts/monitor_square_posting.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
NOW = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def configure(root, monkeypatch):
    (root / '.binance-square-settings.json').write_text('{"enabled":true,"writes_enabled":true}')
    (root / '.binance-square-posting.key').write_text('test-key')
    (root / 'application').mkdir()
    (root / 'application/binance_square_worker.json').write_text(json.dumps({'status':'NO_READY_TASK','observed_at': NOW.isoformat()}))
    monkeypatch.setattr(monitor, 'unit_state', lambda name: {'ActiveState':'active', 'ExecMainStatus':'0'})


def test_success_and_no_ready_task_are_healthy(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    report = monitor.inspect(tmp_path, NOW)
    assert report['status'] == 'OK'
    assert report['engagement']['views'] is None
    assert report['engagement']['status'] == 'UNAVAILABLE'


def test_x_failures_do_not_degrade_square(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    failed = tmp_path / 'distribution/failed'
    failed.mkdir(parents=True)
    (failed / 'x.json').write_text('{"channel":"x","status":"FAILED_PERMANENT"}')
    assert monitor.inspect(tmp_path, NOW)['status'] == 'OK'


def test_stalled_unknown_submission_requires_reconciliation(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    processing = tmp_path / 'distribution/processing'
    processing.mkdir(parents=True)
    (processing / 'square.json').write_text(json.dumps({'channel':'binance_square', 'status':'RUNNING',
        'attempt_times': [(NOW - timedelta(minutes=4)).isoformat()]}))
    report = monitor.inspect(tmp_path, NOW)
    assert 'UNVERIFIED_PROCESSING_TASK_REQUIRES_RECONCILIATION' in report['issues']
    assert (processing / 'square.json').exists()


def test_timer_and_stale_worker_are_detected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, 'unit_state', lambda name: {'ActiveState':'inactive', 'ExecMainStatus':'0'})
    report = monitor.inspect(tmp_path, NOW + timedelta(minutes=6))
    assert {'CONTENT_PRODUCER_NOT_RUNNING','SQUARE_TIMER_NOT_RUNNING','SQUARE_WORKER_HEARTBEAT_STALE'} <= set(report['issues'])
