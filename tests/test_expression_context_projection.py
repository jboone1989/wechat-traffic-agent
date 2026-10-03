import copy
import json

from wechat_traffic_agent.x_social import _expression_model_context


def test_publication_keeps_full_evidence_once_and_current_goal():
    source = {"evidence_id": "code-v1", "url": "https://example.org/code",
              "evidence_text": "Persist invocation ID before dispatch. " * 2000}
    context = {
        "cycle": "real-cycle",
        "source_evidence": source,
        "learning": {"sources": [source], "facts": [{"quote": "Persist invocation ID before dispatch."}],
                     "learning_delta": "Invocation ID survives worker restart."},
        "mind_plan": {"goal": {"goal_id": "owner-daily", "description": "Share useful learning"},
                      "required_topic": "restart", "available_topics": ["unrelated"] * 2000},
        "cognition": {"worth_saying": True, "what_changed": "Restart reconciliation matters."},
        "prediction": {"uncertainty": "Outcome gain remains untested."},
    }
    original = copy.deepcopy(context)
    projected = _expression_model_context(context)
    assert projected["learning"] == original["learning"]
    assert projected["cognition"] == original["cognition"]
    assert projected["prediction"] == original["prediction"]
    assert projected["mind_plan"]["goal"] == original["mind_plan"]["goal"]
    assert len(json.dumps(projected)) < len(json.dumps(context)) / 2
    assert json.dumps(projected).count(source["evidence_text"]) == 1
    assert context == original


def test_same_id_different_revision_is_not_discarded():
    context = {"source_evidence": {"evidence_id": "code", "evidence_text": "revision two"},
               "learning": {"sources": [{"evidence_id": "code", "evidence_text": "revision one"}]}}
    assert _expression_model_context(context)["source_evidence"] == context["source_evidence"]


def test_missing_learning_preserves_source():
    context = {"source_evidence": {"evidence_id": "source", "summary": "Concrete discovery"}}
    assert _expression_model_context(context) == context
