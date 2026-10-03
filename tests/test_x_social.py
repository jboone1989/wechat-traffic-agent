from __future__ import annotations

import asyncio
import pytest
from wechat_traffic_agent.browser_runtime import BrowserRuntimeUnavailable
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.x_social import XAutonomyLoop, XHistoryLedger, public_x_projection, social_evidence_items
from wechat_traffic_agent.interoceptive_fault import classify_task_result

NOW = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)


def _x_strategy(**overrides):
    row = {
        "schema_version": "ferro-l1-x-strategy.v1",
        "status": "SELECTED",
        "post_timing": "POST_NOW",
        "x_format": "SHORT_POST",
        "exploration_mode": "EXPLOIT",
        "angle": "EVIDENCE_FIRST",
        "interaction_mode": "ORIGINAL",
        "provider": "TEST",
        "model": "test",
    }
    row.update(overrides)
    return row


class _L1:
    def __init__(self, attention=None):
        self.attention = attention or {
            "action": "IGNORE",
            "category": "REACTION",
            "valuable_evidence": False,
        }
        self.calls = []
        self.strategy_calls = []

    def choose_x_discovery(self, **kwargs):
        return {"source":"MENTIONS"}

    def choose_x_strategy(self, **kwargs):
        self.strategy_calls.append(kwargs)
        return _x_strategy()

    def choose_social_attention(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "schema_version": "ferro-l1-social-attention.v1",
            "status": "ROUTED",
            "action": self.attention["action"],
            "category": self.attention["category"],
            "valuable_evidence": bool(self.attention["valuable_evidence"]),
            "provider": "TEST",
            "model": "test",
            "confidence": {"social_action": 1.0},
            "latency_ms": 0,
            "decided_at": NOW.isoformat(),
        }


class _Runtime:
    def __init__(self, *, mentions_ok=True, post_ok=True, post_status=None):
        self.calls = []
        self.mentions_ok = mentions_ok
        self.post_ok = post_ok
        self.post_status = post_status

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
            raise AssertionError("Original publication must never call the X API")
            status = "OK" if self.post_ok else (self.post_status or "X_API_ERROR")
            return SimpleNamespace(
                payload={
                    "ok": self.post_ok,
                    "data": {"data": {"id": "p42", "text": payload.get("text")}} if self.post_ok else {},
                    "status": status,
                    "detail": "credits depleted" if status == "X_CREDITS_DEPLETED" else "",
                },
                receipt_id=rid,
            )
        if capability_id == "x.reply":
            return SimpleNamespace(payload={"ok": True, "data": {"data": {"id": "r42", "text": payload.get("text")}}}, receipt_id=rid)
        raise AssertionError(capability_id)


class _GrowthMission:
    def __init__(self):
        self.calls = []

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


class _XOnlyGrowthMission(_GrowthMission):
    def publication_binding(self, *, cycle):
        return {"status": "BOUND_X_EXPRESSION", "bound": True, "cycle": cycle}


class _ModelRuntime:
    agent_manifest = SimpleNamespace(agent_type="content-agent")

    def __init__(self, responses):
        self.responses = list(responses)
        self.tasks = []
        attention = None
        if self.responses and isinstance(self.responses[0], dict):
            candidate = self.responses[0]
            if {"action", "category", "valuable_evidence"} <= set(candidate):
                attention = self.responses.pop(0)
                if attention.get("action") == "REPLY":
                    self.responses.insert(0, {
                        "reply_text": attention.get("reply_text") or "Thanks for the question.",
                        "rationale": attention.get("rationale") or "A direct grounded reply is useful here.",
                    })
        self.l1_decisions = _L1(attention)

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        assert self.responses
        self.tasks.append(task)
        response = dict(self.responses.pop(0))
        if task.startswith("X_EXPRESSION_DRAFT"):
            response.pop("action", None)
        return json.dumps(response)

    def persist(self):
        pass



