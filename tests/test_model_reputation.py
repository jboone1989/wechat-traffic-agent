from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.model_reputation import ExternalModelReputationObserver


NOW = datetime(2026, 9, 18, 6, 30, tzinfo=timezone.utc)


def test_external_reputation_is_built_from_live_source_shapes_not_static_model_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    observer = ExternalModelReputationObserver(state_root=tmp_path)

    def fake(url, *, api_key=None):
        if "benchmarks" in url:
            assert api_key == "secret"
            return {
                "data": [
                    {
                        "source": "artificial-analysis",
                        "model_permaslug": "lab/model-a-20260901",
                        "intelligence_index": 50,
                        "coding_index": 40,
                        "agentic_index": 30,
                    },
                    {
                        "source": "artificial-analysis",
                        "model_permaslug": "lab/model-b-20260901",
                        "intelligence_index": 30,
                        "coding_index": 55,
                        "agentic_index": 50,
                    },
                ],
                "meta": {"as_of": "2026-09-18T00:00:00Z"},
            }
        if "rankings-daily" in url:
            return {
                "data": [
                    {"date": "2026-09-17", "model_permaslug": "lab/model-a", "total_tokens": "100"},
                    {"date": "2026-09-17", "model_permaslug": "lab/model-b", "total_tokens": "1000"},
                ],
                "meta": {"as_of": "2026-09-18T01:00:00Z"},
            }
        if "classifications/task" in url:
            return {
                "data": {
                    "as_of": "2026-09-18",
                    "classifications": [
                        {
                            "tag": "code:general_impl",
                            "models": [
                                {"id": "lab/model-b", "tag_usage_share": 0.8},
                            ],
                        }
                    ],
                }
            }
        return {
            "full": {
                "model-a": {"rating": 1500},
                "model-b": {"rating": 1400},
            },
            "coding": {
                "model-a": {"rating": 1300},
                "model-b": {"rating": 1550},
            },
            "chinese": {
                "model-a": {"rating": 1510},
                "model-b": {"rating": 1450},
            },
        }

    monkeypatch.setattr(observer, "_get_json", fake)
    result = observer.observe(now=NOW)

    assert result["status"] == "OBSERVED"
    assert result["model_count"] == 2
    book = observer.book()
    assert book.rank(["provider/model-a", "provider/model-b"], task_class="general_text")[0] == "provider/model-a"
    assert book.rank(["provider/model-a", "provider/model-b"], task_class="code_audit")[0] == "provider/model-b"
    raw = observer.path.read_text()
    assert "secret" not in raw
    assert "artificial-analysis" in raw
    assert "lmarena" in raw


def test_reputation_refresh_is_periodic(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    observer = ExternalModelReputationObserver(state_root=tmp_path)
    calls = []

    def fake(url, *, api_key=None):
        calls.append(url)
        if "benchmarks" in url:
            return {"data": [], "meta": {"as_of": NOW.isoformat()}}
        if "rankings-daily" in url:
            return {"data": [], "meta": {"as_of": NOW.isoformat()}}
        if "classifications/task" in url:
            return {"data": {"as_of": "2026-09-18", "classifications": []}}
        return {}

    monkeypatch.setattr(observer, "_get_json", fake)
    first = observer.observe(now=NOW)
    second = observer.observe(now=NOW + timedelta(hours=1))
    third = observer.observe(now=NOW + timedelta(hours=7))

    assert first["status"] == "OBSERVED"
    assert second["status"] == "NOT_DUE"
    assert third["status"] == "OBSERVED"
    assert len(calls) == 8


def test_source_failure_degrades_one_leaderboard_without_erasing_others(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    observer = ExternalModelReputationObserver(state_root=tmp_path)

    def fake(url, *, api_key=None):
        if "benchmarks" in url:
            raise RuntimeError("AA unavailable")
        if "rankings-daily" in url:
            return {
                "data": [
                    {"date": "2026-09-17", "model_permaslug": "lab/model-a", "total_tokens": "10"},
                    {"date": "2026-09-17", "model_permaslug": "lab/model-b", "total_tokens": "20"},
                ],
                "meta": {"as_of": NOW.isoformat()},
            }
        if "classifications/task" in url:
            return {"data": {"as_of": "2026-09-18", "classifications": []}}
        return {"full": {"model-a": {"rating": 1500}, "model-b": {"rating": 1400}}}

    monkeypatch.setattr(observer, "_get_json", fake)
    result = observer.observe(now=NOW)

    assert result["source_status"]["artificial_analysis"] == "RuntimeError"
    assert result["source_status"]["openrouter_usage"] == "OK"
    assert result["source_status"]["lmarena"] == "OK"
    assert observer.book().get("model-a") is not None
