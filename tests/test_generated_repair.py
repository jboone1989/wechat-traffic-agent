from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from wechat_traffic_agent.generated_repair import (
    GeneratedRepairPlan,
    GeneratedRepairPlanner,
)
from wechat_traffic_agent.self_repair_executor import SelfRepairExecutor


NOW = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
SOURCE_PATH = "src/wechat_traffic_agent/app_logic.py"
SOURCE_BLOCK = (
    'def normalize_topic(topic):\n'
    '    if not topic:\n'
    '        raise ValueError("Traffic opportunity requires topic: risk")\n'
    '    return topic\n'
)
BLOCK_ID = "block:" + sha256((SOURCE_PATH + "\0" + SOURCE_BLOCK).encode()).hexdigest()[:24]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class PlannerRuntime:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[str] = []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        return json.dumps(self.payload)


class LLMProviderError(RuntimeError):
    pass


class FailingPlannerRuntime:
    def __init__(self):
        self.calls: list[str] = []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        raise LLMProviderError("all eligible LLM resources failed without exposing provider responses")


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "src/wechat_traffic_agent").mkdir(parents=True)
    (root / "tests").mkdir()
    source = root / "src/wechat_traffic_agent/app_logic.py"
    source.write_text(SOURCE_BLOCK)
    (root / "tests/test_app_logic.py").write_text(
        "from pathlib import Path\n"
        "def test_topic_fallback_exists():\n"
        "    text = Path('src/wechat_traffic_agent/app_logic.py').read_text()\n"
        "    assert 'return topic or \\\"risk\\\"' in text\n"
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "ferro@example.invalid")
    _git(root, "config", "user.name", "Ferro Self Repair")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def _problem(state: Path) -> Path:
    path = state / "application/self_maintenance/problems/traffic.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": "self-problem.v1",
        "problem_id": "self-problem:traffic-topic",
        "status": "DIAGNOSING",
        "symptom": "Traffic opportunity requires topic: risk",
        "error_type": "ValueError",
        "failure_site": "unknown",
        "occurrences": 1,
        "cycles": ["v2-1"],
        "last_seen_at": NOW.isoformat(),
        "evidence_refs": ["content-cycle:v2-1:result"],
        "repair_proposal": None,
    }))
    return path


def _valid_plan() -> dict:
    return {
        "hypothesis": "A missing traffic topic should use the already selected risk topic.",
        "rationale": "The crash is raised by one localized guard and a deterministic fallback is reversible.",
        "risk": "LOW",
        "confidence": 0.91,
        "target_files": [SOURCE_PATH],
        "edits": [{
            "path": SOURCE_PATH,
            "block_id": BLOCK_ID,
            "new_text": (
                'def normalize_topic(topic):\n'
                '    return topic or "risk"\n'
            ),
        }],
        "focused_tests": ["tests/test_app_logic.py"],
        "implementation_markers": [{
            "path": SOURCE_PATH,
            "marker": 'return topic or "risk"',
        }],
    }


def test_logical_wakeful_failure_site_maps_back_to_bounded_source_files(tmp_path):
    workspace = _workspace(tmp_path)
    wakeful = workspace / "src/wechat_traffic_agent/wakeful_tasks.py"
    wakeful.write_text(
        'def observe():\n'
        '    rows["draft_worker"] = run_draft()\n'
        '    rows["resource_observation"] = observe_resources()\n'
    )
    draft_worker = workspace / "src/wechat_traffic_agent/trading_draft_worker.py"
    draft_worker.write_text("def run_draft():\n    return {}\n")
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    observe_sources = planner.candidate_source_paths({
        "failure_site": "observe_world:draft_worker",
        "symptom": "failed components: draft_worker",
    })
    resource_sources = planner.candidate_source_paths({
        "failure_site": "resource_review:resource_observation",
        "symptom": "failed components: resource_observation",
    })

    assert observe_sources == [
        "src/wechat_traffic_agent/wakeful_tasks.py",
        "src/wechat_traffic_agent/trading_draft_worker.py",
    ]
    assert resource_sources == [
        "src/wechat_traffic_agent/wakeful_tasks.py",
    ]


