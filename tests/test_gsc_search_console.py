from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from wechat_traffic_agent.gsc_search_console import GoogleSearchConsoleCollector
from wechat_traffic_agent.traffic_learning import SearchDemandStore

NOW = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
ACCOUNT = "https://livingruntime.com"
PROPERTY = "sc-domain:livingruntime.com"


def _collector(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleSearchConsoleCollector(
        state_root=tmp_path, account=ACCOUNT, property_url=PROPERTY,
        client_id="client-id", client_secret="client-secret", refresh_token="refresh-secret",
        refresh_hours=6, lookback_days=7, client=client,
    )


def test_collects_finalized_query_page_rows_and_skips_recent_refresh(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == "https://oauth2.googleapis.com/token":
            assert b"refresh_token=refresh-secret" in request.content
            return httpx.Response(200, json={"access_token": "access-token"})
        body = json.loads(request.content)
        assert body == {
            "startDate": "2026-09-08", "endDate": "2026-09-14",
            "dimensions": ["date", "query", "page"], "type": "web",
            "dataState": "final", "rowLimit": 25000, "startRow": 0,
        }
        assert request.headers["Authorization"] == "Bearer access-token"
        assert "sc-domain%3Alivingruntime.com" in str(request.url)
        return httpx.Response(200, json={"rows": [{
            "keys": ["2026-09-12", "content agent learning", ACCOUNT + "/articles/example/"],
            "clicks": 3, "impressions": 30, "ctr": 0.1, "position": 4.5,
        }]})

    collector = _collector(tmp_path, handler)
    first = collector.collect(now=NOW)
    assert first["status"] == "RECORDED"
    assert first["rows_seen"] == 1 and first["rows_recorded"] == 1
    assert first["data_state"] == "final"
    second = collector.collect(now=NOW + timedelta(hours=1))
    assert second["status"] == "SKIPPED_RECENT"
    assert len(requests) == 2
    rows = SearchDemandStore(tmp_path / "content/search_demand").as_of(NOW + timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].revision == 1
    assert rows[0].query == "content agent learning"
    assert rows[0].ctr == 0.1
    assert rows[0].window_start.utcoffset() is not None


def test_identical_measurement_is_stable_and_changed_metric_gets_revision_two(tmp_path):
    search_calls = 0

    def handler(request):
        nonlocal search_calls
        if str(request.url) == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        search_calls += 1
        clicks = 2 if search_calls < 3 else 4
        return httpx.Response(200, json={"rows": [{
            "keys": ["2026-09-12", "agent runtime", ACCOUNT + "/articles/example/"],
            "clicks": clicks, "impressions": 20, "ctr": clicks / 20, "position": 6.0,
        }]})

    collector = _collector(tmp_path, handler)
    assert collector.collect(now=NOW)["rows_recorded"] == 1
    same = collector.collect(now=NOW + timedelta(hours=7))
    assert same["rows_recorded"] == 0 and same["rows_unchanged"] == 1
    changed = collector.collect(now=NOW + timedelta(hours=14))
    assert changed["rows_recorded"] == 1
    rows = SearchDemandStore(tmp_path / "content/search_demand").as_of(NOW + timedelta(hours=15))
    assert len(rows) == 1
    assert rows[0].revision == 2
    assert rows[0].clicks == 4 and rows[0].impressions == 20
    key_dir = next((tmp_path / "content/search_demand").glob("*/"))
    assert sorted(p.name for p in key_dir.glob("*.json")) == ["00001.json", "00002.json"]


def test_oauth_error_is_sanitized_and_does_not_persist_sync_receipt(tmp_path):
    def handler(request):
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        return httpx.Response(400, json={"error": "bad refresh-secret client-secret"})

    collector = _collector(tmp_path, handler)
    with pytest.raises(RuntimeError) as caught:
        collector.collect(now=NOW)
    message = str(caught.value)
    assert "refresh-secret" not in message
    assert "client-secret" not in message
    assert "HTTP 400" in message
    assert not (tmp_path / "content/search_demand/gsc_sync.json").exists()


def test_invalid_fractional_clicks_fail_closed(tmp_path):
    def handler(request):
        if str(request.url) == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(200, json={"rows": [{
            "keys": ["2026-09-12", "query", ACCOUNT + "/articles/example/"],
            "clicks": 1.5, "impressions": 20, "position": 3.0,
        }]})

    collector = _collector(tmp_path, handler)
    with pytest.raises(RuntimeError, match="non-negative integers"):
        collector.collect(now=NOW)


def test_runner_refreshes_gsc_before_content_strategy_selection():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "scripts/run_content_agent.py").read_text(encoding="utf-8")
    runner = (root / "src/wechat_traffic_agent/wakeful_tasks.py").read_text(encoding="utf-8")
    assert "GoogleSearchConsoleCollector" in entrypoint
    assert runner.index('gsc_collector.collect(now=now)') < runner.index('continuous_loop.run_once(')
