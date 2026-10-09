"""Real feedback loop: precommit method -> execute -> settle -> compare -> adapt."""
from pathlib import Path
import asyncio
import sqlite3

import pytest

from test_content_loop import NOW, setup
from wechat_traffic_agent.learning_transfer import LearningTransfer


CANDIDATE = {
    "procedure_id": "procedure:content-create-verify-publish",
    "variant_id": "retry-checker-once",
    "steps": ["select evidence", "draft", "check", "retry checker", "publish"],
    "policy_action": "RETRY_ONCE_SAFE",
}


def _publish(cycle, *, publication=True):
    return {
        "outcome_attribution": {"public_route": "/articles/" + cycle + "/"}
    } if publication else None


def test_trial_is_immutable_across_restart_and_requires_real_receipt(tmp_path):
    first = LearningTransfer(tmp_path)
    selected = first.assign(cycle="trial-a", topic="ai-agents", candidate=CANDIDATE)
    restarted = LearningTransfer(tmp_path)
    assert restarted.assign(cycle="trial-a", topic="ai-agents", candidate=None) == selected
    with pytest.raises(ValueError, match="task identity"):
        restarted.assign(cycle="trial-a", topic="market-data", candidate=CANDIDATE)
    premature = restarted.settle(
        cycle="trial-a", outcome="PUBLISHED", evidence_refs=["source:1"],
    )
    assert premature["status"] == "UNVERIFIED"
    assert premature["comparison"]["eligible_comparisons"] == 0
    assert restarted.settle(
        cycle="trial-a", outcome="PUBLISHED", evidence_refs=["source:1"],
    )["status"] == "ALREADY_SETTLED"
    with pytest.raises(ValueError, match="cannot be overwritten"):
        restarted.settle(
            cycle="trial-a", outcome="HELD_BY_CHECKS",
            verified_checks=True, evidence_refs=["source:1"],
        )
    assert restarted.comparison(topic="ai-agents", candidate=CANDIDATE)["decision"] == "INSUFFICIENT_EVIDENCE"


def _settle_trials(store, *, topic, better):
    arms = {"BASELINE": 0, "LEARNED": 0}
    i = 0
    while min(arms.values()) < 8:
        cycle = topic + "-" + str(i)
        assignment = store.assign(cycle=cycle, topic=topic, candidate=CANDIDATE)
        arm = assignment["arm"]
        is_success = (arm == "LEARNED") if better else (arm == "BASELINE")
        result = store.settle(
            cycle=cycle,
            outcome="PUBLISHED" if is_success else "HELD_BY_CHECKS",
            publication_receipt=_publish(cycle) if is_success else None,
            verified_checks=not is_success,
            evidence_refs=["receipt:" + cycle],
        )
        assert result["status"] == "MEASURED"
        arms[arm] += 1
        i += 1
        assert i < 300
    return arms


def test_real_outcome_feedback_changes_later_method_assignments(tmp_path):
    ledger = LearningTransfer(tmp_path)
    arms = _settle_trials(ledger, topic="ai-agents", better=True)
    assert arms["BASELINE"] >= 8 and arms["LEARNED"] >= 8
    comparable = ledger.comparison(topic="ai-agents", candidate=CANDIDATE)
    assert comparable["decision"] == "PREFER_LEARNED"
    assert comparable["observed_success_delta"] > .15
    assert comparable["causal_improvement_proven"] is False
    # Same type, but a different topic cannot borrow an untested success.
    assert ledger.comparison(topic="btc-risk", candidate=CANDIDATE)["decision"] == "INSUFFICIENT_EVIDENCE"
    chosen = [
        ledger.assign(cycle="future-" + str(i), topic="ai-agents", candidate=CANDIDATE)["arm"]
        for i in range(30)
    ]
    assert chosen.count("LEARNED") > chosen.count("BASELINE")
    # Bad evidence changes future selection the other way.
    other = LearningTransfer(tmp_path / "other")
    _settle_trials(other, topic="agent-code", better=False)
    assert other.comparison(topic="agent-code", candidate=CANDIDATE)["decision"] == "PREFER_BASELINE"
    chosen = [
        other.assign(cycle="bad-future-" + str(i), topic="agent-code", candidate=CANDIDATE)["arm"]
        for i in range(30)
    ]
    assert chosen.count("BASELINE") > chosen.count("LEARNED")


