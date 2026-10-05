import copy
import json

from wechat_traffic_agent.x_social import _expression_model_context


def test_publication_keeps_exact_verified_facts_and_current_goal():
    source = {"evidence_id": "code-v1", "url": "https://example.org/code",
              "evidence_text": "Persist invocation ID before dispatch. " * 2000}
    context = {
        "cycle": "real-cycle",
        "source_evidence": source,
        "learning": {"sources": [source], "facts": [{"quote": "Persist invocation ID before dispatch."}],
                     "learning_delta": "Invocation ID survives worker restart.",
                     "answer": "A persisted invocation ID lets a restarted worker reconcile the prior operation.",
                     "question_status": "ANSWERED", "inquiry_action": "NEXT_QUESTION"},
        "mind_plan": {"goal": {"goal_id": "owner-daily", "description": "Share useful learning"},
                      "required_topic": "restart", "available_topics": ["unrelated"] * 2000},
        "cognition": {"worth_saying": True, "what_changed": "Restart reconciliation matters."},
        "prediction": {"uncertainty": "Outcome gain remains untested."},
    }
    original = copy.deepcopy(context)
    projected = _expression_model_context(context)
    assert projected["learning"]["facts"] == original["learning"]["facts"]
    assert projected["learning"]["learning_delta"] == original["learning"]["learning_delta"]
    assert projected["learning"]["answer"] == original["learning"]["answer"]
    assert projected["learning"]["question_status"] == "ANSWERED"
    assert projected["cognition"] == original["cognition"]
    assert projected["prediction"] == original["prediction"]
    assert projected["mind_plan"]["goal"] == original["mind_plan"]["goal"]
    assert len(json.dumps(projected)) < len(json.dumps(context)) / 2
    assert "evidence_text" not in projected["learning"]["sources"][0]
    assert projected["learning"]["sources"][0]["url"] == source["url"]
    assert context == original


def test_same_id_different_revision_is_not_discarded():
    context = {"source_evidence": {"evidence_id": "code", "evidence_text": "revision two"},
               "learning": {"sources": [{"evidence_id": "code", "evidence_text": "revision one"}]}}
    assert _expression_model_context(context)["source_evidence"] == context["source_evidence"]


def test_missing_learning_preserves_source():
    context = {"source_evidence": {"evidence_id": "source", "summary": "Concrete discovery"}}
    assert _expression_model_context(context) == context

def test_expression_omits_comparison_archives_but_keeps_application_assessment():
    application = {"prior_quotes": ["original archival quote" * 10000],
        "prior_source_ids": ["old-source"], "prior_source_urls": ["https://example.org/old"],
        "knowledge": {"fact": "Persist receipt before dispatch."},
        "application": {"prediction": "Receipt survives a worker restart."},
        "assessment": {"outcome": "SUPPORTED", "confidence": 0.7},
        "evidence": [{"quote": "The worker reloads its receipt.", "source": "code"}]}
    context = {"cycle": "application-cycle", "learning": {
        "cycle": "application-cycle", "application": application,
        "facts": [{"quote": "The worker reloads its receipt.", "evidence_id": "code"}],
        "sources": [{"evidence_id": "code", "title": "Recovery code", "url": "https://example.org/code",
            "evidence_text": "The worker reloads its receipt." * 1000}]}}
    original = copy.deepcopy(context)
    projected = _expression_model_context(context)
    assessment = projected["learning"]["application"]
    assert assessment["assessment"] == application["assessment"]
    assert assessment["knowledge"] == application["knowledge"]
    assert assessment["evidence"] == application["evidence"]
    assert "prior_quotes" not in assessment
    assert projected["learning"]["facts"] == context["learning"]["facts"]
    assert projected["learning"]["projection"]["receipt_ref"].endswith("application-cycle/learning_result.json")
    assert len(json.dumps(projected)) < len(json.dumps(context)) / 20
    assert context == original


def test_expression_drops_interest_index_but_retains_current_goal_and_facts():
    context = {
        "mind_plan": {"goal": {"description": "Learn and share a mechanism"},
            "endogenous_goal": {"goal_id": "g1", "description": "Study worker restart",
                "interest_refs": ["historical-interest-" + str(i) for i in range(10000)]}},
        "learning": {"facts": [{"quote": "Persist invocation before dispatch."}],
            "learning_delta": "Invocation receipts survive worker restart.",
            "application": {"knowledge": {"fact": "Persist before dispatch"},
                "assessment": {"status": "UNRESOLVED"}, "feedback_cycles": ["old"] * 10000}},
    }
    original = copy.deepcopy(context)
    result = _expression_model_context(context)
    assert result["mind_plan"]["endogenous_goal"]["description"] == "Study worker restart"
    assert "interest_refs" not in result["mind_plan"]["endogenous_goal"]
    assert result["learning"]["facts"] == context["learning"]["facts"]
    assert result["learning"]["application"]["assessment"] == {"status": "UNRESOLVED"}
    assert "feedback_cycles" not in result["learning"]["application"]
    assert len(json.dumps(result)) < 1000
    assert context == original
