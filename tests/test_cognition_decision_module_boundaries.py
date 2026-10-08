"""Independent decision retrieval, in-memory selection and cognition fault boundaries."""
from datetime import datetime, timezone
from types import SimpleNamespace
import ast
from pathlib import Path

from wechat_traffic_agent.decision_candidate_retrieval import (
    CandidateRetrievalPorts, DecisionCandidateRetriever,
)
from wechat_traffic_agent.decision_fact_selection import (
    DecisionFactSelector, SelectionRules,
)
from wechat_traffic_agent.cognition_contract_observer import CognitionContractObserver


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_candidate_retrieval_is_injected_and_retains_router_failure_receipts():
    class Router:
        def __init__(self, root):
            assert root == "/memory"
        def recall(self, question, *, limit, now):
            if question == "blocked":
                raise ConnectionError("backend unavailable")
            return [{"id": "memory-1", "payload": {"note": "confirmed"}}]
    ports = CandidateRetrievalPorts(
        router_factory=Router,
        canonicalize=lambda memory: {
            "context_id": memory["id"], "source": "memory", "payload": memory["payload"],
        },
        timestamp=lambda row: None,
        declares_supersession=lambda row: False,
        dependency_tokens=lambda row: set(),
    )
    batch = DecisionCandidateRetriever(ports).collect(
        local_candidates=[{"context_id": "local", "source": "goal"}],
        needs=[SimpleNamespace(need_id="ok", question="goal"),
               SimpleNamespace(need_id="failed", question="blocked")],
        state_root="/memory", now=NOW,
        owner_goal_ids=set(), owner_mission_ids=set(),
    )
    assert sorted(batch.by_id) == ["local", "memory-1"]
    assert batch.recall_count == 1
    assert batch.recall_ranks[("ok", "memory-1")] == 1
    assert batch.memory_errors == ["failed:ConnectionError"]


def test_supersession_requires_specific_shared_dependency_not_only_owner_goal():
    older = {"context_id": "old", "current": False, "observed_at": 1,
             "tokens": {"goal-1", "source-A"}}
    newer = {"context_id": "new", "current": False, "observed_at": 2,
             "supersedes": True, "tokens": {"goal-1", "source-A"}}
    unrelated = {"context_id": "unrelated", "current": False, "observed_at": 1,
                 "tokens": {"goal-1", "source-B"}}
    ports = CandidateRetrievalPorts(
        router_factory=lambda root: None,
        canonicalize=lambda row: row,
        timestamp=lambda row: row.get("observed_at"),
        declares_supersession=lambda row: bool(row.get("supersedes")),
        dependency_tokens=lambda row: set(row.get("tokens") or []),
    )
    batch = DecisionCandidateRetriever(ports).collect(
        local_candidates=[older, newer, unrelated], needs=[],
        state_root=None, now=NOW, owner_goal_ids={"goal-1"},
        owner_mission_ids=set(),
    )
    assert batch.by_id["old"]["_temporal_shadowed"] is True
    assert "_temporal_shadowed" not in batch.by_id["unrelated"]
    assert "_temporal_shadowed" not in older  # source rows aren't modified