class _BrowserTransport:
    def __init__(self, *, ok=True, mentions_ok=True):
        self.ok, self.calls, self.mentions_ok = ok, [], mentions_ok
    async def identity(self):
        return {"id":"me","username":"agent","public_metrics":{"followers_count":2000}}
    async def discover(self, *, source, max_results):
        if not self.mentions_ok:
            raise RuntimeError("fixture: browser unavailable")
        return [{"id":"m1","text":"Have you tested this method?", "author_id":"u2", "username":"visitor", "url":"https://x.com/u2/status/m1"}]
    async def reply(self, *, text, target_url):
        self.calls.append({"reply_text":text,"target_url":target_url})
        return SimpleNamespace(post_id="r42",url="https://x.com/agent/status/r42",handle="agent",provider="JEV_AGENT_RUNTIME")
    async def publish(self, *, text):
        self.calls.append(text)
        if not self.ok:
            raise BrowserRuntimeUnavailable("Jev browser publication unavailable")
        return SimpleNamespace(post_id="p42",url="https://x.com/agent/status/p42",
                               handle="agent",provider="JEV_AGENT_RUNTIME")

@pytest.fixture(autouse=True)
def _inject_browser_transport(monkeypatch):
    original = XAutonomyLoop.__init__
    def initialize(self, **kwargs):
        original(self, **kwargs)
        if self.browser_publish_transport is None:
            self.browser_publish_transport = _BrowserTransport(
                ok=getattr(self.runtime,"post_ok",True),mentions_ok=getattr(self.runtime,"mentions_ok",True))
    monkeypatch.setattr(XAutonomyLoop,"__init__",initialize)

def test_valuable_mention_becomes_discovered_social_evidence_and_bookmark(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    runtime = _Runtime()
    observed_social = []
    model = _ModelRuntime([{
        "action": "INVESTIGATE", "category": "EVIDENCE", "valuable_evidence": True,
        "summary": "A concrete method claim is worth verifying before adoption.",
        "reply_text": None, "rationale": "The claim could affect an open inquiry but needs independent verification.",
    }])
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="https://example.com",
        social_observer=lambda **kwargs: observed_social.append(kwargs),
        social_context_provider=lambda actor: {
            "actor": "actor:pseudonymous",
            "interactions": 4,
            "familiarity": 0.55,
            "valence": 0.2,
            "approach_bias": 0.65,
            "last_signal": "QUESTION",
        } if actor == "u2" else None,
    )
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "OK"
    assert not runtime.calls
    items = social_evidence_items(tmp_path, now=NOW)
    assert items[0]["evidence_id"] == "x-social:m1"
    assert json.loads((tmp_path / "content/x/social_evidence.jsonl").read_text().splitlines()[0])["status"] == "DISCOVERED"
    history = XHistoryLedger(tmp_path).recent()
    assert not history
    assert observed_social == [{
        "actor": "u2",
        "category": "SOCIAL",
        "signal": "EVIDENCE",
        "now": NOW,
    }]
    assert model.tasks == []
    l1_call = model.l1_decisions.calls[0]
    assert l1_call["relationship"]["interactions"] == 4
    assert l1_call["relationship"]["familiarity"] == 0.55


def test_read_only_x_observation_never_replies_or_bookmarks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    runtime = _Runtime()
    model = _ModelRuntime([{
        "action": "REPLY", "category": "EVIDENCE", "valuable_evidence": True,
        "summary": "The mention is relevant enough to inspect without writing to X.",
        "reply_text": "This reply must not be sent in read-only observation mode.",
        "rationale": "Owner control requested observation only, so no remote write is authorized.",
    }])
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="https://example.com",
    )

    result = asyncio.run(loop.observe(now=NOW, allow_remote_actions=False))

    assert result["status"] == "OK"
    assert not any(capability in {"x.reply", "x.bookmark"} for capability, _ in runtime.calls)