def test_capability_contract_failure_sites_localize_to_affected_modules(tmp_path):
    workspace = _workspace(tmp_path)
    for name in (
        "cognition_context.py",
        "behavioral_state.py",
        "wakeful_tasks.py",
        "owner_agent_episode.py",
        "survival_resources.py",
    ):
        (workspace / "src/wechat_traffic_agent" / name).write_text(
            "def capability_surface():\n    return 'ok'\n"
        )
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    assert planner.candidate_source_paths({
        "failure_site": "cognition_context.py:capability_contract",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: context selection empty",
    }) == ["src/wechat_traffic_agent/cognition_context.py"]
    assert planner.candidate_source_paths({
        "failure_site": "behavioral_state.py:capability_contract",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: neural behavior missing",
    }) == ["src/wechat_traffic_agent/behavioral_state.py"]
    assert planner.candidate_source_paths({
        "failure_site": "wakeful_tasks.py:goal_effect_contract",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: repeated action has no effect",
    }) == ["src/wechat_traffic_agent/wakeful_tasks.py"]
    assert planner.candidate_source_paths({
        "failure_site": "resource_review:survival_resources",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: resource goal repeatedly has no effect",
    }) == [
        "src/wechat_traffic_agent/wakeful_tasks.py",
        "src/wechat_traffic_agent/survival_resources.py",
    ]
    assert planner.candidate_source_paths({
        "failure_site": "owner_agent_episode.py:tool_effect_contract",
        "symptom": "CAPABILITY_CONTRACT_VIOLATION: repeated tool has no new evidence",
    }) == ["src/wechat_traffic_agent/owner_agent_episode.py"]


def test_interoceptive_diagnostic_evidence_localizes_status_producer_and_editable_block(tmp_path):
    workspace = _workspace(tmp_path)
    x_social = workspace / "src/wechat_traffic_agent/x_social.py"
    x_social.write_text(
        "async def publish_social():\n"
        "    transport_failover = {\n"
        "        'status': 'BROWSER_FALLBACK_USED',\n"
        "        'fallback_transport': 'JEV_ACCOUNT_BROWSER',\n"
        "    }\n"
        "    return {'transport_failover': transport_failover}\n",
        encoding="utf-8",
    )
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )
    problem = {
        "failure_site": "interoception:network",
        "symptom": "interoceptive fallback_dependency in network; task=CONTENT_COGNITION",
        "diagnostic_evidence": {
            "symptoms": [{
                "kind": "fallback_dependency",
                "path": "result.x_social_expression.transport_failover.status",
                "detail": "BROWSER_FALLBACK_USED",
                "severity": 0.34,
            }],
            "status_evidence": [{
                "path": "result.x_social_expression.transport_failover.status",
                "status": "BROWSER_FALLBACK_USED",
            }],
        },
    }

    sources = planner.candidate_source_paths(problem)
    blocks = planner.editable_blocks(problem, sources)
    context = planner.source_context(problem, sources[0])

    assert sources == ["src/wechat_traffic_agent/x_social.py"]
    assert blocks
    assert blocks[0].path == "src/wechat_traffic_agent/x_social.py"
    assert blocks[0].symbol == "publish_social"
    assert "BROWSER_FALLBACK_USED" in context["snippet"]


