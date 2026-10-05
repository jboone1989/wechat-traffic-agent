import asyncio
import json
import pytest
from wechat_traffic_agent.learning_research import ResearchStep, investigate_learning, read_research_state
from wechat_traffic_agent.learning_memory_bridge import LearningMemoryBridge
from wechat_traffic_agent.memory_service import MemoryService
from wechat_traffic_agent.x_social import XAutonomyLoop, XResearchPostDraft, _expression_model_context
from test_learning_research import step, Runtime, Explorer, DETAIL, FOCUS, KNOWN, NOW
from test_x_owner_execution import cycle_context
from test_x_social import _ModelRuntime, _Runtime
from test_x_browser import _Transport

pytestmark = pytest.mark.usefixtures("maintenance_cognition")


def test_partial_reasoning_survives_receipt_history_memory_restart_and_expression(tmp_path):
    value = step("DEEPENED", DETAIL)
    value.update(question_status="PARTIAL", inquiry_action="CONTINUE")
    explanation = value["reasoning_steps"][0]["explanation"]
    result = asyncio.run(investigate_learning(
        runtime=Runtime([value]), explorer=Explorer(), folder=tmp_path/"cycles/reasoning",
        state_root=tmp_path, topic="recovery", focus=FOCUS,
        memory={"beliefs":[{"proposition":KNOWN}], "research":{"history":[]}},
        evidence=[DETAIL], selected=DETAIL, now=NOW, search_rounds=0))
    assert result["question_status"] == "PARTIAL"
    assert read_research_state(tmp_path)["history"][-1]["reasoning_steps"] == value["reasoning_steps"]
    restarted = MemoryService(tmp_path)
    saved = restarted.store.get_memory("research-understanding:reasoning")
    assert explanation in saved["text"]
    assert saved["payload"]["reasoning_steps"] == value["reasoning_steps"]
    projected = _expression_model_context({"learning":result})["learning"]
    assert projected["reasoning_steps"] == value["reasoning_steps"]


def test_reasoning_cannot_cite_a_missing_fact():
    value = step("DEEPENED", DETAIL)
    value["reasoning_steps"][0]["fact_indices"] = [1]
    with pytest.raises(ValueError, match="facts actually present"):
        ResearchStep.model_validate(value)