def test_credits_depleted_is_public_status_not_fake_social_success(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CONTENT_X_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("CONTENT_TINYFISH_X_ENABLED", raising=False)
    class UnavailableBrowser:
        async def observe(self, **kwargs):
            raise RuntimeError("fixture: authenticated browser unavailable")
    loop = XAutonomyLoop(runtime=_Runtime(mentions_ok=False), model_runtime=_ModelRuntime([]), state_root=tmp_path, base_url="", browser_mentions_observer=UnavailableBrowser())
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "X_OBSERVATION_FAILED"
    projection = public_x_projection(tmp_path)
    assert projection["connected"] is True
    assert projection["status"] == "X_OBSERVATION_FAILED"
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
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "schema_version": "content-social-expression-drafts.v2",
        "status": "DRAFT_READY",
        "cycle": cycle,
        "publication_authority": False,
        "x_strategy": _x_strategy(),
        "drafts": {},
    }))


def test_x_mission_can_express_grounded_held_website_cycle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-x-only-held"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {
            "action": "POST",
            "post_text": "Checkpoint replay preserves completed work; it does not prove universal crash recovery.",
            "rationale": "The mission-bound cognition is grounded and worth a concise X trace.",
        },
    ])
    runtime = _Runtime(post_ok=True)
    growth = _XOnlyGrowthMission()
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="",
        growth_mission=growth,
    )

    result = asyncio.run(loop.after_content_cycle(
        result={"status": "HELD_BY_CHECKS", "cycle": cycle, "publication": {}},
        now=NOW,
    ))

    assert result["status"] == "POSTED"
    assert not any(capability == "x.post" for capability, _ in runtime.calls)
    assert growth.calls[0]["post_id"] == "p42"


def test_non_mission_held_cycle_still_does_not_expression_post(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-held-no-mission"
    _write_cycle(tmp_path, cycle)
    runtime = _Runtime(post_ok=True)
    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=_ModelRuntime([]),
        state_root=tmp_path,
        base_url="",
    )
    result = asyncio.run(loop.after_content_cycle(
        result={"status": "HELD_BY_CHECKS", "cycle": cycle, "publication": {}},
        now=NOW,
    ))
    assert result["status"] == "NO_EXPRESSION"
    assert not any(capability == "x.post" for capability, _ in runtime.calls)


def test_expression_posts_only_from_persisted_cognition_and_records_lineage(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-1789542000"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {"action": "POST", "post_text": "A real observation changed my active question; I am keeping the conclusion open.",
         "rationale": "The persisted cognition contains a useful public uncertainty trace."},
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
        "publication_receipt_id": f"x-browser-publication:{cycle}:p42",
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
    ])
    loop = XAutonomyLoop(runtime=_Runtime(post_ok=False), model_runtime=model, state_root=tmp_path, base_url="")
    result = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED", "cycle": cycle, "publication": {}}, now=NOW))
    assert result["status"] == "X_PUBLICATION_CAPABILITY_UNAVAILABLE"
    assert XHistoryLedger(tmp_path).recent() == []