def test_generated_repair_requires_two_separate_cycles_before_edit(tmp_path):
    state = tmp_path / "state"
    workspace = _workspace(tmp_path)
    deployed = tmp_path / "deployed"
    (deployed / "src/wechat_traffic_agent").mkdir(parents=True)
    (deployed / "src/wechat_traffic_agent/app_logic.py").write_text(
        (workspace / "src/wechat_traffic_agent/app_logic.py").read_text()
    )
    problem_path = _problem(state)
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=deployed,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    first = executor.run_once(now=NOW)

    assert first["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    assert _git(workspace, "status", "--porcelain") == ""
    assert _git(workspace, "branch", "--show-current") == "main"
    stored = json.loads(problem_path.read_text())
    assert stored["status"] == "REPAIR_PROPOSED"
    assert stored["repair_proposal"]["repair_kind"] == "GENERATED_BOUNDED_EDIT"
    assert stored["repair_proposal"]["planner_confidence"] == 0.91

    second = executor.run_once(now=NOW)

    assert second["status"] == "CAPABILITY_UNAVAILABLE"
    assert len(runtime.calls) == 1
    assert 'raise ValueError("Traffic opportunity requires topic: risk")' in (
        workspace / "src/wechat_traffic_agent/app_logic.py"
    ).read_text()
    assert _git(workspace, "status", "--porcelain") == ""


def test_repair_batch_advances_distinct_problems_with_bounded_throughput(tmp_path):
    state = tmp_path / "state"
    workspace = _workspace(tmp_path)
    deployed = tmp_path / "deployed"
    (deployed / "src/wechat_traffic_agent").mkdir(parents=True)
    (deployed / "src/wechat_traffic_agent/app_logic.py").write_text(
        (workspace / "src/wechat_traffic_agent/app_logic.py").read_text()
    )
    first_path = _problem(state)
    template = json.loads(first_path.read_text())
    for index in (2, 3):
        row = dict(template)
        row["problem_id"] = f"self-problem:traffic-topic-{index}"
        row["occurrences"] = index
        row["cycles"] = [f"v2-{index}"]
        row["evidence_refs"] = [f"content-cycle:v2-{index}:result"]
        (first_path.parent / f"traffic-{index}.json").write_text(json.dumps(row))

    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=deployed,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    batch = executor.run_batch(now=NOW, max_items=3)

    assert batch["status"] == "BATCH_PROGRESSED"
    assert batch["processed_count"] == 3
    assert len(set(batch["problem_ids"])) == 3
    assert batch["result_statuses"] == [
        "GENERATED_REPAIR_PROPOSED",
        "GENERATED_REPAIR_PROPOSED",
        "GENERATED_REPAIR_PROPOSED",
    ]
    assert len(runtime.calls) == 3


def test_model_cannot_escape_localized_boundary_by_claiming_target_path(tmp_path):
    state = tmp_path / "state"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    payload = _valid_plan()
    payload["target_files"] = ["src/wechat_traffic_agent/self_repair_executor.py"]
    payload["edits"][0]["path"] = payload["target_files"][0]
    payload["implementation_markers"][0]["path"] = payload["target_files"][0]
    runtime = PlannerRuntime(payload)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert result["target_files"] == [SOURCE_PATH]
    assert _git(workspace, "status", "--porcelain") == ""
    stored = json.loads(problem_path.read_text())
    recipe = stored["repair_proposal"]["generated_recipe"]
    assert recipe["target_files"] == [SOURCE_PATH]
    assert recipe["edits"][0]["path"] == SOURCE_PATH
    assert recipe["implementation_markers"] == []


def test_repair_authority_files_are_never_generated_targets(tmp_path):
    planner = GeneratedRepairPlanner(
        workspace_root=_workspace(tmp_path),
        runtime=PlannerRuntime(_valid_plan()),
    )

    assert planner.safe_source_path("src/wechat_traffic_agent/pi_remote_repair.py") is False
    assert planner.safe_source_path("src/wechat_traffic_agent/repair_request.py") is False


def test_planner_rejects_generated_network_or_process_capability(tmp_path):
    workspace = _workspace(tmp_path)
    runtime = PlannerRuntime(_valid_plan())
    planner = GeneratedRepairPlanner(workspace_root=workspace, runtime=runtime)
    payload = _valid_plan()
    payload["edits"][0]["new_text"] = (
        'def normalize_topic(topic):\n'
        '    import subprocess\n'
        '    subprocess.run(["curl", "https://example.com"])\n'
        '    return topic or "risk"\n'
    )
    plan = GeneratedRepairPlan.model_validate(payload)

    problem = json.loads(_problem(tmp_path / "state-capability").read_text())
    blocks = planner.editable_blocks(problem, [SOURCE_PATH])
    with pytest.raises(ValueError, match="forbidden capability"):
        planner.validate(
            plan,
            allowed_sources=[SOURCE_PATH],
            allowed_tests=["tests/test_app_logic.py"],
            editable_blocks=blocks,
        )


def test_planner_rejects_low_confidence_instead_of_guessing(tmp_path):
    workspace = _workspace(tmp_path)
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )
    payload = _valid_plan()
    payload["confidence"] = 0.4
    plan = GeneratedRepairPlan.model_validate(payload)

    problem = json.loads(_problem(tmp_path / "state-confidence").read_text())
    blocks = planner.editable_blocks(problem, [SOURCE_PATH])
    with pytest.raises(ValueError, match="confidence below"):
        planner.validate(
            plan,
            allowed_sources=[SOURCE_PATH],
            allowed_tests=["tests/test_app_logic.py"],
            editable_blocks=blocks,
        )


