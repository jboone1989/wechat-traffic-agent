"""Isolated fault episode, health, and model presentation contracts."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.interoception_health_assessment import enrich_l1_health
from wechat_traffic_agent.interoception_episode_ledger import InteroceptionEpisodeLedger
from wechat_traffic_agent.interoception_state_projection import compose_interoception_state
from wechat_traffic_agent.interoception_diagnosis_receipt import diagnostic_failure_receipt
from wechat_traffic_agent.interoception_task_classifier import classify_task_result
from wechat_traffic_agent.cognition_environment_reader import CognitionEnvironmentReader
from wechat_traffic_agent.cognition_selection_receipts import selection_receipts
from wechat_traffic_agent.cognition_snapshot_presentation import build_cognition_snapshot
from wechat_traffic_agent.cognition_selected_projection import SelectedCognitionSections

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_l1_health_may_escalate_without_overwriting_deterministic_evidence():
    deterministic = {
        "task": "CONTENT_COGNITION", "abnormal": False,
        "primary_symptom": "healthy", "base_severity": 0,
        "subsystem": "cognition", "symptoms": [], "status_evidence": [],
    }
    health = SimpleNamespace(assess_health=lambda **kwargs: {
        "health_state": "STUCK", "subsystem": "cognition_provider",
    })
    classified, reading = enrich_l1_health(
        "CONTENT_COGNITION", deterministic, (), l1=health,
    )
    assert classified["abnormal"] is True
    assert classified["primary_symptom"] == "l1_stuck"
    assert classified["base_severity"] == 0.58
    assert reading["health_state"] == "STUCK"
    assert deterministic["symptoms"] == []


def test_deterministic_classifier_records_degradation_without_episode_io():
    classified = classify_task_result(
        "CONTENT_COGNITION",
        {"status": "COGNITION_ONLY", "provider": {"status": "FALLBACK_USED"},
         "prediction_error": 0.4},
    )
    kinds = {row["kind"] for row in classified["symptoms"]}
    assert {"goal_stall", "fallback_dependency", "prediction_error"} <= kinds
    assert classified["abnormal"] is True
    assert classified["prediction_error"] == 0.4


def test_episode_ledger_records_recurrence_then_three_consecutive_recoveries():
    saved = {}
    def opened():
        return [(key, dict(value)) for key, value in saved.items()
                if value.get("status") in {"OPEN", "RECOVERING"}]
    ledger = InteroceptionEpisodeLedger(
        open_episodes=opened,
        episode_path=lambda task, subsystem, symptom: (
            "episode-1", Path("/episodes/episode-1.json")
        ),
        read_json=lambda key: dict(saved[key]) if key in saved else None,
        write_json=lambda key, payload: saved.__setitem__(key, dict(payload)),
        episode_schema="test-episode.v1", recovery_streak=3,
    )
    abnormal = {"abnormal": True, "symptoms": [{"kind": "fallback_dependency"}],
                "primary_status": "FALLBACK"}
    healthy = {"abnormal": False, "symptoms": [], "primary_status": "OK"}
    for occurrence in range(2):
        episode_id, episode, should = ledger.update(
            task="CONTENT_COGNITION", now=NOW, subsystem="cognition",
            primary_symptom="fallback_dependency", severity=0.5,
            classification=abnormal, l1_health={"health_state": "UNKNOWN"},
            observation_id=f"obs-{occurrence}", cycle=f"cycle-{occurrence}",
        )
        assert episode_id == "episode-1"
        assert should is (occurrence == 0)
        assert episode["occurrences"] == occurrence + 1
    for index in range(3):
        ledger.update(
            task="CONTENT_COGNITION", now=NOW, subsystem="cognition",
            primary_symptom="healthy", severity=0,
            classification=healthy, l1_health={},
            observation_id=f"healthy-{index}", cycle=f"recovery-{index}",
        )
    recovered = saved[Path("/episodes/episode-1.json")]
    assert recovered["status"] == "RECOVERED_VERIFIED"
    assert recovered["healthy_streak"] == 3
    assert len(recovered["recovery_evidence_refs"]) == 6


def test_state_projection_is_pure_and_never_hides_open_fault():
    state = {"recent_observations": [{"observation_id": "prior"}]}
    episodes = [{"subsystem": "memory", "episode_id": "e1",
                 "primary_symptom": "retry_dependency",
                 "current_severity": 0.8, "occurrences": 4}]
    observation = {"observation_id": "obs-2", "abnormal": True}
    result = compose_interoception_state(
        previous_state=state, observation=observation, active=episodes,
        distress=0.2, l1_health={"health_state": "STUCK"}, now=NOW,
        bounded=lambda value, default=0: float(value if value is not None else default),
        schema="test-interoception.v1", recent_limit=48,
    )
    assert result["status"] == "ILL"
    assert result["distress"] >= 0.8
    assert result["active_episode_ids"] == ["e1"]
    assert [r["observation_id"] for r in result["recent_observations"]] == ["prior", "obs-2"]
    assert state == {"recent_observations": [{"observation_id": "prior"}]}


def test_diagnosis_request_is_a_receipt_not_an_implicit_action():
    classified = {"symptoms": [{"kind": "hard_error"}],
                  "status_evidence": [{"path": "result", "status": "ERROR"}]}
    active, request = diagnostic_failure_receipt(
        task="CONTENT_COGNITION", classification=classified,
        episode_payload={"diagnosis_requested_at": NOW.isoformat()},
        episode_id="episode-1", observation_id="obs-1",
        primary_symptom="hard_error", subsystem="cognition",
        severity=0.86, should_diagnose=False,
    )
    assert active is True and request["diagnostic_recurrence"] is True
    assert request["interoceptive_episode_id"] == "episode-1"
    assert request["status_evidence"][0]["status"] == "ERROR"
    inactive, absent = diagnostic_failure_receipt(
        task="CONTENT_COGNITION", classification=classified,
        episode_payload=None, episode_id=None, observation_id="obs-2",
        primary_symptom="healthy", subsystem="cognition",
        severity=0, should_diagnose=False,
    )
    assert inactive is False and absent is None


def test_environment_capabilities_are_read_without_memory_index_access():
    runtime = SimpleNamespace(available_capabilities=lambda: ["SEARCH", "WRITE"])
    domain = SimpleNamespace(
        capability_inventory=SimpleNamespace(usable_ids=lambda: ["SEARCH", "READ"]),
        agent_manifest=SimpleNamespace(capabilities=["PUBLISH"]),
    )
    wake = {
        "self_model": {"capability_beliefs": {
            "SEARCH": {"attempts": 5, "confidence": 0.8},
        }},
        "neural_social": {"relationship_count": 1,
                          "recent_relationships": [{"actor": "friend"}]},
    }
    reader = CognitionEnvironmentReader(
        build_behavior=lambda value: {"drives": {"evolution": 0.6}},
        build_phenotype=lambda value: {"traits": {"persistence": 0.8}},
        compact_behavior=lambda value: value,
        compact_phenotype=lambda value: value,
    )
    output = reader.collect(
        application=SimpleNamespace(runtime=runtime),
        domain_runtime=domain, wake=wake,
    )
    assert output.capabilities == ["PUBLISH", "READ", "SEARCH", "WRITE"]
    assert output.capability_beliefs[0]["attempts"] == 5
    assert output.relationships == [{"actor": "friend"}]


def test_selection_receipt_excludes_host_search_questions_from_model_context():
    context = {
        "schema_version": "decision-context.v1",
        "selected_facts": [{"context_id": "m1", "source": "memory",
                            "kind": "observed", "payload": "real evidence"}],
        "information_needs": [{"need_id": "need-1",
                               "question": "private host planning instructions"}],
        "candidate_count": 2, "selected_count": 1,
    }
    preview, summary = selection_receipts(context)
    assert preview["fact_index"] == [{"context_id": "m1", "source": "memory",
                                      "kind": "observed"}]
    assert "information_needs" not in preview
    assert summary["information_need_ids"] == ["need-1"]
    assert "private host planning instructions" not in str((preview, summary))


def test_model_snapshot_drops_neural_control_under_owner_authority():
    views = SelectedCognitionSections(
        current_intent=[], missions={"active": []}, mind={},
        experience=[], memory=[], resources={}, capabilities=[],
        capability_beliefs=[], relationships=[], commitments=[], operational={},
        neural_life={"intention": {"name": "EXPLORE"}},
    )
    environment = SimpleNamespace(
        application_capabilities=[], domain_capabilities=[], manifest_capabilities=[],
        social_projection={"relationship_count": 0}, phenotype={},
        behavior={},
    )
    metadata = {
        "decision_frame": {"decision_type": "OWNER"},
        "context_receipt": {"receipt_id": "receipt-1"},
        "open_loops": [], "epistemic_contract": {},
    }
    snapshot = build_cognition_snapshot(
        now=NOW, application=SimpleNamespace(subject=None),
        app_runtime=SimpleNamespace(cycle=5), projection={"mind_tick": 3},
        wake={"neural_control_signals": [{"task": "AUTONOMOUS"}],
              "neural_feedback": [{"event": "private"}]},
        environment=environment, views=views, metadata=metadata,
        llm_decision_context={"role": "SEED_WORKSPACE_NOT_REASONING_BOUNDARY"},
        selection_summary={
            "retrieval_policy": "decision_conditioned_context_retrieval_v1",
            "selected_sources": [], "always_on_sources": [],
        },
        interoception={"status": "HEALTHY"}, owner_mode=True,
    )
    assert snapshot["neural_state"]["control_signals"] == []
    assert snapshot["neural_state"]["feedback"] == []
    assert snapshot["continuity"]["mind_tick"] == 3


def test_new_fault_and_context_modules_have_explicit_small_boundaries():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for filename in (
        "interoception_health_assessment.py", "interoception_episode_ledger.py",
        "interoception_state_projection.py", "interoception_diagnosis_receipt.py",
        "cognition_environment_reader.py", "cognition_selection_receipts.py",
        "cognition_snapshot_presentation.py",
        "interoception_task_classifier.py",
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in {"live_agent", "wakeful_runtime", "content_goal_loop"}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    filename, node.name, node.end_lineno - node.lineno + 1,
                )
    for filename, name, limit in (
        ("interoceptive_fault.py", "observe", 100),
        ("cognition_context.py", "build_ferro_cognition_context", 220),
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        fn = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
        assert fn.end_lineno - fn.lineno + 1 <= limit
