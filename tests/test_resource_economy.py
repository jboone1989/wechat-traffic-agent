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
    assert result["settlement_status"] == "PENDING_AUDIENCE"
    assert len(mind.session.resource_awareness.proposals) == 1
    assert len(mind.session.resource_awareness.episodes) == 1
    assert len(mind.session.resource_awareness.outcomes) == 1
    assert mind.session.resource_awareness.outcomes[0].external_value is None
    assert mind.session.resource_awareness.outcomes[0].learning_value is None
    assert mind.session.resource_awareness.outcomes[0].reproduction_value is None
    assert len(mind.session.resource_awareness.reflections) == 1
    assert mind.session.resource_awareness.current.estimated_daily_burn == pytest.approx(18.0)


def test_critical_balance_defers_and_enters_hibernation(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(economy.observer, "observe", lambda now: balance(when=now, total=0.2, available=False))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="cycle-low")
    assert decision["decision"] == "DEFER"
    assert decision["pressure"] == "CRITICAL"
    assert decision["hibernation"]["status"] == "HIBERNATE"
    assert economy.hibernation_state()["allowed_tasks"] == ["RESOURCE_REVIEW", "CHECK_OUTCOMES"]
    assert mind.session.resource_awareness.outcomes == ()


def test_balance_increase_is_not_misreported_as_negative_spend(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((balance(when=NOW, total=60.0, suffix="a"),
                         balance(when=NOW + timedelta(minutes=20), total=70.0, suffix="b")))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="cycle-topup")
    result = economy.end_cycle(
        now=NOW + timedelta(minutes=20), cycle="cycle-topup",
        episode_id=decision["episode_id"], before_balance=decision["before_balance"],
        result={"status": "PUBLISHED"},
    )
    assert result["status"] == "AMBIGUOUS_BALANCE_INCREASE"
    assert result["balance_increase_event"]["source"] == "UNKNOWN"
    assert result["balance_increase_event"]["classification"] == "UNRESOLVED"
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


def test_next_cycle_uses_only_world_settled_value(tmp_path, monkeypatch):
    from virtualbrain.research.resource_awareness import OutcomeSettlement

    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((
        balance(when=NOW, total=60.0, suffix="a"),
        balance(when=NOW + timedelta(minutes=20), total=59.5, suffix="b"),
    ))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    first = economy.begin_cycle(now=NOW, cycle="world-1")
    economy.end_cycle(
        now=NOW + timedelta(minutes=20), cycle="world-1",
        episode_id=first["episode_id"], before_balance=first["before_balance"],
        result={"status": "PUBLISHED"},
    )
    outcome = mind.session.resource_awareness.outcomes[-1]
    mind.session.resource_awareness.record_outcome_settlement(OutcomeSettlement(
        settlement_id="settled-1", outcome_id=outcome.outcome_id, revision=1,
        external_value=0.9, learning_value=None, reproduction_value=None,
        observed_tick=int(mind.session.tick), evidence_refs=("audience:24h",),
    ))
    mind.persist_resource_awareness()

    second = economy.begin_cycle(now=NOW + timedelta(minutes=21), cycle="world-2")
    proposal = next(
        row for row in mind.session.resource_awareness.proposals
        if row.proposal_id == second["proposal_id"]
    )
    assert proposal.expected_external_value == pytest.approx(0.9)
    assert proposal.expected_learning_value == pytest.approx(0.5)
    assert proposal.expected_reproduction_value == pytest.approx(0.5)


def test_conserve_defers_without_verified_free_windfall(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(
        economy.observer, "observe",
        lambda now: balance(when=now, total=20.0, available=True, suffix="c"),
    )
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="conserve-no-free")
    assert decision["pressure"] == "CONSERVE"
    assert decision["decision"] == "DEFER"
    assert decision["defer_interval_multiplier"] == 2
    assert economy.hibernation_state() is None


def test_conserve_executes_only_with_verified_free_windfall(tmp_path, monkeypatch):
    import json

    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    monkeypatch.setattr(
        economy.observer, "observe",
        lambda now: balance(when=now, total=20.0, available=True, suffix="f"),
    )
    pool = tmp_path / "application" / "llm_resource_pool"
    pool.mkdir(parents=True)
    (pool / "resources.json").write_text(json.dumps({
        "resources": [{
            "resource_id": "free-test",
            "pricing_class": "FREE",
            "windfall_eligible": True,
            "expires_at": None,
        }]
    }))
    economy.observe(NOW)
    decision = economy.begin_cycle(now=NOW, cycle="conserve-free")
    assert decision["pressure"] == "CONSERVE"
    assert decision["windfall_active"] is True
    assert decision["prefer_free"] is True
    assert decision["decision"] == "EXECUTE"


def test_resource_recovery_exits_hibernation_with_receipt(tmp_path, monkeypatch):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    economy = ContentResourceEconomy(state_root=tmp_path, mind=mind)
    observations = iter((
        balance(when=NOW, total=0.2, available=False, suffix="x"),
        balance(when=NOW + timedelta(hours=1), total=80.0, available=True, suffix="y"),
    ))
    monkeypatch.setattr(economy.observer, "observe", lambda now: next(observations))
    economy.observe(NOW)
    first = economy.begin_cycle(now=NOW, cycle="critical-before-recovery")
    assert first["decision"] == "DEFER"
    assert economy.hibernation_state() is not None

    economy.observe(NOW + timedelta(hours=1))
    second = economy.begin_cycle(
        now=NOW + timedelta(hours=1), cycle="after-resource-recovery"
    )
    assert second["pressure"] in {"NORMAL", "WATCH"}
    assert economy.hibernation_state() is None
    receipts = list(
        (tmp_path / "application/resource_observations/hibernation_recovery_receipts").glob("*.json")
    )
    assert receipts