def test_goal_loop_precommits_a_method_and_links_its_checked_result(tmp_path):
    from test_evidence_floor_goal_loop import NOW, worker
    _, loop = worker(tmp_path)
    result = asyncio.run(loop.run_once(now=NOW))
    assert result["status"] == "DRAFT_CREATED"
    assert result["learning_transfer"]["status"] == "UNVERIFIED"
    db = tmp_path / "state/content/learning/transfer.sqlite3"
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT cycle,arm,outcome,success,selected_json,evidence_json FROM trials"
        ).fetchone()
    assert row[1] == "BASELINE"
    assert row[2] == "DRAFT_CREATED"
    assert row[3] is None
    assert row[4] and row[5]
    # Prior experience selection really travels into this cycle's execution.
    import json
    saved = json.loads((tmp_path / "state/content/continuous/cycles"
                        / result["cycle"] / "memory_process_variant.json").read_text())
    assert saved["arm"] == row[1]
    assert saved["candidate_key"] == "no-qualified-skill"


def test_goal_loop_uses_assigned_arm_not_uncommitted_variant(tmp_path):
    from test_evidence_floor_goal_loop import NOW, worker
    _, loop = worker(tmp_path)
    loop.memory_life.select_process_variant = lambda **kw: CANDIDATE
    result = asyncio.run(loop.run_once(now=NOW))
    saved = result["learning_transfer"]
    assert saved["status"] == "UNVERIFIED"
    import json
    action = json.loads((tmp_path / "state/content/continuous/cycles"
                         / result["cycle"] / "memory_process_variant.json").read_text())
    assert action["arm"] in {"BASELINE", "LEARNED"}
    assert action["policy_action"] == ("RETRY_ONCE_SAFE" if action["arm"] == "LEARNED" else "DIRECT")
    assert action["candidate_key"] != "no-qualified-skill"


def test_mature_world_observation_links_to_precommitted_real_trial(tmp_path):
    ledger = LearningTransfer(tmp_path)
    cycle = "world-cycle-1"
    ledger.assign(cycle=cycle, topic="crypto-market", candidate=CANDIDATE)
    ledger.settle(cycle=cycle, outcome="PUBLISHED", evidence_refs=["publish:1"],
                  publication_receipt=_publish(cycle))
    observed = [{
        "outcome_id": "strategy-outcome:01", "cycle": cycle,
        "pageviews_24h": 18.0,
        "observed_at": "2026-10-08T20:00:00+00:00",
        "evidence_refs": ["nginx:window:1"],
    }]
    assert ledger.observe_world(observed)["attached"] == 1
    assert ledger.observe_world(observed)["attached"] == 0
    assert ledger.audit()["mature_world_outcomes"] == 1
    assert not ledger.audit()["causal_improvement_proven"]
    modified = [{**observed[0], "pageviews_24h": 27}]
    with pytest.raises(ValueError, match="new evidence identity"):
        ledger.observe_world(modified)
    revised = [{**modified[0], "evidence_refs": ["nginx:window:2"]}]
    assert ledger.observe_world(revised)["attached"] == 1
    assert ledger.audit()["mature_world_outcomes"] == 1
    assert ledger.audit()["world_outcome_revisions"] == 2


def test_unverified_cycles_and_missing_actions_cannot_claim_world_gain(tmp_path):
    ledger = LearningTransfer(tmp_path)
    ledger.assign(cycle="inflight", topic="ai", candidate=CANDIDATE)
    ledger.settle(cycle="inflight", outcome="DRAFT_CREATED", evidence_refs=["draft:1"])
    payload = [{"cycle": "inflight", "outcome_id": "outcome:x",
                "pageviews_24h": 900, "evidence_refs": ["invented"]}]
    assert ledger.observe_world(payload)["attached"] == 0
    assert ledger.audit()["mature_world_outcomes"] == 0


