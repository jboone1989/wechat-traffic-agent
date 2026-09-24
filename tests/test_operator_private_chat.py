from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from wechat_traffic_agent.live_agent import LiveAgentService
from wechat_traffic_agent.memory_system import CanonicalMemoryStore
from wechat_traffic_agent.operator_private_chat import (
    OperatorConversationStore,
    ReplyJSONStreamDecoder,
    parse_model_json,
    parse_owner_envelope,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.active_goals = []
        self.external_missions = []
        self.persisted = 0

    def submit_goal(self, description, reason="", *, auto_complete=True):
        goal = SimpleNamespace(
            goal_id="goal:owner-chat:1",
            description=description,
            origin="USER",
            status="ACTIVE",
            priority=0.4,
        )
        self.active_goals.append(goal)
        return goal

    def submit_external_mission(
        self, description, *, metric, baseline, target, deadline, direction,
        constraints=None, evidence_refs=None, issued_by="operator",
    ):
        mission = SimpleNamespace(
            mission_id="mission:owner-chat:1",
            goal_id="goal:owner-chat:mission:1",
            description=description,
            metric=metric,
            baseline=baseline,
            target=target,
            deadline=deadline,
            direction=direction,
            status="ACTIVE",
        )
        self.external_missions.append(mission)
        return mission

    def persist(self) -> None:
        self.persisted += 1


class FakeApplication:
    def __init__(self) -> None:
        self.domain_runtime = FakeRuntime()

    def status_observation(self):
        return {"generation_ready": True, "publish_mode": "website_automatic"}
class StructuredProvider:
    def __init__(self) -> None:
        self.calls = []
        self.stream_calls = []

    @staticmethod
    def response() -> str:
        return json.dumps({
            "reply": "这个目标已经足够具体，我先把它整理成目标草案，正式激活前不会执行。",
            "goal_candidate": {
                "kind": "GENERAL",
                "description": "Build a durable private owner communication channel",
                "reason": "Owner and Ferro need to discuss goals before committing them.",
                "constraints": ["private owner context", "no hidden chain-of-thought"],
            },
            "memory_candidates": [],
        }, ensure_ascii=False)

    async def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.response()

    async def stream(self, prompt, **kwargs):
        self.stream_calls.append((prompt, kwargs))
        raw = self.response()
        for offset in range(0, len(raw), 7):
            yield raw[offset:offset + 7]


class FakeBackend:
    def __init__(self) -> None:
        self.provider = StructuredProvider()


class FakeWakeful:
    def __init__(self) -> None:
        self.calls = []

    def public_snapshot(self):
        return {}

    def schedule_goal_followup(self, now, *, seconds=5, relevant_refs=()):
        self.calls.append((now, seconds, tuple(relevant_refs)))


def _login(client: TestClient, root: Path) -> None:
    token = (root / "secrets" / "operator-console.token").read_text(encoding="utf-8").strip()
    response = client.post(
        "/operator/api/session",
        headers={"X-Operator-Request": "1"},
        json={"token": token},
    )
    assert response.status_code == 200
def test_parse_model_json_accepts_fenced_object():
    parsed = parse_model_json("""```json
{"reply":"ok","goal_candidate":null}
```""")
    assert parsed == {"reply": "ok", "goal_candidate": None}


def test_reply_json_stream_decoder_handles_chunk_boundaries_and_escapes():
    decoder = ReplyJSONStreamDecoder()
    chunks = [
        '{"re',
        'ply":"你',
        '好\\n下一行 \\uD83D',
        '\\uDE80","goal_candidate":null}',
    ]

    visible = "".join(decoder.feed(chunk) for chunk in chunks)

    assert visible == "你好\n下一行 🚀"
    assert decoder.started is True
    assert decoder.finished is True


def test_reply_decoder_rejects_reasoning_before_root_json():
    decoder = ReplyJSONStreamDecoder()

    visible = decoder.feed('We need to reason first. Example: {"reply":"do not leak"}')

    assert visible == ""
    assert decoder.rejected is True
    assert decoder.started is False


def test_parse_owner_envelope_rejects_embedded_or_incomplete_json():
    assert parse_owner_envelope(
        '{"reply":"ok","goal_candidate":null,"memory_candidates":[]}'
    ) == {
        "reply": "ok",
        "goal_candidate": None,
        "memory_candidates": [],
        "tool_requests": [],
    }
    assert parse_owner_envelope(
        'analysis first {"reply":"ok","goal_candidate":null,"memory_candidates":[]}'
    ) is None
    assert parse_owner_envelope('{"reply":"partial') is None


def test_operator_conversation_store_retrieves_relevant_archived_turn_with_neighbor(tmp_path):
    store = OperatorConversationStore(tmp_path)
    store.append("operator", "你是谁")
    store.append("ferro", "I am Ferro, the persistent agent on LivingRuntime, serving as your content agent.")
    for index in range(6):
        store.append("operator", f"unrelated filler {index}")
        store.append("ferro", f"ack {index}")

    found = store.relevant_archive(
        "我最早问‘你是谁’时，你当时用什么语言回答？", recent_limit=8, limit=6
    )

    texts = [row["text"] for row in found]
    assert "你是谁" in texts
    assert "I am Ferro, the persistent agent on LivingRuntime, serving as your content agent." in texts


def test_operator_conversation_store_does_not_return_unrelated_archive(tmp_path):
    store = OperatorConversationStore(tmp_path)
    store.append("operator", "ancient unrelated note about invoices")
    store.append("ferro", "acknowledged")
    for index in range(5):
        store.append("operator", f"recent filler {index}")
        store.append("ferro", f"recent ack {index}")

    assert store.relevant_archive("量子纠缠实验", recent_limit=6) == []


def test_operator_conversation_store_persists_and_activates_general_goal(tmp_path):
    store = OperatorConversationStore(tmp_path)
    first = store.append("operator", "I want to discuss a durable private channel.")
    proposal = store.create_proposal(
        {
            "kind": "GENERAL",
            "description": "Create private owner channel",
            "reason": "Goals require discussion.",
        },
        source_message_id=first["message_id"],
    )
    runtime = FakeRuntime()
    activated = store.activate(proposal["proposal_id"], runtime)

    assert activated["status"] == "ACTIVATED"
    assert activated["goal_id"] == "goal:owner-chat:1"
    assert runtime.persisted == 1
    assert OperatorConversationStore(tmp_path).history()[-1]["role"] == "system"
    assert OperatorConversationStore(tmp_path).proposals()[0]["status"] == "ACTIVATED"


def test_live_service_restart_restores_owner_goal_wake_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    application = FakeApplication()
    store = OperatorConversationStore(root)
    message = store.append("operator", "Keep pursuing this owner-authorized task.")
    proposal = store.create_proposal(
        {
            "kind": "GENERAL",
            "description": "Keep pursuing this owner-authorized task",
            "reason": "restart continuity",
        },
        source_message_id=message["message_id"],
    )
    activated = store.activate(proposal["proposal_id"], application.domain_runtime)
    wakeful = FakeWakeful()

    LiveAgentService(
        state_root=root,
        application=application,
        backend=FakeBackend(),
        wakeful=wakeful,
    )._app()

    assert wakeful.calls
    _, seconds, relevant_refs = wakeful.calls[-1]
    assert seconds == 5
    assert activated["goal_id"] in relevant_refs
    assert activated["proposal_id"] in relevant_refs


def test_operator_conversation_store_activates_measurable_external_mission(tmp_path):
    store = OperatorConversationStore(tmp_path)
    proposal = store.create_proposal({
        "kind": "EXTERNAL_MISSION",
        "description": "Grow X followers from 2000 to 5000 within one month",
        "reason": "Owner established a measurable outcome after discussion.",
        "metric": "x_followers",
        "baseline": 2000,
        "target": 5000,
        "deadline": "2026-10-19T10:30:00+08:00",
        "direction": "AT_LEAST",
        "constraints": ["respect X policy"],
    })
    runtime = FakeRuntime()
    activated = store.activate(proposal["proposal_id"], runtime)

    assert activated["status"] == "ACTIVATED"
    assert activated["mission_id"] == "mission:owner-chat:1"
    assert activated["goal_id"] == "goal:owner-chat:mission:1"
    assert runtime.external_missions[0].metric == "x_followers"
    assert runtime.external_missions[0].baseline == 2000.0
    assert runtime.external_missions[0].target == 5000.0
    assert str(runtime.external_missions[0].direction.value) == "AT_LEAST"
    assert runtime.persisted == 1


def test_semantic_reconciliation_supersedes_degraded_goal_and_inherits_owner_activation(tmp_path):
    store = OperatorConversationStore(tmp_path)
    directive = "宣传 LivingRuntime Remote，并把它作为当前 X 粉丝增长目标的一次真实增长实验来执行。"
    first = store.append("operator", directive)
    degraded = store.create_proposal(
        {
            "kind": "GENERAL",
            "description": directive,
            "reason": "Owner instruction was durably captured while the cognition provider was unavailable; interpretation is deferred.",
            "semantic_status": "PENDING_RECONCILIATION",
        },
        source_message_id=first["message_id"],
    )
    runtime = FakeRuntime()
    degraded = store.activate(degraded["proposal_id"], runtime)
    old_goal = runtime.active_goals[-1]

    second = store.append("operator", directive)
    canonical = store.create_proposal(
        {
            "kind": "EXTERNAL_MISSION",
            "description": "宣传 LivingRuntime Remote 以提升 X 平台粉丝数",
            "metric": "x.followers",
            "baseline": 1836,
            "target": 5000,
            "deadline": "2026-10-19T02:32:02+00:00",
            "direction": "AT_LEAST",
        },
        source_message_id=second["message_id"],
    )
    reconciled = store.reconcile_pending_semantic_upgrades(runtime)
    assert len(reconciled) == 1
    canonical = reconciled[0]

    assert canonical["status"] == "ACTIVATED"
    assert canonical["kind"] == "EXTERNAL_MISSION"
    assert canonical["inherited_activation_from"] == degraded["proposal_id"]
    assert canonical["supersedes_proposal_id"] == degraded["proposal_id"]
    assert canonical["superseded_goal_cancelled"] is True
    assert old_goal.status == "CANCELLED"
    assert store.get_proposal(degraded["proposal_id"])["status"] == "SUPERSEDED"
    assert len(runtime.external_missions) == 1


def test_external_mission_activation_binds_existing_compatible_mission(tmp_path):
    store = OperatorConversationStore(tmp_path)
    runtime = FakeRuntime()
    existing = SimpleNamespace(
        mission_id="mission:existing-x-growth",
        goal_id="goal:existing-x-growth",
        description="Grow X followers",
        metric="x.followers",
        baseline=1836.0,
        target=5000.0,
        deadline=None,
        direction="AT_LEAST",
        status="ACTIVE",
    )
    runtime.external_missions.append(existing)
    proposal = store.create_proposal({
        "kind": "EXTERNAL_MISSION",
        "description": "宣传 LivingRuntime Remote 作为 X 增长实验",
        "metric": "X 平台粉丝数",
        "baseline": 1836,
        "target": 5000,
        "deadline": "2026-10-19",
        "direction": "AT_LEAST",
    })

    activated = store.activate(proposal["proposal_id"], runtime)

    assert activated["mission_id"] == existing.mission_id
    assert activated["goal_id"] == existing.goal_id
    assert activated["mission_binding"] == "EXISTING_COMPATIBLE_MISSION"
    assert len(runtime.external_missions) == 1


def test_private_operator_chat_is_authenticated_persistent_and_goal_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    backend = FakeBackend()
    application = FakeApplication()
    wakeful = FakeWakeful()
    service = LiveAgentService(
        state_root=root,
        application=application,
        backend=backend,
        wakeful=wakeful,
    )

    with TestClient(service._app()) as client:
        assert client.get("/operator/chat/").status_code == 401
        assert client.get("/operator/api/chat").status_code == 401
        _login(client, root)
        page = client.get("/operator/chat/")
        assert page.status_code == 200
        assert "和 Ferro 私下交流" in page.text
        assert "Goal Drafts" in page.text
        assert "长期记忆" in page.text
        assert "本轮行为输入证据" in page.text
        assert "/operator/api/chat/stream" in page.text

        forbidden = client.post("/operator/api/chat", json={"message": "hello"})
        assert forbidden.status_code == 403

        sent = client.post(
            "/operator/api/chat",
            headers={"X-Operator-Request": "1"},
            json={"message": "I need a private channel so we can discuss future goals."},
        )
        assert sent.status_code == 200
        body = sent.json()
        assert body["proposal"]["status"] == "DRAFT"
        assert body["memory_runtime"]["episode_id"]
        trace = body["behavioral_inputs"]
        assert trace["schema_version"] == "ferro-owner-behavioral-inputs.v1"
        assert trace["context_memory_count"] == len(trace["context_memory_ids"])
        assert trace["meaning"].startswith("runtime inputs supplied")
        assert body["message"]["metadata"]["behavioral_inputs"] == trace
        episode = CanonicalMemoryStore(root).get_memory(body["memory_runtime"]["episode_id"])
        assert episode is not None and episode["memory_type"] == "EPISODIC"
        assert episode["access_scope"] == "PRIVATE_OWNER"
        assert [row["role"] for row in body["history"][-2:]] == ["operator", "ferro"]
        assert "Private owner context" in backend.provider.calls[0][0]
        assert "Relevant durable memory" in backend.provider.calls[0][0]
        assert '"neural_life"' in backend.provider.calls[0][0]
        assert '"lived_learning"' in backend.provider.calls[0][0]
        assert '"open_development_acceptance"' in backend.provider.calls[0][0]
        assert '"persona"' in backend.provider.calls[0][0]
        assert "生存、繁殖与进化" in backend.provider.calls[0][0]
        owner_system = backend.provider.calls[0][1]["system"]
        assert "rational Ego layer" in owner_system
        assert "behavioral inputs rather than decorative telemetry" in owner_system
        assert "production-demonstrated behavior" in owner_system
        assert "lived continuity" in owner_system
        assert "canned identity sentence" in owner_system
        assert "STRICT JSON" in owner_system
        assert backend.provider.calls[0][1]["_minimum_quality"] == 0.75
        assert backend.provider.calls[0][1]["_attempt_timeout_seconds"] == 20
        assert backend.provider.calls[0][1]["_reserve_timeout_seconds"] == 35
        proposal_id = body["proposal"]["proposal_id"]

        streamed = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "请用中文继续，并保留我们的上下文。"},
        )
        assert streamed.status_code == 200
        events = [json.loads(line) for line in streamed.text.splitlines() if line.strip()]
        assert events[0]["type"] == "ack"
        assert any(row["type"] == "status" and row.get("stage") == "memory" for row in events)
        assert any(row["type"] == "status" and row.get("stage") == "reply" for row in events)
        assert backend.provider.stream_calls
        assert backend.provider.stream_calls[0][1]["_minimum_quality"] == 0.75
        assert backend.provider.stream_calls[0][1]["_attempt_timeout_seconds"] == 20
        assert backend.provider.stream_calls[0][1]["_reserve_timeout_seconds"] == 35
        visible_reply = "".join(
            str(row.get("text") or "") for row in events if row["type"] == "delta"
        )
        assert visible_reply == "这个目标已经足够具体，我先把它整理成目标草案，正式激活前不会执行。"
        assert events[-1]["type"] == "done"
        assert events[-1]["transport"] == "native"
        streamed_ferro = next(
            row for row in reversed(events[-1]["history"])
            if row["role"] == "ferro"
        )
        streamed_trace = streamed_ferro["metadata"]["behavioral_inputs"]
        assert streamed_trace["schema_version"] == "ferro-owner-behavioral-inputs.v1"
        assert "development_acceptance" in streamed_trace
        assert "personality_traits" in streamed_trace
        assert '"context_memory_count"' in backend.provider.stream_calls[0][0]

        activated = client.post(
            "/operator/api/chat/goals/" + proposal_id.replace(":", "%3A") + "/activate",
            headers={"X-Operator-Request": "1"},
            json={},
        )
        assert activated.status_code == 200
        assert activated.json()["activated"]["status"] == "ACTIVATED"
        assert application.domain_runtime.persisted == 1
        assert wakeful.calls and wakeful.calls[0][1] == 5

        state = client.get("/operator/api/chat").json()
        assert state["proposals"][0]["status"] == "ACTIVATED"
        assert state["history"][-1]["role"] == "system"
        assert any("生存、繁殖与进化" in row["text"] for row in state["memories"])

    # The channel is durable state, not an in-memory browser transcript.
    reloaded = OperatorConversationStore(root)
    assert any(row["role"] == "operator" for row in reloaded.history())
    assert reloaded.proposals()[0]["status"] == "ACTIVATED"
    assert not (root / "content" / "persona" / "interactions.jsonl").exists()


