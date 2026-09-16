import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from wechat_traffic_agent.social_evidence import collect_okx_social_market_context
from wechat_traffic_agent.social_expression import (
    ChannelSocialDraft, OKX_SHORT_POST_MAX_CHARS, SOCIAL_LONG_TEXT_MAX_CHARS, SocialExpressionDraftWorker,
)

NOW = datetime(2026, 9, 16, 13, 20, tzinfo=timezone.utc)


class Runtime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self, mode="ready"):
        self.mode = mode
        self.calls = []

    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        if task.startswith("SOCIAL_EVIDENCE_PLAN"):
            if self.mode.startswith("seek"):
                return json.dumps({
                    "action": "SEEK",
                    "missing_evidence": ["volume", "funding rate", "open interest"],
                    "search_query": "BTC XRP volume funding open interest now",
                    "rationale": "The market interpretation needs positioning and participation evidence before asserting a cause."
                })
            if self.mode == "skip":
                return json.dumps({
                    "action": "SKIP", "missing_evidence": [], "search_query": None,
                    "rationale": "The cycle contains no information gain or useful public question."
                })
            return json.dumps({
                "action": "READY", "missing_evidence": [], "search_query": None,
                "rationale": "The supplied evidence supports one bounded and useful observation."
            })
        if task.startswith("SOCIAL_EVIDENCE_RESOLUTION"):
            action = "QUESTION" if self.mode == "seek_question" else "DRAFT"
            facts = [] if action == "QUESTION" else ["XRP has higher 24h spot volume and negative funding in the supplemental snapshot."]
            return json.dumps({
                "action": action, "useful_facts": facts,
                "rationale": "The enrichment still leaves attribution unresolved." if action == "QUESTION" else "The enrichment adds concrete participation and positioning evidence."
            })
        is_question = "Evidence resolution is QUESTION" in task
        return json.dumps({
            "okx": {
                "channel": "OKX", "format": "SHORT_POST", "intent": "QUESTION" if is_question else "OBSERVATION",
                "text": ("XRP is falling harder than BTC while the evidence still cannot separate an idiosyncratic unwind from broad de-risking. Which changes first next: relative volume, funding, or OI?"
                         if is_question else
                         "XRP is weaker than BTC, and the added volume/funding/OI snapshot gives a concrete positioning check. I am watching whether that divergence persists into the next window."),
                "style_observation_ids": ["conditional"],
                "rationale": "A direct evidence-led market post fits the current thought."
            },
            "x": {
                "channel": "X", "format": "SHORT_POST", "intent": "QUESTION" if is_question else "OBSERVATION",
                "text": ("XRP is weaker than BTC, but attribution is still open. Which signal resolves it first next: relative volume, funding, or OI?"
                         if is_question else
                         "XRP is weaker than BTC. The added volume/funding/OI snapshot gives a better positioning check than price alone; next I am watching whether the divergence persists."),
                "style_observation_ids": ["conditional"],
                "rationale": "The same evidence fits a compact social expression."
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
        "what_matters": "One crypto market snapshot shows dispersion but causal attribution is unresolved."
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
        "title": "A crypto market note", "summary": "XRP fell more than BTC in the current OKX snapshot.",
        "sections": [{"heading": "Now", "paragraphs": ["Price dispersion is visible but attribution remains uncertain."]}],
        "source_refs": [{"ref_id": "okx:1", "source": "OKX public market API"}]
    }}))
    result = {"status": "PUBLISHED", "cycle": cycle, "draft_path": str(draft_path),
              "publication": {"public_route": "/notes/a-market-note/"}}
    return state, cycle, result


async def fake_market_evidence(now):
    return {
        "schema_version": "social-okx-market-evidence.v1", "status": "AVAILABLE",
        "source": "OKX public API", "observed_at": now.isoformat(), "source_revision": "sha256:test",
        "evidence_refs": ["okx-public:test"],
        "summary": "BTC volume 600m USDT, funding +0.0017%, OI 28k BTC; XRP volume 126m USDT, funding -0.0025%, OI 79m XRP.",
        "details": [{"symbol": "BTC"}, {"symbol": "XRP"}], "failures": [],
    }


def test_social_expression_drafts_are_channel_specific_and_non_publishing(tmp_path):
    state, cycle, result = setup_cycle(tmp_path)
    runtime = Runtime()
    worker = SocialExpressionDraftWorker(runtime=runtime, state_root=state)
    value = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert value["status"] == "DRAFTED"
    saved = json.loads((state / "content/continuous/cycles" / cycle / "social_expression_drafts.json").read_text())
    assert saved["publication_authority"] is False
    assert saved["evidence_decision"]["resolution"]["action"] == "DRAFT"
    assert saved["drafts"]["okx"]["intent"] == "OBSERVATION"
    assert saved["drafts"]["x"]["channel"] == "X"
    assert len(saved["drafts"]["okx"]["text"]) <= OKX_SHORT_POST_MAX_CHARS
    assert len(saved["drafts"]["x"]["text"]) <= SOCIAL_LONG_TEXT_MAX_CHARS
    assert "at most 500 characters" in runtime.calls[-1]
    assert "supports long posts and Articles" in runtime.calls[-1]
    assert "STYLE_ONLY" in runtime.calls[-1]
    replay = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert replay["status"] == "ALREADY_DRAFTED"
    assert len(runtime.calls) == 2


