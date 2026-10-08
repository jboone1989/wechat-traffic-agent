"""Resource-review feature contracts with injected, in-process ports."""
from datetime import datetime, timezone

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


class FakeEconomy:
    state_root = None
    def observe(self, now):
        return {"llm_routing_policy": {"routing_mode": "MULTI_PROVIDER", "pressure": "WATCH"}}


class FakeMind:
    def public_projection(self):
        return {"resource_awareness": {"current": {"pressure": "WATCH"}}}


class FakeEvents:
    def __init__(self):
        self.emitted = []
    def emit(self, name, payload, *, observed_at):
        self.emitted.append((name, payload, observed_at))
        return {"status": "RECORDED"}


class FakeScout:
    def __init__(self):
        self.calls = []
    def maybe_discover(self, *, now):
        self.calls.append("discover")
        return {"status": "OK"}
    def verify_pending(self, *, now, limit):
        self.calls.append("verify")
        return {"status": "OK"}
    def expand_provider_leads(self, *, now, limit):
        self.calls.append("leads")
        return {"status": "OK"}
    def verify_provider_leads(self, *, now, limit):
        self.calls.append("providers")
        return {"status": "OK"}
    def plan_trials(self, *, now, limit):
        self.calls.append("plan")
        return {"plans": [], "available_credential_envs": [], "autonomous_work_pending": False}


def test_resource_review_can_run_without_trial_or_browser_transport(monkeypatch):
    from wechat_traffic_agent import runtime_backend
    monkeypatch.setattr(runtime_backend, "chatgpt_only_mode", lambda: True)
    events = FakeEvents()
    runner = WakefulContentTaskRunner(
        resource_economy=FakeEconomy(), content_mind=FakeMind(), state_events=events,
    )
    result = runner._resource(NOW)
    assert result["status"] == "OK"
    assert result["resource_scout"]["status"] == "SKIPPED_CHATGPT_ONLY"
    assert result["provider_trials"]["status"] == "SKIPPED_CHATGPT_ONLY"
    assert events.emitted == [("SET_RESOURCE_PRESSURE", {"pressure": "WATCH"}, NOW)]


def test_resource_plan_has_result_without_autonomous_followup(monkeypatch):
    from wechat_traffic_agent import runtime_backend
    monkeypatch.setattr(runtime_backend, "chatgpt_only_mode", lambda: False)
    scout = FakeScout()
    runner = WakefulContentTaskRunner(
        resource_economy=FakeEconomy(), content_mind=FakeMind(), resource_scout=scout,
        state_events=FakeEvents(),
    )
    result = runner._resource(NOW)
    assert result["status"] == "OK"
    assert result["provider_trial_plans"]["plans"] == []
    assert scout.calls == ["discover", "verify", "leads", "providers", "plan"]
    assert "provider_access_request" not in result


def test_ready_provider_trial_preserves_receipt_and_credential_resolution(monkeypatch):
    from wechat_traffic_agent import runtime_backend
    monkeypatch.setattr(runtime_backend, "chatgpt_only_mode", lambda: False)

    class ReadyScout(FakeScout):
        def plan_trials(self, *, now, limit):
            self.calls.append("plan")
            return {
                "plans": [{"status": "READY_TO_TRIAL", "provider_label": "Test"}],
                "available_credential_envs": [], "autonomous_work_pending": False,
            }
        def run_ready_trials(self, *, now, provider_limit):
            self.calls.append("trial")
            return {
                "providers": [{
                    "completed_count": 1, "credential_env": "TEST_KEY",
                    "trial_ids": ["trial-1"], "error_codes": [],
                }],
            }

    class Assistance:
        def __init__(self):
            self.seen = []
        def fulfill_matching(self, **kwargs):
            self.seen.append(kwargs)
            return [{"request_id": "request-1"}]

    scout = ReadyScout()
    assistance = Assistance()
    runner = WakefulContentTaskRunner(
        resource_economy=FakeEconomy(), content_mind=FakeMind(),
        resource_scout=scout, human_assistance=assistance,
        state_events=FakeEvents(),
    )
    result = runner._resource(NOW)
    assert result["status"] == "OK"
    assert result["provider_access_resolution"] == {
        "status": "FULFILLED", "request_ids": ["request-1"],
    }
    assert result["provider_trials"]["providers"][0]["trial_ids"] == ["trial-1"]
    assert scout.calls == ["discover", "verify", "leads", "providers", "plan", "trial"]
    assert assistance.seen[0]["evidence_refs"] == ("trial-1",)