class RepairingProvider:
    def __init__(self) -> None:
        self.stream_calls = []
        self.structured_calls = []

    async def complete(self, prompt, **kwargs):
        return "analysis first, invalid envelope"

    async def stream(self, prompt, **kwargs):
        self.stream_calls.append((prompt, kwargs))
        yield 'We need to reason first. Example: {"reply":"must not leak"}'

    async def structured(self, prompt, schema, **kwargs):
        self.structured_calls.append((prompt, schema, kwargs))
        return {
            "reply": "结构已修复，并形成目标草案。",
            "goal_candidate": {
                "kind": "GENERAL",
                "description": "Repair owner chat structured output",
                "reason": "The owner requested a bounded draft.",
                "constraints": [],
            },
            "memory_candidates": [],
        }


def test_private_stream_repairs_invalid_envelope_without_leaking_reasoning(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    backend = SimpleNamespace(provider=RepairingProvider())
    service = LiveAgentService(
        state_root=root,
        application=FakeApplication(),
        backend=backend,
        wakeful=FakeWakeful(),
    )

    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "请形成目标草案，但不要激活。"},
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    visible = "".join(
        str(row.get("text") or "")
        for row in events
        if row.get("type") in {"delta", "replace"}
    )
    assert "We need to reason" not in visible
    assert "must not leak" not in visible
    assert "结构已修复" in visible
    assert any(row.get("stage") == "repair" for row in events if row.get("type") == "status")
    assert events[-1]["transport"] == "native-repaired"
    assert events[-1]["proposal"]["status"] == "DRAFT"
    assert backend.provider.structured_calls


