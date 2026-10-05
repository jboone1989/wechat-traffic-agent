import asyncio
import json
from wechat_traffic_agent.content_explorer import GoalEvidenceExplorer, _candidate_quote_options, _bind_quote_choices, EvidenceFilter
from test_content_explorer import candidate


def test_relevance_sees_formula_beyond_site_navigation_without_full_body_prompt():
    formula = "expected_max_sharpe depends on trial_variance; reducing trial_variance lowers the selection penalty."
    row = {**candidate(), "summary":"A repository page about statistical validation.",
           "evidence_text":"Navigation and sign-in links. "*160 + formula}
    quotes, compact = _candidate_quote_options([row],500,query="How does trial_variance change expected_max_sharpe?")
    matching = [key for key, value in quotes.items() if "trial_variance" in value["evidence_quote"]]
    assert matching
    assert len(json.dumps(compact)) < 2000
    value = _bind_quote_choices(json.dumps({"matches":[{"source_quote_id":matching[0],
        "reason":"The formula helps explain the variance path even without a completed worked example."}]}),quotes,EvidenceFilter)
    assert value.matches[0].evidence_id == row["evidence_id"]
    assert value.matches[0].evidence_quote in row["evidence_text"]
