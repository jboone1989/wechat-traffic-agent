"""Research reward must reflect evidence, not publication or completion."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.interest_learning import learning_reward, topic_contexts
from wechat_traffic_agent.state_events import FerroStateEventApplier


def _research(progress="DEEPENED", *, with_fact=True):
    return {
        "cycle": "cycle-1", "topic": "btc-hmm", "progress": progress,
        "question_status": "PARTIAL",
        "learning_delta": "Confirmed that the model uses backward-looking volatility indicators.",
        "sources": [{"evidence_id": "web:1"}],
        "facts": ([{"evidence_id": "web:1", "quote": "rolling volatility",
                    "observation": "The implementation uses volatility"}]
                  if with_fact else []),
        "reasoning_steps": [{"kind": "OBSERVATION"}, {"kind": "INFERENCE"}],
    }


def test_learning_reward_requires_evidence_and_new_application():
    assert learning_reward(_research(progress="REPEATED"))["utility"] == 0.0
    assert learning_reward(_research(with_fact=False))["utility"] == 0.0
    grounded = learning_reward(_research())
    assert grounded["utility"] == 0.25
    assert grounded["kind"] == "GROUNDED_UNDERSTANDING"
    prior = _research()
    prior["application"] = {"assessment": {"status": "CONFIRMED"}, "evidence": []}
    assert learning_reward(prior)["utility"] == 0.25
    tested = _research()
    tested["application"] = {
        "assessment": {"status": "REFUTED"},
        "evidence": [{"evidence_id": "web:new", "quote": "independent outcome"}],
    }
    assert learning_reward(tested)["utility"] > grounded["utility"]


def test_topic_context_uses_durable_knowledge_and_preference(tmp_path):
    root = tmp_path / "state"
    path = root / "content" / "learning" / "research.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"inquiries": {
        "btc-hmm": {"question_status": "PARTIAL", "last_progress": "DEEPENED"},
        "macro": {"question_status": "ANSWERED", "last_progress": "REPEATED"},
    }}), encoding="utf-8")
    context = topic_contexts(root, ["btc-hmm", "macro"], {
        "interests": [{"name": "btc-hmm", "preference": 0.8,
                       "familiarity": 0.25, "salience": 0.9}],
    })
    assert context["btc-hmm"]["knowledge_gap"] > context["macro"]["knowledge_gap"]
    assert context["btc-hmm"]["novelty"] == 0.75
    assert context["btc-hmm"]["preference"] == 0.8


def test_neural_event_dispatch_binds_commitment_and_settlement():
    calls = []
    owner = SimpleNamespace(
        commit_interest_study=lambda **kw: calls.append(("commit", kw)) or {"status": "COMMITTED"},
        settle_interest_study=lambda **kw: calls.append(("settle", kw)) or {"status": "SETTLED"},
    )
    applier = FerroStateEventApplier(owner)
    event = {
        "observed_at": datetime(2026, 10, 7, tzinfo=timezone.utc).isoformat(),
        "payload": {"cycle": "study:1", "topic": "btc-hmm",
                    "features": {"knowledge_gap": 0.75}},
    }
    assert applier.apply({**event, "kind": "COMMIT_INTEREST_STUDY"})["status"] == "COMMITTED"
    result = applier.apply({**event, "kind": "SETTLE_INTEREST_STUDY", "payload": {
        "cycle": "study:1", "topic": "btc-hmm",
        "outcome_id": "interest-learning:study:1", "utility": 0.5,
    }})
    assert result["status"] == "SETTLED"
    assert [kind for kind, _ in calls] == ["commit", "settle"]
