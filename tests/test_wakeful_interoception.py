from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


class _Wakeful:
    def __init__(self):
        self.signals = []
        self.reflections = []

    def emit_interoceptive_fault(self, fault, *, observed_at=None):
        self.signals.append((dict(fault), observed_at))
        return {
            "status": "OBSERVED",
            "episode_id": fault.get("episode_id"),
            "magnitude": fault.get("severity"),
        }

    def schedule_reflection(self, now, *, seconds, reason, relevant_refs=()):
        row = {
            "status": "SCHEDULED",
            "at": now.isoformat(),
            "seconds": seconds,
            "reason": reason,
            "relevant_refs": list(relevant_refs),
        }
        self.reflections.append(row)
        return row


def test_run_wrapper_turns_chronic_fallback_into_neural_signal_and_diagnosis(tmp_path):
    wakeful = _Wakeful()
    runner = WakefulContentTaskRunner(state_root=tmp_path, wakeful=wakeful)

    cycles = iter(["c1", "c2", "c3"])

    def run_unobserved(*_args, **_kwargs):
        return {
            "task": "CONTENT_COGNITION",
            "status": "OK",
            "cycle": next(cycles),
            "composition": {"status": "FALLBACK_USED"},
        }

    runner._run_unobserved = run_unobserved

    first = runner.run("CONTENT_COGNITION", now=NOW)
    second = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=1))
    third = runner.run("CONTENT_COGNITION", now=NOW + timedelta(minutes=2))

    assert first["interoception"]["primary_symptom"] == "fallback_dependency"
    assert second["interoception"]["severity"] > first["interoception"]["severity"]
    assert third["interoception"]["status"] in {"WATCH", "ILL"}
    assert len(wakeful.signals) == 3
    assert len(wakeful.reflections) == 1
    assert "diagnose felt runtime pathology" in wakeful.reflections[0]["reason"]
    assert third["maintenance_observation"]["status"] in {
        "RECORDED",
        "ALREADY_RECORDED",
    }
    assert runner.self_maintenance.review()["open_count"] >= 1
