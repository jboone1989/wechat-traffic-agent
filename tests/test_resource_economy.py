from datetime import datetime, timedelta, timezone

import pytest

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.resource_economy import BalanceObservation, ContentResourceEconomy

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _disable_chatgpt_only_by_default(monkeypatch):
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "0")
    monkeypatch.delenv("LIVINGRUNTIME_CHATGPT_ALLOW_FALLBACK", raising=False)


def balance(*, when=NOW, total=97.35, available=True, suffix="a"):
    return BalanceObservation(
        observed_at=when.isoformat(), is_available=available, currency="CNY",
        total_balance=total, granted_balance=0.0, topped_up_balance=total,
        evidence_ref="deepseek-balance:sha256:" + suffix * 64,
    )


def test_real_balance_observation_enters_persistent_virtualbrain_state(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(economy.observer, "observe", lambda now: balance(when=now))

    observed = economy.observe(NOW)
    current = mind.session.resource_awareness.current
    assert observed["status"] == "OBSERVED"
    assert current is not None
    assert current.available_funds == pytest.approx(97.35)
    assert current.period_budget == pytest.approx(100.0)
    assert current.carryover_funds is None
    assert current.spent_today is None
    assert current.observed_at == NOW.isoformat()

    restarted = ContentMind(root=tmp_path, subject_id="content-agent")
    restored = restarted.session.resource_awareness.current
    assert restored is not None and restored.available_funds == pytest.approx(97.35)
    assert restarted.public_projection()["resource_awareness"]["observation_count"] == 1


def test_cycle_decision_outcome_and_reflection_close_from_balance_delta(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((balance(when=NOW, total=97.35, suffix="a"),
                         balance(when=NOW + timedelta(minutes=20), total=97.10, suffix="b")))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))

    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="cycle-1")
    assert decision["decision"] == "EXECUTE"
    result = economy.end_cycle(
        now=NOW + timedelta(minutes=20), cycle="cycle-1",
        episode_id=decision["episode_id"], before_balance=decision["before_balance"],
        result={"status": "PUBLISHED"},
    )
    assert result["status"] == "RECORDED"
    assert result["actual_cost"] == pytest.approx(0.25)
    assert len(mind.session.resource_awareness.proposals) == 1
    assert len(mind.session.resource_awareness.episodes) == 1
    assert len(mind.session.resource_awareness.outcomes) == 1
    assert len(mind.session.resource_awareness.reflections) == 1
    assert mind.session.resource_awareness.current.estimated_daily_burn == pytest.approx(18.0)


def test_unavailable_or_underfunded_balance_asks_human_without_execution(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(economy.observer, "observe", lambda now: balance(when=now, total=0.2, available=False))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="cycle-low")
    assert decision["decision"] == "ASK_HUMAN"
    assert mind.session.resource_awareness.outcomes == ()


def test_balance_increase_is_not_misreported_as_negative_spend(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((balance(when=NOW, total=10.0, suffix="a"),
                         balance(when=NOW + timedelta(minutes=20), total=20.0, suffix="b")))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="cycle-topup")
    result = economy.end_cycle(
        now=NOW + timedelta(minutes=20), cycle="cycle-topup",
        episode_id=decision["episode_id"], before_balance=decision["before_balance"],
        result={"status": "PUBLISHED"},
    )
    assert result["status"] == "AMBIGUOUS_BALANCE_INCREASE"
    assert mind.session.resource_awareness.outcomes == ()
    assert mind.session.resource_awareness.current.estimated_daily_burn is None