def test_source_context_exposes_raw_source_without_synthetic_line_prefixes(tmp_path):
    workspace = _workspace(tmp_path)
    state = tmp_path / "state"
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    context = planner.source_context(
        problem, "src/wechat_traffic_agent/app_logic.py"
    )

    assert context["snippet"].startswith("def normalize_topic(topic):")
    assert 'raise ValueError("Traffic opportunity requires topic: risk")' in context["snippet"]
    assert not context["snippet"].startswith("1:")


def test_rejected_problem_is_retryable_after_planner_version_upgrade(tmp_path):
    state = tmp_path / "state"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["planner_attempt"] = {
        "status": "REJECTED",
        "planner_version": "generated-repair.v7",
        "occurrences_seen": 1,
        "reason": "ValueError:implementation marker absent after edits",
    }
    problem_path.write_text(json.dumps(problem))
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    updated = json.loads(problem_path.read_text())
    assert updated["planner_attempt"]["planner_version"] == "generated-repair.v9"



def test_historical_unlocalized_problem_retries_after_localizer_version_upgrade(tmp_path):
    state = tmp_path / "state-unlocalized-upgrade"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem.update({
        "status": "AUTO_REPAIR_UNLOCALIZED",
        "symptom": "interoceptive fallback_dependency in network; task=CONTENT_COGNITION",
        "error_type": "INTEROCEPTIVE_FAULT",
        "failure_site": "interoception:network",
        "task": "CONTENT_COGNITION",
    })
    problem.pop("diagnostic_evidence", None)
    problem["planner_attempt"] = {
        "status": "UNLOCALIZED",
        "planner_version": "generated-repair.v8",
        "occurrences_seen": 1,
        "observed_at": NOW.isoformat(),
        "reason": "no bounded candidate source paths for the current failure evidence",
    }
    problem_path.write_text(json.dumps(problem))
    episode_path = (
        state
        / "application"
        / "interoception"
        / "episodes"
        / "historical.json"
    )
    episode_path.parent.mkdir(parents=True)
    episode_path.write_text(json.dumps({
        "episode_id": "fault-episode:historical",
        "task": "CONTENT_COGNITION",
        "subsystem": "network",
        "primary_symptom": "fallback_dependency",
        "last_seen_at": NOW.isoformat(),
        "symptoms": [{
            "kind": "fallback_dependency",
            "severity": 0.34,
            "path": "result.topic_normalization",
            "detail": "Traffic opportunity requires topic: risk",
        }],
    }))
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "REPAIR_PROPOSED"
    assert updated["planner_attempt"]["planner_version"] == "generated-repair.v9"
    assert updated["diagnostic_evidence"]["recovered_from_episode"] is True
    assert (
        updated["diagnostic_evidence"]["interoceptive_episode_id"]
        == "fault-episode:historical"
    )


def test_editable_block_id_binds_authoritative_source(tmp_path):
    workspace = _workspace(tmp_path)
    state = tmp_path / "state-block"
    problem = json.loads(_problem(state).read_text())
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    blocks = planner.editable_blocks(problem, [SOURCE_PATH])

    assert len(blocks) == 1
    assert blocks[0].block_id == BLOCK_ID
    assert blocks[0].old_text == SOURCE_BLOCK
    assert blocks[0].symbol == "normalize_topic"


def test_stale_block_id_is_rejected_after_source_changes(tmp_path):
    workspace = _workspace(tmp_path)
    state = tmp_path / "state-stale"
    problem = json.loads(_problem(state).read_text())
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )
    plan = GeneratedRepairPlan.model_validate(_valid_plan())
    old_blocks = planner.editable_blocks(problem, [SOURCE_PATH])
    (workspace / SOURCE_PATH).write_text(SOURCE_BLOCK.replace("return topic", "return str(topic)"))
    new_blocks = planner.editable_blocks(problem, [SOURCE_PATH])

    assert old_blocks[0].block_id != new_blocks[0].block_id
    with pytest.raises(ValueError, match="unknown block_id"):
        planner.validate(
            plan,
            allowed_sources=[SOURCE_PATH],
            allowed_tests=["tests/test_app_logic.py"],
            editable_blocks=new_blocks,
        )



