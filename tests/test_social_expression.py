import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.social_expression import SocialExpressionDraftWorker

NOW = datetime(2026, 9, 16, 13, 20, tzinfo=timezone.utc)


class Runtime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self):
        self.calls = []

    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        return json.dumps({
            "okx": {
                "channel": "OKX", "format": "SHORT_POST",
                "text": "BTC is weaker, but this snapshot still cannot identify the seller. I am watching volume and funding next; a fast relative-strength reversal would weaken this read.",
                "style_observation_ids": ["conditional"],
                "rationale": "A direct market read fits the current thought."
            },
            "x": {
                "channel": "X", "format": "SHORT_POST",
                "text": "BTC is weaker, but one snapshot cannot identify who sold. Watching volume/funding next; a fast relative-strength reversal would weaken this read.",
                "style_observation_ids": ["conditional"],
                "rationale": "The same cognition fits a compact conditional post."
            }
        })

    def persist(self):
        pass


def setup_cycle(tmp_path: Path):
    state = tmp_path / "state"
    cycle = "v2-social-123"
    folder = state / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    (folder / "cognition.json").write_text(json.dumps({
        "worth_saying": True, "expression_kind": "THOUGHT",
        "what_matters": "One market snapshot supports dispersion, not causal attribution."
    }))
    (folder / "social_style_context.json").write_text(json.dumps({
        "purpose": "STYLE_ONLY", "status": "DISCOVERED", "performance_claim": "UNVERIFIED",
        "observations": [{
            "observation_id": "conditional", "channel": "X", "format": "SHORT_POST",
            "source_url": "https://x.com/example/status/1",
            "features": ["Use if-then conditions.", "Name what would weaken the view."]
        }]
    }))
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps({"artifact": {
        "title": "A market note", "summary": "A bounded market observation.",
        "sections": [{"heading": "Now", "paragraphs": ["BTC moved and uncertainty remains."]}],
        "source_refs": [{"ref_id": "okx:1", "source": "OKX public market API"}]
    }}))
    result = {"status": "PUBLISHED", "cycle": cycle, "draft_path": str(draft_path),
              "publication": {"public_route": "/notes/a-market-note/"}}
    return state, cycle, result


def test_social_expression_drafts_are_channel_specific_and_non_publishing(tmp_path):
    state, cycle, result = setup_cycle(tmp_path)
    runtime = Runtime()
    worker = SocialExpressionDraftWorker(runtime=runtime, state_root=state)
    value = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert value["status"] == "DRAFTED"
    saved = json.loads((state / "content/continuous/cycles" / cycle / "social_expression_drafts.json").read_text())
    assert saved["publication_authority"] is False
    assert saved["style_status"] == "DISCOVERED"
    assert saved["style_performance_claim"] == "UNVERIFIED"
    assert saved["drafts"]["okx"]["channel"] == "OKX"
    assert saved["drafts"]["x"]["channel"] == "X"
    assert len(saved["drafts"]["x"]["text"]) <= 280
    assert "STYLE_ONLY" in runtime.calls[0]
    replay = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert replay["status"] == "ALREADY_DRAFTED"
    assert len(runtime.calls) == 1


def test_social_expression_requires_published_worth_saying_cycle(tmp_path):
    state, _cycle, result = setup_cycle(tmp_path)
    worker = SocialExpressionDraftWorker(runtime=Runtime(), state_root=state)
    assert asyncio.run(worker.after_content_cycle(result={**result, "status": "HELD_BY_CHECKS"}, now=NOW))["status"] == "NO_PUBLISHED_EXPRESSION"
