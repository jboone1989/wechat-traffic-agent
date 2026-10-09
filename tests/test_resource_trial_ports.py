"""Feature-port contract tests for provider trial policy, measurement and feedback."""
import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.provider_trial_executor import ProviderTrialExecutor
from wechat_traffic_agent.resource_trial_policy import ResourceTrialPolicy, retry_plan
from wechat_traffic_agent.resource_outcome_recorder import ResourceOutcomeRecorder
from wechat_traffic_agent.resource_feedback_consumer import ResourceFeedbackConsumer


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def plan(**updates):
    fields = dict(
        plan_id="plan-a", lead_id="lead-a", provider_label="Alpha",
        credential_env="ALPHA_API_KEY", task_class="simple_public_text",
        status="READY_TO_TRIAL", max_requests=3, evidence_refs=(),
    )
    fields.update(updates)
    return SimpleNamespace(**fields)


def trial(**updates):
    data = dict(
        opportunity_id="lead-a", model_id="model-a", trial_id="trial-1",
        status="FAILED", error_code="HTTP_429", quality_score=None,
        completed_at=NOW.isoformat(), started_at=NOW.isoformat(),
        evidence_refs=(),
    )
    data.update(updates)
    return SimpleNamespace(**data)


def test_policy_prioritizes_untried_provider_and_excludes_incompatible_model():
    policy = ResourceTrialPolicy()
    previous = [trial(status="COMPLETED")]
    ready = policy.prioritized(
        [plan(), plan(plan_id="new", lead_id="lead-new")],
        previous, [], provider_limit=1, resolver_ref="resolver:v1",
    )
    assert [item.lead_id for item in ready] == ["lead-new"]
    incompatible = [trial(trial_id=f"bad-{i}", error_code="HTTP_404")
                    for i in range(3)]
    decision = policy.models(
        plan(), incompatible, expand_during_outage=False,
        runtime_incompatible=lambda **kwargs: set(),
    )
    assert decision.incompatible_models == frozenset({"model-a"})
    assert decision.excluded_models == frozenset({"model-a"})


def test_policy_honors_completed_and_current_executor_retry_evidence():
    assert retry_plan(plan(), [trial(status="COMPLETED")], now=NOW) is None
    # Old failures may not poison a new executor generation.
    @dataclass(frozen=True)
    class TrialPlan:
        plan_id: str = "plan-a"
        evidence_refs: tuple = ()
        max_requests: int = 3
    fresh = retry_plan(TrialPlan(), [trial()], now=NOW)
    assert fresh is not None and fresh.plan_id.endswith(":executor-refresh")


def test_executor_does_not_persist_or_choose_retry_policy():
    requests = []
    measurement = SimpleNamespace(to_payload=lambda: {
        "trial_id": "trial-1", "status": "COMPLETED",
    })
    executor = ProviderTrialExecutor(
        resolve_target=lambda env, **kw: requests.append(("resolve", env))
        or SimpleNamespace(model="model-a"),
        run_trial=lambda candidate, **kw: requests.append(("trial", candidate.plan_id))
        or [measurement],
        to_trials=lambda data: [trial(status="COMPLETED")],
    )
    target = executor.resolve(plan(), api_key="fake-key", excluded_models=frozenset())
    result = executor.execute(plan(), api_key="fake-key", target=target, now=NOW)
    assert target.model == "model-a"
    assert len(result.measurements) == len(result.trials) == 1
    assert requests == [("resolve", "ALPHA_API_KEY"), ("trial", "plan-a")]


def test_recorder_deduplicates_world_outcome_and_records_model_receipts(tmp_path):
    class Scout:
        def __init__(self): self.rows = []
        def record_model_trial(self, row): self.rows.append(row)
    def writer(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    scout = Scout()
    recorder = ResourceOutcomeRecorder(
        state_root=tmp_path, scout=scout, write_json=writer,
    )
    first = recorder.record_acquisition_outcome(
        plan_id="p", lead_id="lead-a", status="TRIAL_FAILED", now=NOW,
        evidence_refs=("trial-1",), detail_summary="failed",
    )
    second = recorder.record_acquisition_outcome(
        plan_id="p", lead_id="lead-a", status="TRIAL_FAILED",
        now=NOW + timedelta(hours=4),
        evidence_refs=("trial-1",), detail_summary="failed",
    )
    assert first["created"] is True
    assert second["created"] is False
    assert first["outcome_id"] == second["outcome_id"]
    assert len(recorder.acquisition_outcomes()) == 1
    measurement = SimpleNamespace(to_payload=lambda: {
        "trial_id": "trial-1", "status": "FAILED",
    })
    recorder.record_trials(SimpleNamespace(
        measurements=[measurement], trials=[trial()],
    ))
    assert len(scout.rows) == 1
    receipts = list((tmp_path / "application/resource_discovery/trial_receipts").glob("*.json"))
    assert len(receipts) == 1


def test_feedback_consumes_existing_history_without_executor_call(tmp_path, monkeypatch):
    from wechat_traffic_agent import llm_resources
    monkeypatch.setattr(
        llm_resources, "sync_llm_resource_pool",
        lambda **kwargs: {"status": "SYNCHRONIZED"},
    )
    selection = []
    consumer = ResourceFeedbackConsumer(
        state_root=tmp_path,
        scout=SimpleNamespace(model_trials=lambda: []),
        persist_selection=lambda **kwargs: selection.append(kwargs)
        or {"status": "NO_EMPIRICALLY_VIABLE_PROVIDER"},
    )
    first = consumer.consume(
        leads=[], available=set(), now=NOW,
        executed_measurements=[], provider_rows=[], replan_required=False,
    )
    second = consumer.consume(
        leads=[], available=set(), now=NOW,
        executed_measurements=[], provider_rows=[], replan_required=False,
    )
    assert first["status"] == second["status"] == "NO_READY_TRIALS"
    assert first["llm_resource_pool"]["status"] == "SYNCHRONIZED"
    assert first["diagnostics"] == []
    assert len(selection) == 2


def test_all_resource_ports_are_acyclic_with_small_methods():
    root = Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent"
    for filename in (
        "resource_trial_policy.py", "provider_trial_executor.py",
        "resource_outcome_recorder.py", "resource_feedback_consumer.py",
        "resource_trial_coordinator.py",
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "resource_scout"
                assert node.module != "wakeful_runtime"
                assert node.module != "live_agent"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    filename, node.name,
                )
    tree = ast.parse((root / "resource_scout.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "run_ready_trials")
    assert fn.end_lineno - fn.lineno + 1 <= 50