class LanguageRepairProvider(RepairingProvider):
    async def stream(self, prompt, **kwargs):
        self.stream_calls.append((prompt, kwargs))
        yield json.dumps({
            "reply": "Here is the draft.",
            "goal_candidate": None,
            "memory_candidates": [],
        }, ensure_ascii=False)

    async def structured(self, prompt, schema, **kwargs):
        self.structured_calls.append((prompt, schema, kwargs))
        return {
            "reply": "这是修复后的中文回复。",
            "goal_candidate": None,
            "memory_candidates": [],
        }


def test_private_stream_repairs_language_mismatch_for_chinese_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    backend = SimpleNamespace(provider=LanguageRepairProvider())
    service = LiveAgentService(
        state_root=root,
        application=FakeApplication(),
        backend=backend,
        wakeful=FakeWakeful(),
    )

    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "请继续用中文回答。"},
        )

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    final = events[-1]
    assert final["transport"] == "native-repaired"
    assert final["message"]["text"] == "这是修复后的中文回复。"
    assert backend.provider.structured_calls


class XMissionProvider:
    async def stream(self, prompt, **kwargs):
        yield json.dumps({
            "reply": "已形成 X 增长目标草案。",
            "goal_candidate": {
                "kind": "EXTERNAL_MISSION",
                "description": "一个月内将 X 粉丝从 2000 提升到 5000",
                "reason": "owner 提出了可衡量增长目标",
                "metric": "followers",
                "baseline": 2000,
                "target": 5000,
                "deadline": "2026-10-19T10:00:00+00:00",
                "direction": "AT_LEAST",
                "constraints": ["遵守 X 平台政策"],
            },
            "memory_candidates": [],
        }, ensure_ascii=False)


