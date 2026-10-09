#!/usr/bin/env python3
"""Inspect Square scheduling and receipts without posting or exposing keys."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wechat_traffic_agent.sources.live_cache import live_cache_root
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def unit_state(name):
    result = subprocess.run(['systemctl', 'show', name, '-p', 'LoadState', '-p', 'ActiveState',
                             '-p', 'SubState', '-p', 'ExecMainStatus', '-p', 'NRestarts'],
                            capture_output=True, text=True, timeout=5)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def inspect(root, now):
    distribution = root / 'distribution'
    flags = read_json(root / '.binance-square-settings.json')
    key = root / '.binance-square-posting.key'
    issues = []
    if not key.is_file() or key.stat().st_size == 0:
        issues.append('WAITING_FOR_KEY')
    if not flags.get('enabled') or not flags.get('writes_enabled'):
        issues.append('PUBLISHING_DISABLED')
    outbox = PublicationEventStore(root)
    states = {'queue': {'PENDING', 'RETRY_PENDING'}, 'processing': {'RUNNING'},
              'success': {'SUCCESS'}, 'failed': {'FAILED_PERMANENT'}}
    tasks = {name: [row for row in outbox.list_tasks() if row['status'] in allowed]
             for name, allowed in states.items()}
    tasks = {name: [row for row in rows if row.get('channel') == 'binance_square']
             for name, rows in tasks.items()}
    successful = [{'post_id': row.get('published_post_id'), 'url': row.get('published_url')}
                  for row in tasks['success'] if row.get('status') == 'SUCCESS']
    if tasks['failed']:
        issues.append('FAILED_TASK_REQUIRES_REVIEW')
    for task in tasks['processing']:
        attempts = task.get('attempt_times') or []
        if attempts and (now - datetime.fromisoformat(attempts[-1])).total_seconds() > 120:
            issues.append('UNVERIFIED_PROCESSING_TASK_REQUIRES_RECONCILIATION')
    units = {name: unit_state(name) for name in (
        'content-agent.service', 'ferro-square-distribution.timer', 'ferro-square-distribution.service',
    )}
    if units['content-agent.service'].get('ActiveState') != 'active':
        issues.append('CONTENT_PRODUCER_NOT_RUNNING')
    if units['ferro-square-distribution.timer'].get('ActiveState') != 'active':
        issues.append('SQUARE_TIMER_NOT_RUNNING')
    if units['ferro-square-distribution.service'].get('ExecMainStatus') not in ('0', None):
        issues.append('SQUARE_WORKER_FAILED')
    worker = read_json(root / 'application' / 'binance_square_worker.json')
    native = SquareDraftStore(root).list()
    editorial_counts = {status: sum(row.get('status') == status for row in native)
                        for status in ('DRAFT', 'REVIEWED', 'NEEDS_REVIEW', 'PUBLISHING',
                                       'SUCCESS', 'REJECTED', 'UNVERIFIED')}
    for row in native:
        if row.get('status') == 'SUCCESS':
            successful.append({'post_id': row.get('post_id'), 'url': row.get('url')})
        elif row.get('status') in ('UNVERIFIED', 'REJECTED'):
            issues.append('NATIVE_DRAFT_REQUIRES_REVIEW_' + row['status'])
        elif row.get('status') == 'PUBLISHING' and (
                now - datetime.fromisoformat(row['attempted_at'])).total_seconds() > 120:
            issues.append('UNVERIFIED_NATIVE_DRAFT_REQUIRES_RECONCILIATION')
    if worker.get('status') in ('DISABLED', 'WAITING_FOR_WRITES', 'WAITING_FOR_KEY'):
        issues.append('SQUARE_WORKER_' + worker['status'])
    if not worker.get('observed_at') or (now - datetime.fromisoformat(worker['observed_at'])).total_seconds() > 300:
        issues.append('SQUARE_WORKER_HEARTBEAT_STALE')
    market = read_json(live_cache_root(root) / 'binance_market_structure' / 'latest.json')
    if worker.get('status') == 'WAITING_FOR_EDITORIAL':
        if market.get('status') != 'OK':
            issues.append('BTC_ETH_MARKET_EVIDENCE_INCOMPLETE')
        elif (now - datetime.fromisoformat(market['observed_at'])).total_seconds() > 1200:
            issues.append('BTC_ETH_MARKET_EVIDENCE_STALE')
    return {
        'schema_version': 'ferro-square-health.v1', 'observed_at': now.isoformat(),
        'status': ('DEGRADED' if issues else 'WAITING_FOR_EDITORIAL'
                   if worker.get('status') == 'WAITING_FOR_EDITORIAL' else 'OK'),
        'issues': sorted(set(issues)),
        'editorial': {'mode': 'NATIVE_REVIEWED_DRAFTS', 'counts': editorial_counts,
                      'authoring_status': 'EXPLICIT_EDITORIAL_WORK'},
        'market_evidence': {'status': market.get('status', 'UNAVAILABLE'),
                            'observed_at': market.get('observed_at'), 'error': market.get('error')},
        'key_saved': key.is_file() and key.stat().st_size > 0,
        'writes_enabled': bool(flags.get('writes_enabled')), 'units': units,
        'event_count': len(outbox.list_events()),
        'tasks': {name: len(rows) for name, rows in tasks.items()},
        'confirmed_posts': successful, 'latest_worker': worker,
        'engagement': {'status': 'UNAVAILABLE', 'views': None, 'likes': None, 'comments': None,
                       'reason': 'No verified analytics transport is configured; missing metrics are not zero.'},
    }


def main():
    root = Path(os.environ.get('CONTENT_APPLICATION_ROOT', '/home/ubuntu/content-agent-state'))
    report = inspect(root, datetime.now(timezone.utc))
    destination = root / 'application' / 'binance_square_health.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True) + '\n')
    temporary.replace(destination)
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report['status'] == 'DEGRADED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
