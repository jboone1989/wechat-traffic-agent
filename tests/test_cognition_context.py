from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.cognition_context import build_ferro_cognition_context
from wechat_traffic_agent.memory_self import ProspectiveSelfMemoryService


class _AppRuntime:
    cycle = 2371

    def available_capabilities(self):
        return ("content.status", "web.search", "web.fetch", "social.publish")


class _Inventory:
    def usable_ids(self):
        return ["advisory_reasoning", "structured_reasoning"]


class _DomainRuntime:
    active_goals = [
        SimpleNamespace(
            goal_id="goal-owner",
            description="Ship the real owner experiment",
            status=SimpleNamespace(value="ACTIVE"),
            priority=0.9,
        )
    ]
    external_missions = []
    missions = None
    capability_inventory = _Inventory()
    agent_manifest = SimpleNamespace(capabilities=("topic_discovery", "content_research"))


class _MindSession:
    def snapshot(self):
        return {
            "autobiography": {
                "events": [
                    {
                        "event_id": "experience-1",
                        "tick": 419,
                        "summary": "The previous strategy failed and should not be repeated.",
                        "source": "content-process",
                        "domain": "content-process",
                        "outcome": 0.0,
                        "confidence": 1.0,
                        "evidence_refs": ["receipt-1"],
                    }
                ]
            }
        }


class _Mind:
    session = _MindSession()

    def public_projection(self):
        return {
            "mind_schema": "content-mind:v1",
            "mind_tick": 420,
            "interests": [{"name": "remote-agent-debugging", "salience": 0.9, "preference": 0.8}],
            "open_inquiries": [{"inquiry_id": "inq-1", "question": "What angle converts?", "origin": "MISSION", "status": "OPEN"}],
            "beliefs": [{"proposition": "generic announcements underperform", "stance": 0.8, "confidence": 0.75, "revision_count": 2}],
            "recent_belief_changes": [{"proposition": "generic announcements underperform", "before": 0.2, "after": 0.8, "evidence_refs": ["receipt-1"]}],
            "latest_plan": {
                "cycle": "v2-123",
                "goal": {"goal_id": "goal-owner", "origin": "USER"},
                "external_mission_id": "mission-x",
                "owner_goal_id": "goal-owner",
                "world_inquiry_topics": {"inq-1": "remote-agent-debugging"},
                "irrelevant_bulk": "must not be copied",
            },
            "resource_awareness": {
                "current": {"pressure": "WATCH"},
                "observation_count": 12,
                "decision_count": 3,
                "outcome_count": 2,
                "reflection_count": 2,
            },
            "strategy_learning": {"outcome_count": 2, "topics": ["remote-agent-debugging"]},
            "learning_boundary": "world feedback changes future choices",
        }


class _Wakeful:
    def public_snapshot(self):
        return {
            "state": "THINKING",
            "attention": {"mission": 0.9},
            "resource_pressure": "WATCH",
            "last_cognition_at": "2026-09-25T15:00:00+00:00",
            "last_action_at": "2026-09-25T14:59:00+00:00",
            "pending_plan_count": 2,
            "recent_stimulus_kinds": ["MISSION_OUTCOME"],
            "neural_life": {
                "affect": {"curiosity": 0.7, "frustration": 0.4},
                "body": {"disk_pressure": 0.5},
                "drives": {"evolution": 0.6},
                "intention": {"name": "EVOLVE", "strength": 0.8},
                "history": [{"must": "not leak bulk history"}],
            },
            "neural_control_signals": [{"kind": "attention", "intent": "mission"}],
            "neural_feedback": [{"outcome_id": "out-1", "pathway": ["prediction", "result"]}],
        }


def _application():
    return SimpleNamespace(
        runtime=_AppRuntime(),
        domain_runtime=_DomainRuntime(),
        subject=SimpleNamespace(subject_id="content-agent", tick=2372),
    )


def test_self_memory_uses_application_runtime_available_capabilities():
    packet = ProspectiveSelfMemoryService.snapshot_from_runtime(_application(), _Mind())
    assert "web.search" in packet.current_capabilities
    assert "web.fetch" in packet.current_capabilities
    assert "social.publish" in packet.current_capabilities


def test_cognition_snapshot_unifies_current_harness_state():
    context = build_ferro_cognition_context(
        application=_application(),
        mind=_Mind(),
        wakeful=_Wakeful(),
    )

    assert context["continuity"]["application_cycle"] == 2371
    assert context["continuity"]["subject_tick"] == 2372
    assert context["continuity"]["mind_tick"] == 420
    assert context["mind"]["mind_tick"] == 420
    assert context["recent_experience"][0]["event_id"] == "experience-1"
    assert "web.search" in context["capabilities"]["available"]
    assert "content_research" in context["capabilities"]["available"]
    assert context["neural_state"]["life"]["affect"]["curiosity"] == 0.7
    assert "history" not in context["neural_state"]["life"]
    assert "irrelevant_bulk" not in context["mind"]["latest_plan"]