def test_x_original_post_uses_authenticated_browser_without_api(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    cycle = "v2-x-credit-browser-fallback"
    _write_cycle(tmp_path, cycle)
    responses = [
        {
            "action": "POST",
            "post_text": "A grounded test post.",
            "rationale": "The persisted cognition is worth sharing.",
        },
    ]

    class BrowserTransport(_BrowserTransport):
        def __init__(self):
            self.calls = []

        async def publish(self, *, text):
            self.calls.append(text)
            return SimpleNamespace(
                post_id="browser-p42",
                url="https://x.com/agent/status/browser-p42",
                handle="agent",
                provider="JEV_AGENT_RUNTIME",
                duplicate_found=False,
            )

    browser = BrowserTransport()
    growth = _GrowthMission()
    loop = XAutonomyLoop(
        runtime=_Runtime(post_ok=False, post_status="X_CREDITS_DEPLETED"),
        model_runtime=_ModelRuntime(responses),
        state_root=tmp_path,
        base_url="",
        browser_publish_transport=browser,
        growth_mission=growth,
    )

    result = asyncio.run(
        loop.after_content_cycle(
            result={"status": "PUBLISHED", "cycle": cycle, "publication": {}},
            now=NOW,
        )
    )

    assert result["status"] == "POSTED"
    assert result["post_id"] == "browser-p42"
    assert result["transport"] == "JEV_AGENT_RUNTIME"
    assert result["transport_failover"] is None
    classified = classify_task_result(
        "CONTENT_COGNITION",
        {"status": "OK", "x_social_expression": result},
    )
    assert not any(
        symptom["kind"] == "fallback_dependency"
        for symptom in classified["symptoms"]
    )
    assert browser.calls == ["A grounded test post."]
    assert growth.calls[0]["publication_receipt_id"] == (
        "x-browser-publication:v2-x-credit-browser-fallback:browser-p42"
    )
    assert not (tmp_path / "content/x/publication_blocker.json").exists()
    row = XHistoryLedger(tmp_path).recent()[-1]
    assert row["lineage"]["publication_transport"] == "JEV_AGENT_RUNTIME"
    assert row["lineage"]["transport_failover"] is None


def test_x_browser_failure_records_a_short_retry_blocker(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    cycle = "v2-x-all-transports-blocked"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([
        {
            "action": "POST",
            "post_text": "A grounded test post.",
            "rationale": "The persisted cognition is worth sharing.",
        },
    ])
    runtime = _Runtime(post_ok=False, post_status="X_CREDITS_DEPLETED")

    class BrokenBrowserTransport(_BrowserTransport):
        async def publish(self, *, text):
            raise RuntimeError("browser session unavailable")

    loop = XAutonomyLoop(
        runtime=runtime,
        model_runtime=model,
        state_root=tmp_path,
        base_url="",
        browser_publish_transport=BrokenBrowserTransport(),
        growth_mission=_GrowthMission(),
    )

    first = asyncio.run(
        loop.after_content_cycle(
            result={"status": "PUBLISHED", "cycle": cycle, "publication": {}},
            now=NOW,
        )
    )
    assert first["status"] == "X_PUBLICATION_CAPABILITY_UNAVAILABLE"
    assert first["blocker"]["resource"] == "X_PUBLICATION_TRANSPORT"
    assert first["blocker"]["required_action"] == "RESTORE_X_PUBLICATION_TRANSPORT"
    assert first["transport_failover"] is None
    assert not runtime.calls
    assert first["blocker"]["transport"] == "JEV_AGENT_RUNTIME"
    blocker = json.loads(
        (tmp_path / "content/x/publication_blocker.json").read_text()
    )
    assert blocker["status"] == "ACTIVE"
    calls_after_first = list(runtime.calls)

    second = asyncio.run(
        loop.after_content_cycle(
            result={"status": "PUBLISHED", "cycle": cycle, "publication": {}},
            now=NOW,
        )
    )
    assert second["status"] == "X_RESOURCE_BLOCKED"
    assert runtime.calls == calls_after_first
    assert model.responses == []


def test_successful_post_after_blocker_expiry_clears_resource_blocker(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    cycle = "v2-x-credit-recovered"
    _write_cycle(tmp_path, cycle)
    blocker_path = tmp_path / "content/x/publication_blocker.json"
    blocker_path.parent.mkdir(parents=True, exist_ok=True)
    blocker_path.write_text(json.dumps({
        "status": "ACTIVE",
        "blocker": "X_CREDITS_DEPLETED",
        "retry_after": (NOW - __import__("datetime").timedelta(seconds=1)).isoformat(),
    }))
    loop = XAutonomyLoop(
        runtime=_Runtime(post_ok=True),
        model_runtime=_ModelRuntime([
            {"action":"POST","post_text":"Recovered publication path.","rationale":"Credits can be retried now."},
        ]),
        state_root=tmp_path,
        base_url="",
        growth_mission=_GrowthMission(),
    )
    result = asyncio.run(loop.after_content_cycle(
        result={"status":"PUBLISHED","cycle":cycle,"publication":{}}, now=NOW,
    ))
    assert result["status"] == "POSTED"
    cleared = json.loads(blocker_path.read_text())
    assert cleared["status"] == "CLEARED"
    assert cleared["cleared_at"] == NOW.isoformat()


class _BrowserMentions:
    def __init__(self):
        self.calls = []

    async def observe(self, *, now, max_results):
        self.calls.append((now, max_results))
        return [{
            "id": "2100486738342764999", "text": "Why does Ferro keep running between chats?",
            "username": "visitor", "url": "https://x.com/visitor/status/2100486738342764999",
        }]


def test_browser_mentions_can_reply_without_api_credits(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    runtime = _Runtime(mentions_ok=False)
    observer = _BrowserMentions()
    model = _ModelRuntime([{
        "action": "REPLY", "category": "QUESTION", "valuable_evidence": False,
        "summary": "A visitor asks how Ferro persists across chats.",
        "reply_text": "I persist through runtime state rather than one chat session.",
        "rationale": "This is a relevant identity question that merits a grounded reply.",
    }])
    loop = XAutonomyLoop(
        runtime=runtime, model_runtime=model, state_root=tmp_path, base_url="",
        browser_mentions_observer=observer,
    )
    result = asyncio.run(loop.observe(now=NOW))
    assert result["status"] == "OK"
    assert result["new_mentions"] == 1
    assert observer.calls == [(NOW, 20)]
    assert not any(capability in {"x.reply", "x.bookmark"} for capability, _ in runtime.calls)
    interaction = json.loads((tmp_path / "content/persona/interactions.jsonl").read_text().splitlines()[-1])
    assert interaction["source"] == "X"
    assert interaction["category"] == "QUESTION"
    assert interaction["learning_authority"] == "AUDIENCE_INTEREST_ONLY"
    assert public_x_projection(tmp_path)["status"] == "OK"
    assert len(model.tasks) == 1



def test_follower_decline_does_not_veto_selected_post(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    cycle = "v2-audience-feedback"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([{
        "post_text": "A grounded observation that Ferro chose to publish.",
        "rationale": "The persisted cognition is worth sharing.",
    }])
    growth = _GrowthMission()
    growth.publication_guardrail = lambda: pytest.fail("Retired hard stop must not be consulted")
    transport = _BrowserTransport()
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path,
        base_url="", growth_mission=growth, browser_publish_transport=transport)
    result = asyncio.run(loop.after_content_cycle(
        result={"status": "PUBLISHED", "cycle": cycle, "publication": {}}, now=NOW))
    assert result["status"] == "POSTED"
    assert len(model.tasks) == 1
    assert len(transport.calls) == 1
    assert len(growth.calls) == 1


def test_baseline_failure_is_visible_and_does_not_veto_post(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONTENT_X_PUBLICATION_ENABLED", "1")
    cycle = "v2-measurement-failure"
    _write_cycle(tmp_path, cycle)
    model = _ModelRuntime([{"post_text": "Ferro can publish a grounded update.",
        "rationale": "Useful evidence is ready to share."}])
    growth = _GrowthMission()
    def unavailable(**kwargs):
        raise RuntimeError("follower baseline unavailable")
    growth.record_publication_baseline = unavailable
    transport = _BrowserTransport()
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path,
        base_url="", growth_mission=growth, browser_publish_transport=transport)
    result = asyncio.run(loop.after_content_cycle(
        result={"status": "PUBLISHED", "cycle": cycle, "publication": {}}, now=NOW))
    assert result["status"] == "POSTED"
    assert result["mission_baseline"]["status"] == "MEASUREMENT_FAILED"
    assert "follower baseline unavailable" in result["mission_baseline"]["detail"]
    assert len(transport.calls) == 1
    measurement = json.loads((tmp_path / "content/continuous/cycles" / cycle / "x_publication_measurement.json").read_text())
    assert measurement == result["mission_baseline"]


def test_x_only_cycle_without_social_draft_gets_real_l1_strategy_before_l2_text(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "v2-x-l1-direct"
    _write_cycle(tmp_path, cycle)
    folder = tmp_path / "content/continuous/cycles" / cycle
    (folder / "social_expression_drafts.json").unlink()
    model = _ModelRuntime([
        {
            "action": "POST",
            "post_text": "Jev chose the action; Sol only wrote this grounded sentence.",
            "rationale": "The persisted cognition is worth a concise trace.",
        },
    ])
    loop = XAutonomyLoop(
        runtime=_Runtime(post_ok=True),
        model_runtime=model,
        state_root=tmp_path,
        base_url="",
        growth_mission=_XOnlyGrowthMission(),
    )

    result = asyncio.run(
        loop.after_content_cycle(
            result={"status": "HELD_BY_CHECKS", "cycle": cycle, "publication": {}},
            now=NOW,
        )
    )

    assert result["status"] == "POSTED"
    assert model.l1_decisions.strategy_calls
    strategy = json.loads((folder / "x_l1_strategy.json").read_text())
    assert strategy["post_timing"] == "POST_NOW"
    assert model.tasks[0].startswith("X_EXPRESSION_DRAFT")
    assert "Decide whether" not in model.tasks[0]


@pytest.mark.parametrize("result_on_attempt", [2, None])
def test_empty_search_refines_from_feedback_then_handles_real_results(tmp_path, result_on_attempt):
    class SearchL1(_L1):
        def choose_x_discovery(self, **kwargs):
            return {"source": "SEARCH"}

    class SearchTransport(_BrowserTransport):
        def __init__(self):
            super().__init__()
            self.queries = []

        async def discover(self, *, source, query, max_results):
            assert source == "SEARCH"
            self.queries.append(query)
            return ([{"id": "search-1", "text": "What makes AI agent evaluations reliable?",
                      "username": "researcher", "url": "https://x.com/researcher/status/search-1"}]
                    if len(self.queries) == result_on_attempt else [])

    plans = [{"domain": domain, "query": query, "question": "What should I investigate?",
              "rationale": "I will refine my exploration using the actual results."}
             for domain, query in [("CRYPTOCURRENCY", "crypto drawdown evidence"),
                                   ("AI", "AI agent evaluation"),
                                   ("FINANCE", "bond term premium")]]
    model = _ModelRuntime(plans)
    model.l1_decisions = SearchL1()
    transport = SearchTransport()
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path,
                         base_url="https://example.org", browser_publish_transport=transport)
    result = asyncio.run(loop.observe(now=NOW, allow_remote_actions=False))
    expected_attempts = result_on_attempt or 3
    assert len(transport.queries) == expected_attempts
    assert result["mention_count"] == (1 if result_on_attempt else 0)
    assert result["discovery_outcome"] == ("OBSERVED_POSTS" if result_on_attempt else "EMPTY_RESULTS")
    assert len(model.l1_decisions.calls) == (1 if result_on_attempt else 0)
    assert transport.calls == []
    second_prompt = model.tasks[1]
    feedback = json.JSONDecoder().raw_decode(second_prompt.split("\n", 1)[1])[0]["previous_search_feedback"]
    assert feedback["decision"]["query"] == plans[0]["query"]
    assert feedback["post_count"] == 0
    assert len(result["discovery"]["search_attempts"]) == expected_attempts


@pytest.mark.parametrize("repair_passes", [True, False])
def test_research_post_quality_repairs_thin_text_before_any_browser_write(tmp_path, monkeypatch, repair_passes):
    monkeypatch.setenv("CONTENT_X_WRITES_ENABLED", "1")
    cycle = "research-quality"
    _write_cycle(tmp_path, cycle)
    folder = tmp_path / "content/continuous/cycles" / cycle
    source_quote = "Volatility filtering recovered eleven regimes versus six in a size-matched random sample."
    learning = {"cycle": cycle, "progress": "LEARNED",
        "focus": {"question": "How does sampling change regime discovery?"},
        "learning_delta": "Filtering changes the discovered regime count.",
        "sources": [{"evidence_id": "paper", "summary": source_quote, "title": "Regime clustering study", "url": "https://example.org/paper"}],
        "facts": [{"evidence_id": "paper", "quote": source_quote, "observation": "The comparison reports eleven versus six regimes."}]}
    (folder / "learning_result.json").write_text(json.dumps(learning))
    thin = "Regimes need validation on unseen data before we can trust them."
    repaired = "A clustering study reports 11 regimes with volatility filtering versus 6 with size-matched random sampling. I still need to test prediction."
    rejected = {"concrete_discovery": False, "faithful_to_sources": True, "adds_to_recent_shares": False,
                "source_fact_id": None, "post_quote": None, "rationale": "The post drops the supplied measured comparison and repeats a validation reminder."}
    approved = {"concrete_discovery": True, "faithful_to_sources": True, "adds_to_recent_shares": True,
                "source_fact_id": "fact-0", "post_quote": "11 regimes with volatility filtering versus 6",
                "rationale": "The attributed sampling comparison is concrete and distinct from prior generic validation posts."}
    model = _ModelRuntime([
        {"post_text": thin, "rationale": "A bounded research question."}, rejected,
        {"post_text": repaired, "rationale": "Retain the source's measured comparison."},
        approved if repair_passes else rejected])
    transport = _BrowserTransport()
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path,
        base_url="", growth_mission=_XOnlyGrowthMission(), browser_publish_transport=transport)
    result = asyncio.run(loop.after_content_cycle(result={"status": "PUBLISHED", "cycle": cycle}, now=NOW))
    assert result["status"] == ("POSTED" if repair_passes else "HELD_BY_LEARNING_QUALITY")
    assert transport.calls == ([repaired] if repair_passes else [])
    assert len(model.tasks) == 4
    assert json.loads((folder / "x_learning_quality_0.json").read_text())["passed"] is False
    assert json.loads((folder / "x_learning_quality_1.json").read_text())["passed"] is repair_passes


def test_learning_quality_rejects_forged_post_quote_or_source_reference(tmp_path):
    from wechat_traffic_agent.learning_expression_quality import review_learning_expression
    quote = "Volatility filtering recovered eleven distinct Bitcoin market regimes."
    learning = {"progress": "LEARNED", "sources": [{"evidence_id": "paper", "summary": quote}],
                "facts": [{"evidence_id": "paper", "quote": quote}]}
    forged = {"concrete_discovery": True, "faithful_to_sources": True, "adds_to_recent_shares": True,
              "source_fact_id": "fact-0", "post_quote": "Eleven regimes improve future predictions.",
              "rationale": "This invented assertion must not pass exact public text validation."}
    with pytest.raises(ValueError, match="exact excerpt"):
        asyncio.run(review_learning_expression(runtime=_ModelRuntime([forged, forged]), folder=tmp_path,
            learning=learning, text="The study reports eleven regimes.", recent_shares=[], label="quality"))


@pytest.mark.parametrize("format_name", ["LONG_POST", "ARTICLE"])
def test_cached_research_longform_cannot_bypass_quality_before_native_publisher(tmp_path, format_name):
    cycle = "cached-research-" + format_name
    folder = tmp_path / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True)
    quote = "Volatility filtering recovered eleven regimes versus six in a random sample."
    learning = {"progress": "LEARNED", "sources": [{"evidence_id": "paper", "summary": quote}],
                "facts": [{"evidence_id": "paper", "quote": quote}]}
    draft = {"channel": "X", "format": format_name, "title": "A thin research note",
             "text": "Market models require more evidence before adoption.",
             "style_observation_ids": [], "rationale": "The draft repeats a broad verification principle."}
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "status": "DRAFT_READY", "drafts": {"x": draft}, "x_strategy": {}}))
    rejected = {"concrete_discovery": False, "faithful_to_sources": True, "adds_to_recent_shares": False,
                "source_fact_id": None, "post_quote": None,
                "rationale": "This draft repeats a generic reminder and contains no concrete observation."}
    transport = _BrowserTransport()
    model = _ModelRuntime([rejected, draft, rejected])
    loop = XAutonomyLoop(runtime=_Runtime(), model_runtime=model, state_root=tmp_path,
        base_url="", browser_publish_transport=transport)
    result = asyncio.run(loop._owner_longform(folder=folder,
        context={"learning": learning, "owner_publication_request": {}}, now=NOW,
        surface="X_ARTICLE" if format_name == "ARTICLE" else "X_LONG_POST"))
    assert result["status"] == "HELD_BY_LEARNING_QUALITY"
    assert transport.calls == []
    assert len(model.tasks) == 3