@pytest.mark.parametrize("first_draft_is_summary", [False, True, "wall"])
def test_partial_research_promotes_short_selection_to_long_in_one_draft_call(tmp_path, monkeypatch, first_draft_is_summary):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    folder, result = cycle_context(tmp_path)
    quote = "The worker stores a tool invocation ID before execution and reconciles it after restart."
    explanation = (
        "The source saves invocation identity before executing. That gives a restarted worker "
        "a stable way to match an earlier operation instead of blindly creating a new one.\n\n"
        "Identity alone does not tell us whether the external side effect succeeded; "
        "the next step is to inspect the reconciliation result before deciding to retry."
    )
    learning = {"cycle":folder.name, "question_status":"PARTIAL", "answer":explanation,
        "learning_delta":"Persisted identity distinguishes an earlier operation from a new invocation.",
        "reasoning_steps":[{"kind":"INFERENCE","fact_indices":[0],"explanation":explanation}],
        "sources":[{"evidence_id":"source-1","summary":quote,"url":"https://example.org/recovery"}],
        "facts":[{"evidence_id":"source-1","quote":quote,"observation":"Identity is saved before execution."}]}
    (folder/"learning_result.json").write_text(json.dumps(learning))
    review = {"concrete_discovery":True,"faithful_to_sources":True,"adds_to_recent_shares":True,
        "useful_understanding":True,"reader_takeaway":"Saved identity permits matching an earlier operation but does not prove its outcome.",
        "source_fact_id":"fact-0","post_quote":"Identity alone does not tell us whether the external side effect succeeded",
        "rationale":"The intermediate distinction is useful while the recovery question remains unfinished."}
    supported = {"format":"LONG_POST","post_text":explanation,
                 "rationale":"Preserve the evidence-to-inference explanation without cutting its limit."}
    responses = [supported, review]
    if first_draft_is_summary:
        summary = "The worker saves invocation identity before execution so it can match operations after restart."
        rejected = {**review, "useful_understanding":False, "post_quote":summary,
                    "rationale":"It lists a mechanism and benefit but omits why identity does not prove outcome."}
        if first_draft_is_summary == "wall":
            wall = explanation.replace("\n\n", " ")
            rejected = {**review, "useful_understanding": False,
                        "rationale": "The long note compresses several reasoning moves into one unbroken paragraph."}
            repaired = {"channel": "X", "format": "LONG_POST", "text": explanation,
                        "intent": "EXPLAINER", "style_observation_ids": [],
                        "rationale": "Separate the identity observation and its unresolved outcome implication."}
            responses = [{"format": "LONG_POST", "post_text": wall,
                          "rationale": "A correct but unreadable research summary."}, rejected, repaired, review]
        else:
            responses = [{"format":"SHORT_POST","post_text":summary,
                          "rationale":"A compressed mechanism summary."}, rejected, supported, review]
    model = _ModelRuntime(responses)
    browser = _Transport()
    loop = XAutonomyLoop(runtime=_Runtime(),model_runtime=model,state_root=tmp_path,
        base_url="",browser_publish_transport=browser)
    outcome = asyncio.run(loop.after_content_cycle(result=result,now=NOW))
    assert outcome["status"] == "POSTED" and outcome["format"] == "LONG_POST"
    assert browser.calls == [explanation]
    assert len(model.tasks) == (4 if first_draft_is_summary else 2)
    assert "concise takeaway plus the supplied public URL" not in model.tasks[0]
    assert "explicitly requested an original SHORT_POST" not in model.tasks[0]
    assert "earlier SHORT_POST selection is provisional" in model.tasks[0]
    assert "reasoning_steps" in model.tasks[0]
    draft = json.loads((folder/"social_expression_drafts.json").read_text())
    assert draft["x_strategy"]["selection_authority"] == "FERRO_LEARNING_EXPRESSION"
    assert "\n\n" in draft["drafts"]["x"]["text"]
    assert draft["drafts"]["x"]["text"] == explanation
    assert asyncio.run(loop.after_content_cycle(result=result,now=NOW))["status"] == "ALREADY_EXPRESSED"
    assert browser.calls == [explanation]


def test_short_research_format_still_enforces_actual_character_limit():
    with pytest.raises(ValueError, match="Use LONG_POST"):
        XResearchPostDraft(format="SHORT_POST",post_text="x"*281,rationale="A supported explanation.")


def test_learning_reaches_reasoning_with_a_large_history_archive(tmp_path):
    history = [{
        "cycle": f"historical-{index}", "topic": "older-topic",
        "focus": {"question": "Previous investigation " * 30,
                  "completion_criterion": "Explain a supported mechanism " * 20},
        "answer": "Previously supported explanation " * 40,
        "next_question": "A different unknown to investigate " * 12,
        "facts": [{"evidence_id": DETAIL["evidence_id"], "quote": DETAIL["summary"],
                   "observation": "A historical observation " * 20}],
        "reasoning_steps": [{"kind": "INFERENCE", "fact_indices": [0],
                             "explanation": "Previous evidence-to-inference account " * 20}],
    } for index in range(6)]
    memory = {"beliefs": [{"proposition": KNOWN}], "research": {"history": history}}
    runtime = Runtime([step("DEEPENED", DETAIL)])
    result = asyncio.run(investigate_learning(
        runtime=runtime, explorer=Explorer(), folder=tmp_path/"cycles/bounded-history",
        state_root=tmp_path, topic="recovery", focus=FOCUS, memory=memory,
        evidence=[DETAIL], selected=DETAIL, now=NOW, search_rounds=0))
    assert result["reasoning_steps"]
    assert result["progress"] == "DEEPENED"
    assert memory["research"]["history"] == history