def test_transient_llm_failure_defers_without_poisoning_problem(tmp_path):
    state = tmp_path / "state-deferred"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    runtime = FailingPlannerRuntime()
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    first = executor.run_once(now=NOW)

    assert first["status"] == "GENERATED_PLAN_DEFERRED"
    assert first["planner_status"] == "DEFERRED_RESOURCE"
    assert first["retry_seconds"] == 900
    stored = json.loads(problem_path.read_text())
    assert stored["status"] == "DIAGNOSING"
    assert stored["planner_attempt"]["status"] == "DEFERRED_RESOURCE"
    assert stored["planner_attempt"]["planner_version"] == "generated-repair.v9"
    assert stored["planner_attempt"]["retry_after"] == (
        NOW + timedelta(seconds=900)
    ).isoformat()
    assert len(runtime.calls) == 1

    immediate = executor.run_once(now=NOW + timedelta(seconds=1))

    assert immediate["status"] == "NO_REPAIR_WORK"
    assert len(runtime.calls) == 1

    retried = executor.run_once(now=NOW + timedelta(seconds=901))

    assert retried["status"] == "GENERATED_PLAN_DEFERRED"
    assert len(runtime.calls) == 2


def test_historical_rejected_llm_resource_failure_is_retryable_without_new_occurrence(tmp_path):
    state = tmp_path / "state-old-resource-reject"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["planner_attempt"] = {
        "status": "REJECTED",
        "planner_version": "generated-repair.v7",
        "occurrences_seen": 1,
        "observed_at": NOW.isoformat(),
        "reason": "LLMProviderError:all eligible LLM resources failed without exposing provider responses",
    }
    problem_path.write_text(json.dumps(problem))
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "REPAIR_PROPOSED"
    assert updated["planner_attempt"]["status"] == "PROPOSED"



class MalformedPlannerRuntime:
    def __init__(self):
        self.calls: list[str] = []

    async def cognitive_probe(self, task, max_tokens, output_schema=None):
        self.calls.append(task)
        return "not-json"


def test_malformed_planner_output_is_deferred_and_retried(tmp_path):
    state = tmp_path / "state-malformed"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    runtime = MalformedPlannerRuntime()
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    first = executor.run_once(now=NOW)

    assert first["status"] == "GENERATED_PLAN_DEFERRED"
    assert first["planner_status"] == "DEFERRED_MODEL_OUTPUT"
    assert first["retry_seconds"] == 300
    stored = json.loads(problem_path.read_text())
    assert stored["status"] == "DIAGNOSING"
    assert stored["planner_attempt"]["status"] == "DEFERRED_MODEL_OUTPUT"
    assert stored["planner_attempt"]["retry_count"] == 1
    assert stored["planner_attempt"]["retry_after"] == (
        NOW + timedelta(seconds=300)
    ).isoformat()
    assert len(runtime.calls) == 2

    immediate = executor.run_once(now=NOW + timedelta(seconds=10))
    assert immediate["status"] == "NO_REPAIR_WORK"
    assert len(runtime.calls) == 2

    second = executor.run_once(now=NOW + timedelta(seconds=301))
    assert second["status"] == "GENERATED_PLAN_DEFERRED"
    assert second["planner_status"] == "DEFERRED_MODEL_OUTPUT"
    assert second["retry_count"] == 2
    assert len(runtime.calls) == 4


def test_transient_planner_retry_budget_exhausts_after_three_scheduled_retries(tmp_path):
    state = tmp_path / "state-retry-budget"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    runtime = MalformedPlannerRuntime()
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    first = executor.run_once(now=NOW)
    assert first["status"] == "GENERATED_PLAN_DEFERRED"
    second = executor.run_once(now=NOW + timedelta(seconds=301))
    assert second["status"] == "GENERATED_PLAN_DEFERRED"
    third = executor.run_once(now=NOW + timedelta(seconds=602))
    assert third["status"] == "GENERATED_PLAN_DEFERRED"
    fourth = executor.run_once(now=NOW + timedelta(seconds=903))

    assert fourth["status"] == "GENERATED_PLAN_RETRY_EXHAUSTED"
    assert fourth["planner_status"] == "REJECTED"
    stored = json.loads(problem_path.read_text())
    assert stored["planner_attempt"]["retry_count"] == 4
    assert stored["planner_attempt"]["status"] == "REJECTED"
    assert stored["status"] == "AUTO_REPAIR_REJECTED"


