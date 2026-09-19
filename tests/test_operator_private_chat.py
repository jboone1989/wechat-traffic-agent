from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from wechat_traffic_agent.live_agent import LiveAgentService
from wechat_traffic_agent.operator_private_chat import OperatorConversationStore, parse_model_json


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

    async def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return json.dumps({
            "reply": "这个目标已经足够具体，我先把它整理成目标草案，正式激活前不会执行。",
            "goal_candidate": {
                "kind": "GENERAL",
                "description": "Build a durable private owner communication channel",
                "reason": "Owner and Ferro need to discuss goals before committing them.",
                "constraints": ["private owner context", "no hidden chain-of-thought"],
            },
        }, ensure_ascii=False)


class FakeBackend:
    def __init__(self) -> None:
        self.provider = StructuredProvider()


class FakeWakeful:
    def __init__(self) -> None:
        self.calls = []

    def public_snapshot(self):
        return {}

    def schedule_goal_followup(self, now, *, seconds=5):
        self.calls.append((now, seconds))


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
        assert "/operator/api/chat" in page.text

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
        assert [row["role"] for row in body["history"][-2:]] == ["operator", "ferro"]
        assert "Private owner context" in backend.provider.calls[0][0]
        assert "STRICT JSON" in backend.provider.calls[0][1]["system"]
        proposal_id = body["proposal"]["proposal_id"]

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

    # The channel is durable state, not an in-memory browser transcript.
    reloaded = OperatorConversationStore(root)
    assert any(row["role"] == "operator" for row in reloaded.history())
    assert reloaded.proposals()[0]["status"] == "ACTIVATED"
    assert not (root / "content" / "persona" / "interactions.jsonl").exists()
