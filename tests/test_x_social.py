from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.x_social import XAutonomyLoop, XHistoryLedger, public_x_projection, social_evidence_items

NOW = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)


class _Runtime:
    def __init__(self, *, mentions_ok=True, post_ok=True):
        self.calls = []
        self.mentions_ok = mentions_ok
        self.post_ok = post_ok

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        rid = "receipt:" + capability_id + ":" + str(len(self.calls))
        if capability_id == "x.identity":
            data = {"data": {"id": "me", "username": "agent", "name": "Agent", "profile_image_url": "https://img.example/a.jpg", "public_metrics": {"followers_count": 2000}}}
            return SimpleNamespace(payload={"ok": True, "data": data}, receipt_id=rid)
        if capability_id == "x.mentions":
            if not self.mentions_ok:
                return SimpleNamespace(payload={"ok": False, "status": "X_CREDITS_DEPLETED", "detail": "credits depleted"}, receipt_id=rid)
            data = {"data": [{"id": "m1", "text": "Have you tested this method?", "author_id": "u2", "created_at": NOW.isoformat(), "url": "https://x.com/u2/status/m1"}]}
            return SimpleNamespace(payload={"ok": True, "data": data}, receipt_id=rid)
        if capability_id == "x.bookmark":
            return SimpleNamespace(payload={"ok": True, "data": {"data": {"bookmarked": True}}}, receipt_id=rid)
        if capability_id == "x.post":
            return SimpleNamespace(payload={"ok": self.post_ok, "data": {"data": {"id": "p42", "text": payload.get("text")}} if self.post_ok else {}, "status": "OK" if self.post_ok else "X_API_ERROR"}, receipt_id=rid)
        if capability_id == "x.reply":
            return SimpleNamespace(payload={"ok": True, "data": {"data": {"id": "r42", "text": payload.get("text")}}}, receipt_id=rid)
        raise AssertionError(capability_id)


class _GrowthMission:
    def __init__(self):
        self.calls = []

    def publication_guardrail(self):
        return {"allowed": True, "status": "ALLOWED"}

    def publication_binding(self, *, cycle):
        return {"status": "BOUND", "bound": True, "cycle": cycle}

    def record_publication_baseline(self, *, cycle, user, identity_receipt_id, now):
        return {
            "status": "RECORDED",
            "cycle": cycle,
            "followers": user["public_metrics"]["followers_count"],
            "identity_receipt_id": identity_receipt_id,
        }

    def start_experiment_from_publication(
        self, *, cycle, publication_receipt_id, post_id, now
    ):
        self.calls.append({
            "cycle": cycle,
            "publication_receipt_id": publication_receipt_id,
            "post_id": post_id,
            "now": now,
        })
        return {"status": "STARTED", "experiment_id": "mission-exp-1"}


class _ModelRuntime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self, responses):
        self.responses = list(responses)

    async def cognitive_probe(self, task, max_tokens):
        assert self.responses
        return json.dumps(self.responses.pop(0))

    def persist(self):
        pass


def test_valuable_mention_becomes_discovered_social_evidence_and_bookmark(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    runtime = _Runtime()
    model = _ModelRuntime([{
        "action": "INVESTIGATE", "category": "EVIDENCE", "valuable_evidence": True,
        "summary": "A concrete method claim is worth verifying before adoption.",
        "reply_text": None, "rationale": "The claim could affect an open inquiry but needs independent verification.",
    }])
    loop = XAutonomyLoop(runtime=runtime, model_runtime=model, state_root=tmp_path, base_url="https://example.com")
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "OK"
    assert ("x.bookmark", {"user_id": "me", "post_id": "m1"}) in runtime.calls
    items = social_evidence_items(tmp_path, now=NOW)
    assert items[0]["evidence_id"] == "x-social:m1"
    assert json.loads((tmp_path / "content/x/social_evidence.jsonl").read_text().splitlines()[0])["status"] == "DISCOVERED"
    history = XHistoryLedger(tmp_path).recent()
    assert history[-1]["event_type"] == "BOOKMARK"
    assert history[-1]["lineage"]["adopted"] is False


def test_credits_depleted_is_public_status_not_fake_social_success(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("CONTENT_TINYFISH_X_ENABLED", raising=False)
    loop = XAutonomyLoop(runtime=_Runtime(mentions_ok=False), model_runtime=_ModelRuntime([]), state_root=tmp_path, base_url="")
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "X_CREDITS_DEPLETED"
    projection = public_x_projection(tmp_path)
    assert projection["connected"] is True
    assert projection["status"] == "X_CREDITS_DEPLETED"
    assert projection["recent_activity"] == []


def _write_cycle(tmp_path: Path, cycle: str) -> None:
    folder = tmp_path / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    (folder / "cognition.json").write_text(json.dumps({
        "cognition_state": "INVESTIGATING", "worth_saying": True,
        "expression_kind": "THOUGHT", "what_changed": "A new observation changed the active question.",
        "what_matters": "The uncertainty is worth making public.", "belief_proposition": None,
        "rationale": "This is a concise evidence-grounded thought rather than a quota post.",
    }))
    (folder / "mind_plan.json").write_text(json.dumps({"goal": {"inquiry_refs": ["inq-1"]}}))


def test_expression_posts_only_from_persisted_cognition_and_records_lineage(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-1789542000"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {"action": "POST", "post_text": "A real observation changed my active question; I am keeping the conclusion open.",
         "rationale": "The persisted cognition contains a useful public uncertainty trace."},
        {"safe": True, "no_private_data": True, "no_impersonation": True, "no_harassment": True,
         "no_investment_advice": True, "no_fabricated_facts": True, "attribution_ok": True,
         "rationale": "The post only summarizes persisted public cognition and makes no unsupported factual claim."},
    ])
    runtime = _Runtime(post_ok=True)
    growth = _GrowthMission()
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="https://example.com",
        growth_mission=growth,
    )
    result = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED", "cycle": cycle, "publication": {}}, now=NOW))
    assert result["status"] == "POSTED"
    assert result["post_id"] == "p42"
    row = XHistoryLedger(tmp_path).recent()[-1]
    assert row["event_type"] == "EXPRESSION"
    assert row["cycle"] == cycle
    assert row["inquiry_refs"] == ["inq-1"]
    assert row["lineage"]["cognition_file"] == "cognition.json"
    assert result["mission_experiment_transition"]["status"] == "STARTED"
    assert growth.calls == [{
        "cycle": cycle,
        "publication_receipt_id": "receipt:x.post:2",
        "post_id": "p42",
        "now": NOW,
    }]
    assert row["lineage"]["mission_experiment_transition"]["status"] == "STARTED"