def test_x_follower_goal_metric_is_canonicalized(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    service = LiveAgentService(
        state_root=root,
        application=FakeApplication(),
        backend=SimpleNamespace(provider=XMissionProvider()),
        wakeful=FakeWakeful(),
    )
    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "请形成 X 粉丝增长目标草案，不要激活。"},
        )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1]["proposal"]["metric"] == "x.followers"


class OwnerToolRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.persisted = 0

    def capability_manifest(self):
        return (
            {
                "capability_id": "web.search",
                "description": "Search the public web.",
                "input_schema": {"type": "object"},
                "side_effect_level": "READ_ONLY",
                "required_permissions": [],
            },
            {
                "capability_id": "x.post",
                "description": "Publish to X.",
                "input_schema": {"type": "object"},
                "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                "required_permissions": ["social.publish"],
            },
        )

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        return SimpleNamespace(
            receipt_id="capability-receipt:owner-tool:1",
            payload={"ok": True, "results": [{"title": "Ferro result"}]},
        )

    def persist(self):
        self.persisted += 1


class OwnerToolProvider:
    def __init__(self, capability_id="web.search") -> None:
        self.capability_id = capability_id
        self.stream_calls = []
        self.complete_calls = []

    def first(self):
        payload = {"query": "Ferro"} if self.capability_id == "web.search" else {"text": "hello"}
        return json.dumps({
            "reply": "我先读取当前只读证据。",
            "tool_requests": [{
                "capability_id": self.capability_id,
                "payload": payload,
                "reason": "需要当前证据",
            }],
            "goal_candidate": None,
            "memory_candidates": [],
        }, ensure_ascii=False)

    def final(self):
        if self.capability_id == "web.search":
            reply = "我已经通过 Runtime 的只读 web.search 核对了当前证据。"
        else:
            reply = "这个 owner 私聊不会执行 x.post，因为它具有外部副作用。"
        return json.dumps({
            "reply": reply,
            "tool_requests": [],
            "goal_candidate": None,
            "memory_candidates": [],
        }, ensure_ascii=False)

    async def stream(self, prompt, **kwargs):
        self.stream_calls.append((prompt, kwargs))
        raw = self.final() if "Owner Tool Results round" in prompt else self.first()
        for offset in range(0, len(raw), 9):
            yield raw[offset:offset + 9]

    async def complete(self, prompt, **kwargs):
        self.complete_calls.append((prompt, kwargs))
        return self.final()