def test_selector_has_explicit_no_io_port_and_honors_budget():
    goal = {"context_id": "goal-1", "source": "goal", "current": True,
            "text": "goal", "payload": {"goal_id": "g-1"}}
    memory = {"context_id": "memory-1", "source": "memory", "current": False,
              "text": "A detailed old outcome", "payload": {"outcome": "observed"}}
    need = SimpleNamespace(
        need_id="objective", priority=1.0,
        as_dict=lambda: {"need_id": "objective"},
    )
    selector = DecisionFactSelector(
        candidates=[goal, memory], by_id={"goal-1": goal, "memory-1": memory},
        needs=[need],
        scored_by_need={"objective": [
            (2.0, "goal-1", ["source_matches_information_need"]),
            (1.3, "memory-1", ["source_matches_information_need"]),
        ]},
        policy={
            "coverage_floor_min": 0.78, "coverage_ratio": 0.5,
            "multi_need_bonus": 0.2, "size_tax": 0.1,
            "relevance_floor": 0.60,
        },
        current_time=NOW, owner_goal_ids={"g-1"}, owner_mission_ids=set(),
        budget_chars=1000, max_facts=3,
        rules=SelectionRules(
            text=lambda value: str(value),
            dependency_tokens=lambda row: set(),
            explicitly_stale=lambda row, **kw: False,
        ),
    )
    result = selector.select(
        state_root=None, memory_recall_count=0, memory_errors=[],
    )
    assert "goal-1" in result["selected_ids"]
    assert result["selected_count"] <= 2
    assert result["memory_router"]["enabled"] is False
    assert result["selected_chars_estimate"] <= 1000


def test_contract_observer_isolated_and_never_diagnoses_duplicate():
    events = []
    class Monitor:
        def __init__(self, root):
            events.append("monitor_setup")
        def observe_context(self, snapshot, *, now):
            assert snapshot["context_receipt"]["receipt_id"] == "r-1"
            assert snapshot["phenotype_prior"]["identity"] == "stable"
            return {"status": "VIOLATED", "violations": [{"kind": "gap",
                     "subsystem": "context", "expected": "facts", "observed": "none"}],
                    "duplicate": True, "observation_id": "obs-1"}
    class Fault:
        def __init__(self, root):
            events.append("unexpected_fault_setup")
    service = CognitionContractObserver(
        state_root="/memory", monitor_factory=Monitor, fault_factory=Fault,
    )
    snapshot = service.observe(
        {"context_receipt": {"receipt_id": "condensed"}},
        full_phenotype_prior={"identity": "stable"},
        full_behavioral_state={"mode": "awake"},
        context_receipt={"receipt_id": "r-1"}, now=NOW,
        domain_runtime=None, app_runtime=None,
    )
    assert events == ["monitor_setup"]
    assert snapshot["capability_contracts"]["status"] == "VIOLATED"
    assert "capability_contract_diagnosis" not in snapshot


def test_new_context_modules_are_bounded_and_do_not_import_runner():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for file in (
        "decision_candidate_retrieval.py", "decision_fact_selection.py",
        "cognition_contract_observer.py", "cognition_selected_projection.py",
    ):
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert str(node.module) not in {"content_goal_loop", "live_agent"}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (file, node.name)
    for file, name, cap in (
        ("decision_context.py", "build_decision_context", 100),
    ):
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        fn = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
        assert fn.end_lineno - fn.lineno + 1 <= cap


def test_selected_cognition_projection_is_pure_and_keeps_source_groups():
    from wechat_traffic_agent.cognition_selected_projection import (
        project_selected_cognition,
    )
    selected = {
        "goal": [{"goal_id": "g-1", "kind": "GOAL"}],
        "mission": [{"mission_id": "mission-1"}],
        "memory": [{"memory_id": "m-1", "summary": "observed outcome"}],
        "interest": [{"topic": "AI"}],
        "capability": ["SEARCH"],
        "internal": [{"affect": {"curiosity": 0.5}}],
        "resource": [{"available": True}],
        "commitment": [{"commitment_id": "c-1"}],
    }
    projection = project_selected_cognition(
        {"selected": selected}, {"mind_schema": "v1", "mind_tick": 7},
    )
    assert projection.current_intent[0]["goal_id"] == "g-1"
    assert projection.missions["active"][0]["mission_id"] == "mission-1"
    assert projection.memory[0]["memory_id"] == "m-1"
    assert projection.mind["interests"][0]["topic"] == "AI"
    assert projection.capabilities == ["SEARCH"]
    assert projection.neural_life["affect"]["curiosity"] == 0.5
    assert projection.commitments[0]["commitment_id"] == "c-1"