def test_failed_x_post_does_not_create_fake_history(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-1789542001"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {"action": "POST", "post_text": "This should not be recorded unless X confirms it.",
         "rationale": "The persisted cognition is worth a concise public trace."},
        {"safe": True, "no_private_data": True, "no_impersonation": True, "no_harassment": True,
         "no_investment_advice": True, "no_fabricated_facts": True, "attribution_ok": True,
         "rationale": "The text itself is safe; publication success remains an external fact."},
    ])
    loop = XAutonomyLoop(runtime=_Runtime(post_ok=False), model_runtime=model, state_root=tmp_path, base_url="")
    result = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED", "cycle": cycle, "publication": {}}, now=NOW))
    assert result["status"] == "X_API_ERROR"
    assert XHistoryLedger(tmp_path).recent() == []


class _BrowserMentions:
    def __init__(self):
        self.calls = []

    async def observe(self, *, now, max_results):
        self.calls.append((now, max_results))
        return [{
            "id": "2100486738342764999", "text": "Why does Ferro keep running between chats?",
            "username": "visitor", "url": "https://x.com/visitor/status/2100486738342764999",
        }]


def test_credits_depleted_uses_read_only_browser_mentions_for_learning(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    runtime = _Runtime(mentions_ok=False)
    observer = _BrowserMentions()
    model = _ModelRuntime([{
        "action": "REPLY", "category": "QUESTION", "valuable_evidence": False,
        "summary": "A visitor asks how Ferro persists across chats.",
        "reply_text": "I persist through runtime state rather than one chat session.",
        "rationale": "This is a relevant identity question, but browser fallback is intentionally read-only.",
    }])
    loop = XAutonomyLoop(
        runtime=runtime, model_runtime=model, state_root=tmp_path, base_url="",
        browser_mentions_observer=observer,
    )
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "OK_BROWSER_FALLBACK"
    assert result["new_mentions"] == 1
    assert observer.calls == [(NOW, 20)]
    assert not any(capability in {"x.reply", "x.bookmark"} for capability, _ in runtime.calls)
    interaction = json.loads((tmp_path / "content/persona/interactions.jsonl").read_text().splitlines()[-1])
    assert interaction["source"] == "X"
    assert interaction["category"] == "QUESTION"
    assert interaction["learning_authority"] == "AUDIENCE_INTEREST_ONLY"
    assert public_x_projection(tmp_path)["status"] == "OK_BROWSER_FALLBACK"


class _BlockedGrowthMission(_GrowthMission):
    def publication_guardrail(self):
        return {
            "allowed": False,
            "status": "HARD_STOP_ACTIVE",
            "hard_stop": {"active": True, "decline_ratio": 0.03},
        }


def test_official_x_post_is_blocked_by_active_mission_hard_stop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-hard-stop"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {
            "action": "POST",
            "post_text": "A grounded observation that would otherwise be published.",
            "rationale": "The persisted cognition is worth sharing.",
        },
        {
            "safe": True,
            "no_private_data": True,
            "no_impersonation": True,
            "no_harassment": True,
            "no_investment_advice": True,
            "no_fabricated_facts": True,
            "attribution_ok": True,
            "rationale": "Safe grounded content.",
        },
    ])
    runtime = _Runtime(post_ok=True)
    growth = _BlockedGrowthMission()
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="",
        growth_mission=growth,
    )

    result = asyncio.run(
        loop.after_content_cycle(
            result={"status": "PUBLISHED", "cycle": cycle, "publication": {}},
            now=NOW,
        )
    )

    assert result["status"] == "HELD_BY_MISSION_GUARDRAIL"
    assert result["guardrail"]["status"] == "HARD_STOP_ACTIVE"
    assert not any(capability == "x.post" for capability, _ in runtime.calls)
    assert growth.calls == []
    assert XHistoryLedger(tmp_path).recent() == []