def test_private_stream_executes_readonly_runtime_tool_and_returns_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    application = FakeApplication()
    application.runtime = OwnerToolRuntime()
    provider = OwnerToolProvider()
    service = LiveAgentService(
        state_root=root,
        application=application,
        backend=SimpleNamespace(provider=provider),
        wakeful=FakeWakeful(),
    )

    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "请查一下当前证据。"},
        )

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(row.get("stage") == "tool" for row in events if row.get("type") == "status")
    assert events[-1]["transport"] == "native-tools-stream"
    assert events[-1]["message"]["text"].startswith("我已经通过 Runtime")
    assert events[-1]["tool_receipts"] == [{
        "capability_id": "web.search",
        "status": "INVOKED",
        "receipt_id": "capability-receipt:owner-tool:1",
        "ok": True,
    }]
    assert application.runtime.calls == [("web.search", {"query": "Ferro", "limit": 8})]
    assert "Owner Tool Results round 1" in provider.stream_calls[1][0]
    replaces = [row["text"] for row in events if row.get("type") == "replace"]
    assert len(replaces) >= 2
    assert len(replaces[-1]) >= len(replaces[0])
    assert provider.complete_calls == []

    with TestClient(service._app()) as client:
        _login(client, root)
        recall = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "刚才成功的只读能力和 receipt_id 是什么？"},
        )

    assert recall.status_code == 200
    recall_prompt = provider.stream_calls[2][0]
    assert '"tool_receipts"' in recall_prompt
    assert '"capability_id": "web.search"' in recall_prompt
    assert '"status": "INVOKED"' in recall_prompt
    assert '"receipt_id": "capability-receipt:owner-tool:1"' in recall_prompt
    assert '"ok": true' in recall_prompt