def test_zero_or_noop_history_does_not_collapse_future_cost_estimate(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((balance(when=NOW, total=97.35, suffix="a"),
                         balance(when=NOW + timedelta(minutes=20), total=97.35, suffix="b")))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    first = economy.begin_cycle(now=NOW, cycle="cycle-zero")
    economy.end_cycle(now=NOW + timedelta(minutes=20), cycle="cycle-zero",
                      episode_id=first["episode_id"], before_balance=first["before_balance"],
                      result={"status": "ALREADY_PROCESSED"})
    second = economy.begin_cycle(now=NOW + timedelta(minutes=21), cycle="cycle-next")
    assert second["estimated_cost"] == pytest.approx(economy.bootstrap_cycle_estimate)


def test_burn_uses_older_window_when_observations_are_every_five_minutes(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((
        balance(when=NOW, total=100.0, suffix="a"),
        balance(when=NOW + timedelta(minutes=5), total=99.9, suffix="b"),
        balance(when=NOW + timedelta(minutes=10), total=99.8, suffix="c"),
        balance(when=NOW + timedelta(minutes=15), total=99.7, suffix="d"),
    ))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    economy.observe(NOW + timedelta(minutes=5))
    economy.observe(NOW + timedelta(minutes=10))
    final = economy.observe(NOW + timedelta(minutes=15))
    assert final["resource_state"]["estimated_daily_burn"] == pytest.approx(28.8)
    assert final["resource_state"]["runway_days"] == pytest.approx(99.7 / 28.8)


def test_vb_activates_opportunity_feast_for_verified_free_frontier_resource(tmp_path, monkeypatch):
    import json
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(
        economy.observer,
        "observe",
        lambda now: balance(when=now,total=20.0,available=True,suffix="w"),
    )
    pool=tmp_path/"application"/"llm_resource_pool"
    pool.mkdir(parents=True)
    (pool/"resources.json").write_text(json.dumps({
        "resources":[{
            "resource_id":"frontier-free",
            "pricing_class":"FREE",
            "windfall_eligible":True,
            "capability_tier":"FRONTIER",
            "capability_score":0.97,
            "expires_at":None,
        }]
    }))

    result=economy.observe(NOW)

    policy=result["llm_routing_policy"]
    assert policy["pressure"]=="CONSERVE"
    assert policy["windfall_active"] is True
    assert policy["windfall_mode"]=="OPPORTUNITY_FEAST"
    assert policy["windfall_resource_count"]==1
    saved=json.loads((pool/"policy.json").read_text())
    assert saved["windfall_resource_ids"]==["frontier-free"]
    assert "self_upgrade" in saved["high_value_task_classes"]
    assert saved["windfall_bonus"]==pytest.approx(0.30)


def test_verified_free_route_executes_when_paid_balance_sensor_is_unknown(tmp_path, monkeypatch):
    import json
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    def unavailable(_now):
        raise RuntimeError("provider unreachable")
    monkeypatch.setattr(economy.observer, "observe", unavailable)
    monkeypatch.setenv("TEST_FREE_API_KEY", "present")
    pool = tmp_path / "application" / "llm_resource_pool"
    pool.mkdir(parents=True)
    (pool / "resources.json").write_text(json.dumps({
        "observed_at": NOW.isoformat(),
        "resources": [{
            "resource_id": "verified-free", "pricing_class": "FREE",
            "credential_env": "TEST_FREE_API_KEY", "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 5, "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }],
    }))
    observed = economy.observe(NOW)
    assert observed["status"] == "PARTIALLY_OBSERVED"
    assert observed["balance"] is None
    decision = economy.begin_cycle(now=NOW, cycle="free-cycle")
    assert decision["decision"] == "EXECUTE"
    result = economy.end_cycle(now=NOW, cycle="free-cycle", episode_id=decision["episode_id"],
                               before_balance=decision["before_balance"], result={"status": "PUBLISHED"})
    assert result["status"] == "RECORDED_COST_UNKNOWN"
    outcome = mind.session.resource_awareness.outcomes[-1]
    assert "cost-sensor:unknown" in outcome.evidence_refs
    assert "cost unknown" in outcome.result_summary


def test_publication_without_measured_downstream_value_does_not_invent_value(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((balance(when=NOW, total=10.0, suffix="a"),
                         balance(when=NOW + timedelta(minutes=20), total=9.9, suffix="b")))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="unmeasured-publication")
    economy.end_cycle(now=NOW + timedelta(minutes=20), cycle="unmeasured-publication",
                      episode_id=decision["episode_id"], before_balance=decision["before_balance"],
                      result={"status": "PUBLISHED"})
    outcome = mind.session.resource_awareness.outcomes[-1]
    assert outcome.external_value == 0
    assert outcome.learning_value == 0
    assert outcome.reproduction_value == 0


def test_negative_paid_balance_keeps_raw_debt_but_free_route_can_execute(tmp_path, monkeypatch):
    import json
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    negative = BalanceObservation(
        observed_at=NOW.isoformat(),
        is_available=False,
        currency="CNY",
        total_balance=-0.02,
        granted_balance=0.0,
        topped_up_balance=-0.02,
        evidence_ref="deepseek-balance:sha256:" + "n" * 64,
    )
    monkeypatch.setattr(economy.observer, "observe", lambda now: negative)
    monkeypatch.setenv("TEST_FREE_API_KEY", "present")
    pool = tmp_path / "application" / "llm_resource_pool"
    pool.mkdir(parents=True)
    (pool / "resources.json").write_text(json.dumps({
        "observed_at": NOW.isoformat(),
        "resources": [{
            "resource_id": "verified-free",
            "pricing_class": "FREE",
            "credential_env": "TEST_FREE_API_KEY",
            "metrics_source": "EMPIRICAL_TRIALS",
            "quota_remaining": 5,
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }],
    }))

    observed = economy.observe(NOW)
    current = mind.session.resource_awareness.current

    assert observed["balance"]["total_balance"] == pytest.approx(-0.02)
    assert observed["balance"]["topped_up_balance"] == pytest.approx(-0.02)
    assert current is not None
    assert current.available_funds == 0.0
    assert current.quotas["deepseek_account_debt"] == pytest.approx(0.02)
    assert current.quotas["deepseek_topped_up_balance"] == 0.0

    decision = economy.begin_cycle(now=NOW, cycle="negative-paid-free-route")
    assert decision["decision"] == "EXECUTE"
    assert decision["available_funds"] == 0.0


def test_chatgpt_only_resource_state_ignores_deepseek_balance_for_cognition(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    cli = tmp_path / "cognitionctl.py"
    cli.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("LIVINGRUNTIME_CHATGPT_COGNITION_CLI", str(cli))
    monkeypatch.setattr(
        economy.observer,
        "observe",
        lambda _now: (_ for _ in ()).throw(
            AssertionError("DeepSeek balance must not be queried in ChatGPT-only mode")
        ),
    )

    observed = economy.observe(NOW)
    current = mind.session.resource_awareness.current
    projection = mind.public_projection()["resource_awareness"]["current"]

    assert observed["status"] == "CHATGPT_ONLY"
    assert observed["balance"] is None
    assert observed["balance_sensor"] == "NOT_APPLICABLE"
    assert current is not None
    assert current.quotas["chatgpt_cognition_authoritative"] == 1.0
    assert current.quotas["chatgpt_cognition_available"] == 1.0
    assert current.quotas["llm_eligible_free_route_count"] == 0.0
    assert projection["pressure"] == "NORMAL"
    assert projection["provider_available"] is True
    assert projection["provider_mode"] == "LIVINGRUNTIME_CHATGPT_ONLY"
    assert observed["llm_routing_policy"]["routing_mode"] == "CHATGPT_ONLY"
    assert observed["llm_routing_policy"]["prefer_free"] is False
    assert observed["llm_routing_policy"]["max_failovers"] == 0
    assert observed["llm_routing_policy"]["windfall_active"] is False

    decision = economy.begin_cycle(now=NOW, cycle="chatgpt-only-cycle")
    assert decision["decision"] == "EXECUTE"
    assert decision["estimated_cost"] == 0.0
