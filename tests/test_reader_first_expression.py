import asyncio
import json
import pytest
from wechat_traffic_agent.x_social import XAutonomyLoop, XResearchPostDraft
from wechat_traffic_agent.x_article_media import ArticleImageSpec, prepare_article_media
from wechat_traffic_agent.learning_expression_quality import review_learning_expression
from wechat_traffic_agent.x_browser import XBrowserPublishResult
from test_x_owner_execution import cycle_context
from test_x_social import _ModelRuntime, _Runtime, NOW

pytestmark = pytest.mark.usefixtures("maintenance_cognition")
ANCHOR = "Saved identity lets a restarted worker match the earlier operation."
BODY = ("If a worker restarts after a payment request, should it send the payment again?\n\n"
        + ANCHOR + "\n\nIdentity is a label for the operation, not its result. "
        "The worker must check the earlier outcome before deciding to retry.")
QUOTE = "The worker stores a tool invocation ID before execution and reconciles it after restart."


def learning(folder):
    value = {"cycle": folder.name, "question_status": "PARTIAL", "answer": BODY,
             "learning_delta": "Identity and outcome answer different recovery questions.",
             "reasoning_steps": [{"kind": "INFERENCE", "fact_indices": [0], "explanation": BODY}],
             "sources": [{"evidence_id": "source-1", "summary": QUOTE, "url": "https://example.org/recovery"}],
             "facts": [{"evidence_id": "source-1", "quote": QUOTE, "observation": "Save identity before execution."}]}
    (folder/"learning_result.json").write_text(json.dumps(value))
    return value


def review():
    return {"concrete_discovery": True, "faithful_to_sources": True, "adds_to_recent_shares": True,
            "useful_understanding": True, "reader_takeaway": "An operation's identity does not establish its outcome.",
            "source_fact_id": "fact-0", "post_quote": ANCHOR,
            "rationale": "The reader can distinguish matching a prior operation from proving its result."}


def image():
    return {"role": "INLINE", "mode": "GRAPHIC", "after_text": ANCHOR,
            "diagram_labels": ["Save operation identity", "Restart: match the earlier operation", "Check outcome before retry"],
            "caption": "Identity enables matching; reconciliation establishes outcome.",
            "alt_text": "Operation identity and outcome reconciliation sequence"}


@pytest.mark.parametrize("preselected", ["X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"])
def test_research_selects_illustrated_article_once_after_material_is_available(tmp_path, monkeypatch, preselected):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    folder, result = cycle_context(tmp_path)
    learning(folder)
    request = json.loads((folder/"owner_publication_request.json").read_text())
    request["decision"]["publication_surface"] = preselected
    (folder/"owner_publication_request.json").write_text(json.dumps(request))
    model = _ModelRuntime([{"format": "ARTICLE", "title": "Should a restarted worker repeat a payment?",
        "post_text": BODY, "images": [image()],
        "rationale": "Start with a familiar retry question, then explain identity and outcome with a sequence."}, review()])
    class Transport:
        calls = []
        async def publish_article(self, **kwargs):
            self.calls.append(kwargs)
            return XBrowserPublishResult(post_id="123", url="https://x.com/ferro/status/123", handle="ferro")
    transport = Transport()
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path, base_url="",
                         browser_publish_transport=transport)
    outcome = asyncio.run(loop.after_content_cycle(result=result, now=NOW))
    assert outcome["status"] == "POSTED" and outcome["format"] == "ARTICLE"
    assert len(model.tasks) == 2
    assert "Any earlier format selection is provisional" in model.tasks[0]
    assert "image_plan" in model.tasks[1]
    saved = json.loads((folder/"social_expression_drafts.json").read_text())["drafts"]["x"]
    assert saved["images"][0]["after_text"] == ANCHOR
    assert saved["title"] == "Should a restarted worker repeat a payment?"
    sent = transport.calls[0]
    assert sent["inline_images"][0]["kind"] == "EXPLANATORY_DIAGRAM"
    assert sent["inline_images"][0]["after_text"] == ANCHOR
    assert sent["text"].index(image()["caption"]) < sent["text"].index("Identity is a label")
    assert asyncio.run(loop.after_content_cycle(result=result, now=NOW))["status"] == "ALREADY_EXPRESSED"
    assert len(transport.calls) == 1


def test_inline_graphic_cannot_silently_become_a_generic_cover():
    with pytest.raises(ValueError, match="explicit diagram labels"):
        ArticleImageSpec(role="INLINE", mode="GRAPHIC", prompt="Explain the sequence", alt_text="Recovery sequence")


@pytest.mark.parametrize("anchor", [None, "Absent passage", ANCHOR])
def test_research_article_requires_an_unambiguous_placement(anchor):
    spec = {**image(), "after_text": anchor}
    body = BODY + ("\n\n" + ANCHOR if anchor == ANCHOR else "")
    with pytest.raises(ValueError, match="unique exact after_text"):
        XResearchPostDraft(format="ARTICLE", title="Identity is not outcome", post_text=body, images=[spec],
                           rationale="The diagram belongs beside the matching explanation.")


def test_developed_numbered_comparison_does_not_require_blank_line_paragraphs(tmp_path):
    folder, _ = cycle_context(tmp_path)
    value = learning(folder)
    text = "1. " + ANCHOR + "\n2. Identity is a label, so check the result before retrying."
    model = _ModelRuntime([review()])
    checked = asyncio.run(review_learning_expression(runtime=model, folder=folder, learning=value,
        text=text, recent_shares=[], label="points", format_name="LONG_POST"))
    assert checked["passed"] and checked["readable_presentation"]


def test_shared_structured_helper_preserves_public_prose_and_budget_on_schema_repair():
    from wechat_traffic_agent.content_loop import _structured
    class Runtime:
        tasks = []
        budgets = []
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            self.tasks.append(task)
            self.budgets.append(max_tokens)
            if len(self.tasks) == 1:
                return json.dumps({"format": "ARTICLE", "post_text": BODY,
                                  "rationale": "Explain identity and outcome without losing the distinction."})
            return json.dumps({"format": "ARTICLE", "title": "Identity does not prove the outcome",
                               "post_text": BODY, "images": [image()],
                               "rationale": "Explain identity and outcome without losing the distinction."})
    runtime = Runtime()
    draft = asyncio.run(_structured(runtime, "Write a reader-oriented illustrated Article.",
                                    XResearchPostDraft, max_tokens=6000))
    assert draft.post_text == BODY
    assert runtime.budgets == [6000, 6000]
    assert all("every prose field must be ONE short sentence" not in task for task in runtime.tasks)
    assert all("Shorten generated prose to at most 150 characters" not in task for task in runtime.tasks)