def test_private_stream_refuses_side_effect_tool_request(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    application = FakeApplication()
    application.runtime = OwnerToolRuntime()
    provider = OwnerToolProvider(capability_id="x.post")
    service = LiveAgentService(
        state_root=root,
        application=application,
        backend=SimpleNamespace(provider=provider),
        wakeful=FakeWakeful(),
    )

    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat/stream",
            headers={"X-Operator-Request": "1"},
            json={"message": "发一条 X 帖子。"},
        )

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1]["transport"] == "native-tools-stream"
    assert "不会执行 x.post" in events[-1]["message"]["text"]
    assert application.runtime.calls == []
    assert events[-1]["tool_receipts"][0]["status"] == "FORBIDDEN_SIDE_EFFECT"


class PrimaryStructuredProvider:
    def __init__(self) -> None:
        self.structured_calls = []
        self.complete_calls = []

    async def structured(self, prompt, schema, **kwargs):
        self.structured_calls.append((prompt, schema, kwargs))
        return {
            "reply": "我已经收到这个任务，并会把真实执行断点保留下来。",
            "tool_requests": [],
            "goal_candidate": None,
            "memory_candidates": [],
        }

    async def complete(self, prompt, **kwargs):
        self.complete_calls.append((prompt, kwargs))
        raise AssertionError("structured-capable owner provider should not use free-form complete")