def test_historical_rejected_validation_error_is_retryable_without_new_occurrence(tmp_path):
    state = tmp_path / "state-old-validation-reject"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["planner_attempt"] = {
        "status": "REJECTED",
        "planner_version": "generated-repair.v7",
        "occurrences_seen": 1,
        "observed_at": NOW.isoformat(),
        "reason": "ValidationError:1 validation error for GeneratedRepairPlan Invalid JSON: trailing characters",
    }
    problem_path.write_text(json.dumps(problem))
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    updated = json.loads(problem_path.read_text())
    assert updated["status"] == "REPAIR_PROPOSED"
    assert updated["planner_attempt"]["status"] == "PROPOSED"



def test_model_supplied_marker_is_non_authoritative_and_runtime_derives_adoption_marker(tmp_path):
    workspace = _workspace(tmp_path)
    state = tmp_path / "state-marker"
    problem = json.loads(_problem(state).read_text())
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )
    payload = _valid_plan()
    payload["implementation_markers"] = [{
        "path": SOURCE_PATH,
        "marker": "marker-the-model-invented-but-the-edit-does-not-contain",
    }]
    plan = GeneratedRepairPlan.model_validate(payload)
    blocks = planner.editable_blocks(problem, [SOURCE_PATH])

    validated = planner.validate(
        plan,
        allowed_sources=[SOURCE_PATH],
        allowed_tests=["tests/test_app_logic.py"],
        editable_blocks=blocks,
    )
    materialized = planner.materialize_edits(validated, blocks)
    recipe = SelfRepairExecutor._recipe_from_plan(validated, materialized)

    assert recipe.implementation_markers == ((
        SOURCE_PATH,
        payload["edits"][0]["new_text"],
    ),)


def test_generated_repair_plan_does_not_require_model_marker():
    payload = _valid_plan()
    payload.pop("implementation_markers")

    plan = GeneratedRepairPlan.model_validate(payload)

    assert plan.implementation_markers == []


def test_model_may_omit_runtime_owned_paths_and_tests():
    payload = _valid_plan()
    payload.pop("target_files")
    payload.pop("focused_tests")
    payload.pop("implementation_markers")
    payload["edits"][0].pop("path")

    plan = GeneratedRepairPlan.model_validate(payload)

    assert plan.target_files == []
    assert plan.focused_tests == []
    assert plan.edits[0].path == ""
    assert plan.implementation_markers == []


def test_model_selected_unapproved_test_is_ignored_by_authoritative_binding(tmp_path):
    state = tmp_path / "state-authoritative-tests"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    payload = _valid_plan()
    payload["focused_tests"] = ["tests/test_model_invented.py"]
    runtime = PlannerRuntime(payload)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert result["focused_tests"] == ["tests/test_app_logic.py"]
    stored = json.loads(problem_path.read_text())
    recipe = stored["repair_proposal"]["generated_recipe"]
    assert recipe["focused_tests"] == ["tests/test_app_logic.py"]
    assert stored["planner_attempt"]["planner_version"] == "generated-repair.v9"



def test_counterevidence_is_included_in_next_generated_repair_prompt(tmp_path):
    state = tmp_path / "state-counterevidence-prompt"
    workspace = _workspace(tmp_path)
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["repair_trial"] = {
        "status": "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
        "baseline_status": "PASSED",
        "previous_hypothesis": "Return an empty JSON object when parsing fails.",
        "previous_edits": [{
            "path": SOURCE_PATH,
            "block_id": BLOCK_ID,
            "new_text": "def normalize_topic(topic):\n    return {}\n",
        }],
        "failed_tests": [
            "tests/test_app_logic.py::test_original_contract",
        ],
        "assertion_evidence": [
            "AssertionError: original contract changed",
        ],
    }
    problem["planner_attempt"] = {
        "status": "COUNTEREVIDENCE_READY",
        "planner_version": "generated-repair.v7",
        "occurrences_seen": 1,
        "observed_at": NOW.isoformat(),
    }
    problem_path.write_text(json.dumps(problem))
    runtime = PlannerRuntime(_valid_plan())
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert len(runtime.calls) == 1
    prompt = runtime.calls[0]
    assert "prior_repair_trial" in prompt
    assert "tests/test_app_logic.py::test_original_contract" in prompt
    assert "Return an empty JSON object when parsing fails." in prompt
    assert "Do not repeat the rejected edit" in prompt