def test_direct_variant_does_not_create_false_method_change(tmp_path):
    ledger = LearningTransfer(tmp_path)
    direct = {**CANDIDATE, "policy_action": "DIRECT"}
    row = ledger.assign(cycle="direct", topic="ai", candidate=direct)
    assert row["arm"] == "BASELINE"
    assert row["candidate_key"] == "no-qualified-skill"


def test_learned_method_changes_real_checker_execution_vs_baseline(tmp_path):
    import json
    from wechat_traffic_agent.learning_transfer import _fingerprint, ACTION
    from test_evidence_floor_goal_loop import NOW, worker

    def chosen_cycle(arm):
        for i in range(100):
            cycle = f"v2-retry_{i}-1790000000"
            draw = int(_fingerprint([cycle, ACTION])[:8], 16) % 100
            if (draw >= 50) == (arm == "LEARNED"):
                return cycle
        raise AssertionError("holdout assignment missing")

    async def execute(label):
        (tmp_path / label).mkdir()
        _, loop = worker(tmp_path / label, counter=True)
        loop.memory_life.select_process_variant = lambda **kwargs: CANDIDATE
        calls = []
        original = loop.runtime.cognitive_probe

        async def intermittent(task, max_tokens, output_schema=None):
            response = await original(task, max_tokens, output_schema=output_schema)
            if task.startswith("CHECK_ARTICLE:"):
                calls.append(task)
                if len(calls) == 1:
                    row = json.loads(response)
                    row["grounded"] = False
                    return json.dumps(row)
            return response

        loop.runtime.cognitive_probe = intermittent
        result = await loop.run_once(now=NOW, cycle_id=chosen_cycle(label))
        return result, calls

    baseline, no_retry = asyncio.run(execute("BASELINE"))
    learned, with_retry = asyncio.run(execute("LEARNED"))
    assert baseline["status"] == "HELD_BY_CHECKS"
    assert learned["status"] == "DRAFT_CREATED"
    assert len(no_retry) == 1
    assert len(with_retry) == 2
    assert baseline["learning_transfer"]["arm"] == "BASELINE"
    assert learned["learning_transfer"]["arm"] == "LEARNED"
    # No public publication means this still cannot count as a verified
    # audience effect or evidence of better real-world performance.
    assert learned["learning_transfer"]["status"] == "UNVERIFIED"


def test_real_goal_publication_then_mature_nginx_observation_closes_the_loop(tmp_path):
    from datetime import datetime, timedelta
    from test_website_experiment import NOW, _combined, _goal_worker
    from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
    from wechat_traffic_agent.content_strategy import collect_mature_strategy_outcomes

    _, worker, publisher = _goal_worker(tmp_path)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "PUBLISHED"
    settled = result["learning_transfer"]
    assert settled["status"] == "MEASURED"
    assert settled["measurement"]["success"] == 1
    route = result["publication"]["outcome_attribution"]["public_route"]
    assert route == settled["measurement"]["publication_route"]

    published_at = datetime.fromisoformat(
        result["publication"]["outcome_attribution"]["published_at"]
    )
    log = tmp_path / "real-access.log"
    log.write_text("".join([
        _combined(published_at - timedelta(minutes=1), "/"),
        _combined(published_at + timedelta(hours=1), route),
        _combined(published_at + timedelta(hours=25), "/"),
    ]))
    collected = WebsiteOutcomeCollector(
        state_root=tmp_path / "goal-state", access_log=log,
        account="https://example.com",
    ).collect(now=published_at + timedelta(hours=25))
    assert collected["status"] == "RECORDED"
    mature = collect_mature_strategy_outcomes(
        state_root=tmp_path / "goal-state", account="https://example.com",
        as_of=published_at + timedelta(hours=25),
    )
    linked = worker.learning_transfer.observe_world(mature)
    assert linked["attached"] >= 1
    assert worker.learning_transfer.audit()["mature_world_outcomes"] >= 1
    assert not worker.learning_transfer.audit()["causal_improvement_proven"]