def test_seek_then_question_uses_supplemental_evidence_without_forcing_a_claim(tmp_path):
    state, cycle, result = setup_cycle(tmp_path)
    runtime = Runtime("seek_question")
    worker = SocialExpressionDraftWorker(runtime=runtime, state_root=state, evidence_collector=fake_market_evidence)
    value = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert value["status"] == "DRAFTED"
    saved = json.loads((state / "content/continuous/cycles" / cycle / "social_expression_drafts.json").read_text())
    assert saved["evidence_decision"]["plan"]["action"] == "SEEK"
    assert saved["evidence_decision"]["resolution"]["action"] == "QUESTION"
    assert saved["evidence_decision"]["supplemental_evidence"]["source"] == "OKX public API"
    assert saved["drafts"]["okx"]["intent"] == "QUESTION"
    assert saved["drafts"]["x"]["intent"] == "QUESTION"
    assert len(runtime.calls) == 3


def test_skip_does_not_force_social_draft(tmp_path):
    state, cycle, result = setup_cycle(tmp_path)
    runtime = Runtime("skip")
    worker = SocialExpressionDraftWorker(runtime=runtime, state_root=state)
    value = asyncio.run(worker.after_content_cycle(result=result, now=NOW))
    assert value["status"] == "SKIPPED"
    saved = json.loads((state / "content/continuous/cycles" / cycle / "social_expression_drafts.json").read_text())
    assert saved["status"] == "SKIPPED"
    assert saved["drafts"] == {}
    assert saved["evidence_decision"]["resolution"]["action"] == "SKIP"
    assert len(runtime.calls) == 1


def test_social_expression_requires_published_worth_saying_cycle(tmp_path):
    state, _cycle, result = setup_cycle(tmp_path)
    worker = SocialExpressionDraftWorker(runtime=Runtime(), state_root=state)
    assert asyncio.run(worker.after_content_cycle(result={**result, "status": "HELD_BY_CHECKS"}, now=NOW))["status"] == "NO_PUBLISHED_EXPRESSION"


def test_channel_formats_allow_articles_but_keep_okx_feed_short():
    okx = ChannelSocialDraft(
        channel="OKX", format="SHORT_POST", intent="OBSERVATION", text="x" * OKX_SHORT_POST_MAX_CHARS,
        style_observation_ids=(), rationale="Compact OKX boundary test.",
    )
    assert len(okx.text) == OKX_SHORT_POST_MAX_CHARS
    try:
        ChannelSocialDraft(
            channel="OKX", format="SHORT_POST", intent="OBSERVATION", text="x" * (OKX_SHORT_POST_MAX_CHARS + 1),
            style_observation_ids=(), rationale="Too long for OKX feed.",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("OKX over-limit feed post was accepted")

    okx_article = ChannelSocialDraft(
        channel="OKX", format="ARTICLE", intent="EXPLAINER", text="x" * 1200,
        style_observation_ids=(), rationale="Long-form OKX article is a separate format.",
    )
    x_long = ChannelSocialDraft(
        channel="X", format="LONG_POST", intent="UPDATE", text="x" * 1200,
        style_observation_ids=(), rationale="Premium X can carry a longer social post.",
    )
    x_article = ChannelSocialDraft(
        channel="X", format="ARTICLE", intent="EXPLAINER", text="x" * 2500,
        style_observation_ids=(), rationale="X Article is a separate long-form expression.",
    )
    assert len(okx_article.text) == 1200
    assert len(x_long.text) == 1200
    assert len(x_article.text) == 2500
    assert len(x_article.text) <= SOCIAL_LONG_TEXT_MAX_CHARS


def test_okx_public_enrichment_includes_volume_funding_and_open_interest(monkeypatch):
    import wechat_traffic_agent.social_evidence as module

    spots = []
    for symbol, last, opened, volume in [
        ("BTC", "75644.9", "76470.2", "600253299"), ("ETH", "2400", "2450", "300000000"),
        ("SOL", "97", "100", "120000000"), ("XRP", "1.2736", "1.4104", "125802084"),
        ("BNB", "700", "705", "50000000"),
    ]:
        spots.append({"instId": f"{symbol}-USDT", "last": last, "open24h": opened,
                      "volCcy24h": volume, "ts": "1789568476000"})

    def fake_read(url):
        if "market/tickers" in url:
            return {"code": "0", "data": spots}
        if "open-interest" in url:
            return {"code": "0", "data": [
                {"instId": "BTC-USDT-SWAP", "oiCcy": "28422.77", "ts": "1789568476992"},
                {"instId": "XRP-USDT-SWAP", "oiCcy": "79501347", "ts": "1789568477100"},
            ]}
        symbol = url.split("instId=")[1].split("-")[0]
        rate = "-0.0000246" if symbol == "XRP" else "0.0000169"
        return {"code": "0", "data": [{"instId": f"{symbol}-USDT-SWAP", "fundingRate": rate,
                                              "ts": "1789568462000"}]}

    monkeypatch.setattr(module, "_read_json_url", fake_read)
    value = asyncio.run(collect_okx_social_market_context(NOW))
    assert value["status"] == "AVAILABLE"
    assert "spot quote volume 24h" in value["summary"]
    assert "swap funding" in value["summary"]
    assert "swap OI" in value["summary"]
    xrp = next(row for row in value["details"] if row["symbol"] == "XRP")
    assert xrp["swap_funding_rate"] < 0
    assert xrp["swap_open_interest_base_ccy"] == 79501347.0