def test_counterevidence_failed_test_files_are_authoritative_allowed_tests(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "tests/test_counter_contract.py").write_text(
        "def test_preserved_contract():\n    assert True\n",
        encoding="utf-8",
    )
    state = tmp_path / "state-counter-tests"
    problem = json.loads(_problem(state).read_text())
    problem["repair_trial"] = {
        "status": "REJECTED_BY_AUTHORITATIVE_REPLAY",
        "baseline_status": "PASSED",
        "candidate_replay_status": "FAILED",
        "failed_tests": [
            "tests/test_counter_contract.py::test_preserved_contract",
            "tests/test_app_logic.py::test_topic_fallback_exists",
        ],
        "assertion_evidence": ["AssertionError: preserve this contract"],
    }
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    selected = planner.candidate_test_paths(problem, [SOURCE_PATH])

    assert selected[:2] == [
        "tests/test_counter_contract.py",
        "tests/test_app_logic.py",
    ]


def test_counterevidence_test_paths_reject_traversal_non_tests_and_missing_files(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "tests/test_safe_contract.py").write_text(
        "def test_safe():\n    assert True\n",
        encoding="utf-8",
    )
    state = tmp_path / "state-counter-test-boundary"
    problem = json.loads(_problem(state).read_text())
    problem["repair_trial"] = {
        "failed_tests": [
            "../tests/test_escape.py::test_escape",
            "/tmp/test_absolute.py::test_absolute",
            "src/wechat_traffic_agent/app_logic.py::test_not_a_test",
            "tests/helper.py::test_helper",
            "tests/test_missing.py::test_missing",
            "tests/test_safe_contract.py::test_safe",
        ]
    }
    planner = GeneratedRepairPlanner(
        workspace_root=workspace,
        runtime=PlannerRuntime(_valid_plan()),
    )

    selected = planner._counterevidence_test_paths(problem)

    assert selected == ["tests/test_safe_contract.py"]


def test_planner_can_select_counterevidence_test_that_scoring_would_not_discover(tmp_path):
    workspace = _workspace(tmp_path)
    counter_test = "tests/test_counter_contract.py"
    (workspace / counter_test).write_text(
        "def test_preserved_contract():\n    assert True\n",
        encoding="utf-8",
    )
    state = tmp_path / "state-counter-plan"
    problem_path = _problem(state)
    problem = json.loads(problem_path.read_text())
    problem["repair_trial"] = {
        "status": "REJECTED_BY_AUTHORITATIVE_REPLAY",
        "baseline_status": "PASSED",
        "candidate_replay_status": "FAILED",
        "failed_tests": [
            counter_test + "::test_preserved_contract",
        ],
        "assertion_evidence": [
            "AssertionError: prior candidate violated preserved contract",
        ],
    }
    problem["planner_attempt"] = {
        "status": "REJECTED",
        "planner_version": "generated-repair.v5",
        "occurrences_seen": 1,
        "observed_at": NOW.isoformat(),
        "reason": "ValueError:selected an unapproved test",
    }
    problem_path.write_text(json.dumps(problem))
    payload = _valid_plan()
    payload["focused_tests"] = [counter_test]
    runtime = PlannerRuntime(payload)
    executor = SelfRepairExecutor(
        state_root=state,
        workspace_root=workspace,
        deployed_root=workspace,
        python_executable=sys.executable,
        planner_runtime=runtime,
        recipes={},
    )

    result = executor.run_once(now=NOW)

    assert result["status"] == "GENERATED_REPAIR_PROPOSED"
    assert result["focused_tests"][0] == counter_test
    assert "tests/test_app_logic.py" in result["focused_tests"]
    updated = json.loads(problem_path.read_text())
    assert updated["planner_attempt"]["planner_version"] == "generated-repair.v9"
    assert counter_test in updated["planner_attempt"]["allowed_tests"]
