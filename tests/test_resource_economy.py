from datetime import datetime, timedelta, timezone

import pytest

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.resource_economy import BalanceObservation, ContentResourceEconomy

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


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
