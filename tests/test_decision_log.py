from wechat_traffic_agent.decision_log import (
    DISCARD_STREAK_ALERT, append_decision, decision_log_path, discard_streak, recent_decisions,
)


def test_append_and_read_roundtrip(tmp_path):
    record = {"ts": "2026-10-13T00:00:00+00:00", "cycle": "c1", "genre": "deep_take",
              "review": {"verdict": "pass"}}
    append_decision(tmp_path, record)
    rows = recent_decisions(tmp_path)
    assert rows == [record]
    assert decision_log_path(tmp_path).name == "genre_decision_log.jsonl"


def test_discard_streak_counts_trailing_discards(tmp_path):
    for i in range(3):
        append_decision(tmp_path, {"review": {"verdict": "discarded"}, "i": i})
    assert discard_streak(tmp_path) == 3
    assert DISCARD_STREAK_ALERT == 3
    append_decision(tmp_path, {"review": {"verdict": "pass"}})
    assert discard_streak(tmp_path) == 0
    append_decision(tmp_path, {"review": {"verdict": "discarded"}})
    assert discard_streak(tmp_path) == 1


def test_missing_log_file_is_empty(tmp_path):
    assert recent_decisions(tmp_path) == []
    assert discard_streak(tmp_path) == 0


def test_corrupt_lines_are_skipped(tmp_path):
    path = decision_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken\n" + '{"review": {"verdict": "discarded"}}\n', encoding="utf-8")
    assert discard_streak(tmp_path) == 1


def test_non_object_json_lines_are_skipped(tmp_path):
    path = decision_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[1, 2, 3]\n{"review": {"verdict": "discarded"}}\n', encoding="utf-8")
    assert recent_decisions(tmp_path) == [{"review": {"verdict": "discarded"}}]
    assert discard_streak(tmp_path) == 1
