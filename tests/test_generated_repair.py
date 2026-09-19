from __future__ import annotations

from datetime import datetime, timezone
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

    async def cognitive_probe(self, task, max_tokens):
        self.calls.append(task)
        return json.dumps(self.payload)
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

    assert second["status"] == "CANDIDATE_COMMITTED"
    assert len(runtime.calls) == 1
    assert second["commit"]["pushed"] is False
    assert second["commit"]["branch"].startswith("agent/repair-")
    assert second["focused"]["ok"] is True
    assert second["full"]["ok"] is True
    assert 'return topic or "risk"' in (
        workspace / "src/wechat_traffic_agent/app_logic.py"
    ).read_text()
    assert _git(workspace, "status", "--porcelain") == ""


def test_generated_repair_rejects_target_outside_localized_boundary(tmp_path):
    state = tmp_path / "state"
    workspace = _workspace(tmp_path)
    _problem(state)
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

    assert result["status"] == "GENERATED_PLAN_REJECTED"
    assert "localized source boundary" in result["reason"]
    assert _git(workspace, "status", "--porcelain") == ""

    second = executor.run_once(now=NOW)
    assert second["status"] == "NO_REPAIR_WORK"
    assert len(runtime.calls) == 1


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
        "planner_version": "generated-repair.v1",
        "occurrences_seen": 1,
        "reason": "old planner could not produce exact source text",
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
    assert updated["planner_attempt"]["planner_version"] == "generated-repair.v3"



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