def test_owner_chat_prefers_structured_primary_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    provider = PrimaryStructuredProvider()
    service = LiveAgentService(
        state_root=root,
        application=FakeApplication(),
        backend=SimpleNamespace(provider=provider),
        wakeful=FakeWakeful(),
    )
    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat",
            headers={"X-Operator-Request": "1"},
            json={"message": "把宣传 LivingRuntime Remote 作为一个真实任务接收。"},
        )

    assert response.status_code == 200
    assert provider.complete_calls == []
    assert len(provider.structured_calls) == 1
    kwargs = provider.structured_calls[0][2]
    assert kwargs["_routing_task_class"] == "owner_dialogue"
    assert kwargs["_attempt_timeout_seconds"] == 20
    assert response.json()["reply"].startswith("我已经收到")


class UnavailableOwnerProvider:
    async def structured(self, prompt, schema, **kwargs):
        raise RuntimeError("all eligible LLM resources failed without exposing provider responses")

    async def complete(self, prompt, **kwargs):
        raise RuntimeError("all eligible LLM resources failed without exposing provider responses")


def test_owner_task_intake_survives_llm_pool_outage(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    application = FakeApplication()
    wakeful = FakeWakeful()
    service = LiveAgentService(
        state_root=root,
        application=application,
        backend=SimpleNamespace(provider=UnavailableOwnerProvider()),
        wakeful=wakeful,
    )
    directive = (
        "宣传 LivingRuntime Remote，并把它作为当前 X 粉丝增长目标的一次真实增长实验来执行；"
        "如果执行链阻塞，保留断点证据。"
    )
    with TestClient(service._app()) as client:
        _login(client, root)
        intake = client.post(
            "/operator/api/chat",
            headers={"X-Operator-Request": "1"},
            json={"message": directive},
        )
        assert intake.status_code == 200
        assert intake.json()["status"] == "COGNITION_DEFERRED"
        assert intake.json()["proposal"] is None

        draft = client.post(
            "/operator/api/chat/goal-draft",
            headers={"X-Operator-Request": "1"},
            json={},
        )
        assert draft.status_code == 200
        body = draft.json()
        assert body["status"] == "GOAL_DRAFT_COGNITION_DEFERRED"
        assert body["proposal"]["status"] == "DRAFT"
        assert body["proposal"]["kind"] == "GENERAL"
        assert body["proposal"]["description"] == directive
        proposal_id = body["proposal"]["proposal_id"]

        activated = client.post(
            "/operator/api/chat/goals/" + proposal_id.replace(":", "%3A") + "/activate",
            headers={"X-Operator-Request": "1"},
            json={},
        )
        assert activated.status_code == 200
        assert activated.json()["activated"]["status"] == "ACTIVATED"

    assert application.domain_runtime.active_goals[-1].description == directive
    assert application.domain_runtime.persisted == 1
    assert wakeful.calls and wakeful.calls[-1][1] == 5


class TransientContextFailureProvider:
    async def structured(self, prompt, schema, **kwargs):
        raise FileNotFoundError("optional-state-rotated")

    async def complete(self, prompt, **kwargs):
        raise FileNotFoundError("optional-state-rotated")


def test_owner_task_intake_survives_transient_context_io_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_OPERATOR_CONSOLE_ENABLED", "true")
    root = tmp_path / "state"
    service = LiveAgentService(
        state_root=root,
        application=FakeApplication(),
        backend=SimpleNamespace(provider=TransientContextFailureProvider()),
        wakeful=FakeWakeful(),
    )
    with TestClient(service._app()) as client:
        _login(client, root)
        response = client.post(
            "/operator/api/chat",
            headers={"X-Operator-Request": "1"},
            json={"message": "宣传 LivingRuntime Remote，并保留真实断点证据。"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COGNITION_DEFERRED"
    assert body["proposal"] is None
    assert body["history"][-1]["role"] == "system"
