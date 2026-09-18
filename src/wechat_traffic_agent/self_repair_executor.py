"""Bounded RepairProposal execution and production verification."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

from .generated_repair import GeneratedRepairPlan, GeneratedRepairPlanner

from agent_runtime.coding import (
    BoundedCodingProvider,
    CAP_CODE_BRANCH,
    CAP_CODE_COMMIT,
    CAP_CODE_DIFF,
    CAP_CODE_EDIT,
    CAP_CODE_INSPECT,
    CAP_CODE_TEST,
    CodingPolicy,
)


@dataclass(frozen=True)
class RepairEdit:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class RepairRecipe:
    repair_kind: str
    target_files: tuple[str, ...]
    implementation_markers: tuple[tuple[str, str], ...]
    edits: tuple[RepairEdit, ...]
    focused_tests: tuple[str, ...]


_BELIEF_OLD = """                    if value.cognition_state == 'BELIEF_CHANGED' and not changed:
                        raise ValueError('BELIEF_CHANGED requires a persisted worldview change')"""
_BELIEF_NEW = """                    if value.cognition_state == 'BELIEF_CHANGED' and not changed:
                        return"""

_BELIEF_CLASS_OLD = """    belief_proposition: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=10, max_length=600)


def _candidate_fingerprint(candidate):"""
_BELIEF_CLASS_NEW = '''    belief_proposition: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=10, max_length=600)


def _normalize_cognition_consistency(value: CognitionDecision, worldview_update: object) -> CognitionDecision:
    """Durable state is authoritative when model narration disagrees with it."""
    changed = isinstance(worldview_update, dict) and worldview_update.get("status") in {"FORMED", "REVISED"}
    if value.cognition_state != "BELIEF_CHANGED" or changed:
        return value
    return value.model_copy(update={
        "cognition_state": "INVESTIGATING",
        "what_changed": "Fresh evidence was observed, but no durable worldview change was persisted.",
        "what_matters": "The question remains open until stronger evidence justifies changing the durable worldview.",
        "belief_proposition": None,
        "rationale": "Durable worldview state is authoritative, so an unsupported belief-change claim was normalized.",
    })


def _candidate_fingerprint(candidate):'''

_BELIEF_CALL_OLD = """                    CognitionDecision, validate=validate_cognition,
                )
                _put(cognition_path, cognition_decision.model_dump(mode='json'))"""
_BELIEF_CALL_NEW = """                    CognitionDecision, validate=validate_cognition,
                )
                cognition_decision = _normalize_cognition_consistency(cognition_decision, worldview_update)
                _put(cognition_path, cognition_decision.model_dump(mode='json'))"""

_JSON_OLD = """        raw = await runtime.cognitive_probe(task, max_tokens=1800)
        try:
            value = checked(raw)
        except ValueError as exc:
            retry = task + "\\nValidation: " + str(exc)[:1000]
            value = checked(await runtime.cognitive_probe(retry, max_tokens=1800))
        selected = [registry[match.evidence_id] for match in value.matches]"""
_JSON_NEW = """        raw = await runtime.cognitive_probe(task, max_tokens=1800)
        try:
            value = checked(raw)
        except ValueError as first_exc:
            retry = task + "\\nValidation: " + str(first_exc)[:1000]
            try:
                value = checked(await runtime.cognitive_probe(retry, max_tokens=1800))
            except ValueError as second_exc:
                malformed = (
                    "backend did not return a JSON object" in str(second_exc)
                    or type(second_exc).__name__ == "ValidationError"
                )
                if not malformed:
                    raise
                # Relevance classification is an admission gate, not evidence itself.
                # Two malformed model responses must fail closed instead of killing the
                # whole life cycle or admitting unverified candidates.
                return {
                    "items": [],
                    "matches": [],
                    "candidate_count": len(candidates),
                    "classifier_status": "MODEL_OUTPUT_INVALID",
                    "classifier_error": type(second_exc).__name__,
                }
        selected = [registry[match.evidence_id] for match in value.matches]"""


DEFAULT_RECIPES: dict[str, RepairRecipe] = {
    "NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW": RepairRecipe(
        repair_kind="NORMALIZE_COGNITION_STATE_TO_DURABLE_WORLDVIEW",
        target_files=("src/wechat_traffic_agent/content_goal_loop.py",),
        implementation_markers=(
            (
                "src/wechat_traffic_agent/content_goal_loop.py",
                "def _normalize_cognition_consistency(",
            ),
            (
                "src/wechat_traffic_agent/content_goal_loop.py",
                "cognition_decision = _normalize_cognition_consistency(",
            ),
        ),
        edits=(
            RepairEdit(
                "src/wechat_traffic_agent/content_goal_loop.py",
                _BELIEF_CLASS_OLD,
                _BELIEF_CLASS_NEW,
            ),
            RepairEdit(
                "src/wechat_traffic_agent/content_goal_loop.py",
                _BELIEF_OLD,
                _BELIEF_NEW,
            ),
            RepairEdit(
                "src/wechat_traffic_agent/content_goal_loop.py",
                _BELIEF_CALL_OLD,
                _BELIEF_CALL_NEW,
            ),
        ),
        focused_tests=(
            "tests/test_content_explorer_loop.py",
            "tests/test_self_maintenance.py",
        ),
    ),
    "FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT": RepairRecipe(
        repair_kind="FAIL_CLOSED_ON_INVALID_RELEVANCE_CLASSIFIER_OUTPUT",
        target_files=("src/wechat_traffic_agent/content_explorer.py",),
        implementation_markers=(
            (
                "src/wechat_traffic_agent/content_explorer.py",
                '"classifier_status": "MODEL_OUTPUT_INVALID"',
            ),
            (
                "src/wechat_traffic_agent/content_explorer.py",
                "if not malformed:",
            ),
        ),
        edits=(
            RepairEdit(
                "src/wechat_traffic_agent/content_explorer.py",
                _JSON_OLD,
                _JSON_NEW,
            ),
        ),
        focused_tests=(
            "tests/test_content_explorer.py",
            "tests/test_self_maintenance.py",
        ),
    ),
    "DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION": RepairRecipe(
        repair_kind="DETERMINISTIC_FALLBACK_ON_INVALID_EVIDENCE_SELECTION",
        target_files=("src/wechat_traffic_agent/content_goal_loop.py",),
        implementation_markers=(
            (
                "src/wechat_traffic_agent/content_goal_loop.py",
                "def _fallback_evidence_selection(",
            ),
            (
                "src/wechat_traffic_agent/content_goal_loop.py",
                "MODEL_OUTPUT_INVALID_AFTER_REPAIR",
            ),
        ),
        edits=(),
        focused_tests=(
            "tests/test_content_explorer_loop.py",
            "tests/test_self_maintenance.py",
        ),
    ),
    "INVALIDATE_STALE_MODEL_RECOVERY_INPUT_CHANGE": RepairRecipe(
        repair_kind="INVALIDATE_STALE_MODEL_RECOVERY_INPUT_CHANGE",
        target_files=("src/wechat_traffic_agent/content_loop.py",),
        implementation_markers=(
            (
                "src/wechat_traffic_agent/content_loop.py",
                "'status': 'RECOVERY_INVALIDATED'",
            ),
            (
                "src/wechat_traffic_agent/content_loop.py",
                "'reason': 'MODEL_PHASE_INPUT_CHANGED'",
            ),
        ),
        edits=(),
        focused_tests=(
            "tests/test_content_loop.py",
            "tests/test_self_maintenance.py",
        ),
    ),
}


_GENERATED_REPAIR_KIND = "GENERATED_BOUNDED_EDIT"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


class SelfRepairExecutor:
    """Execute one bounded repair candidate, or verify one already adopted repair."""

    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path | None,
        deployed_root: Path | None,
        recipes: dict[str, RepairRecipe] | None = None,
        python_executable: str | None = None,
        planner_runtime: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.deployed_root = Path(deployed_root).resolve() if deployed_root else None
        self.recipes = dict(recipes or DEFAULT_RECIPES)
        self.python_executable = str(python_executable or sys.executable)
        self.planner_runtime = planner_runtime
        self.root = self.state_root / "application" / "self_maintenance"

    def run_once(self, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self repair requires timezone-aware time")

        problem = self._next_problem(statuses={"REPAIR_PROPOSED"})
        if problem is not None:
            return self._run_proposal(problem, now)

        diagnosing = self._next_diagnosing_problem()
        if diagnosing is not None:
            return self._generate_repair_proposal(diagnosing, now)

        observing = self._next_problem(statuses={"OBSERVING_PRODUCTION"})
        if observing is not None:
            return self._run_proposal(observing, now)
        return {"status": "NO_REPAIR_WORK"}

    def _run_proposal(self, problem: dict[str, Any], now: datetime) -> dict[str, Any]:
        proposal = problem.get("repair_proposal") or {}
        kind = str(proposal.get("repair_kind") or "")
        recipe = self._recipe_for_proposal(problem, proposal)
        if recipe is None:
            return {
                "status": "NO_BOUNDED_RECIPE",
                "problem_id": problem.get("problem_id"),
                "repair_kind": kind,
            }

        if self._implemented(self.deployed_root, recipe):
            return self._verify_adopted(problem, recipe, now)

        if self.workspace_root is None or not (self.workspace_root / ".git").exists():
            return {
                "status": "WORKSPACE_UNAVAILABLE",
                "problem_id": problem.get("problem_id"),
                "repair_kind": kind,
            }
        return self._build_candidate(problem, recipe, now)

    def _next_problem(self, *, statuses: set[str] | None = None) -> dict[str, Any] | None:
        statuses = statuses or {"REPAIR_PROPOSED", "OBSERVING_PRODUCTION"}
        rows: list[dict[str, Any]] = []
        folder = self.root / "problems"
        for path in folder.glob("*.json") if folder.exists() else ():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") not in statuses:
                continue
            if not isinstance(row.get("repair_proposal"), dict):
                continue
            row["_path"] = str(path)
            rows.append(row)
        rows.sort(
            key=lambda row: (
                str(row.get("status") or "") == "REPAIR_PROPOSED",
                int(row.get("occurrences") or 0),
                str(row.get("last_seen_at") or ""),
            ),
            reverse=True,
        )
        return rows[0] if rows else None

    def _planner(self) -> GeneratedRepairPlanner | None:
        if self.workspace_root is None or self.planner_runtime is None:
            return None
        return GeneratedRepairPlanner(
            workspace_root=self.workspace_root,
            runtime=self.planner_runtime,
        )

    def _next_diagnosing_problem(self) -> dict[str, Any] | None:
        planner = self._planner()
        if planner is None:
            return None
        rows: list[dict[str, Any]] = []
        folder = self.root / "problems"
        for path in folder.glob("*.json") if folder.exists() else ():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict) or str(row.get("status") or "") != "DIAGNOSING":
                continue
            if row.get("repair_proposal"):
                continue
            attempt = row.get("planner_attempt") if isinstance(row.get("planner_attempt"), dict) else {}
            if attempt.get("status") == "REJECTED" and (
                int(attempt.get("occurrences_seen") or 0) == int(row.get("occurrences") or 0)
            ):
                continue
            sources = planner.candidate_source_paths(row)
            if not sources:
                continue
            row["_path"] = str(path)
            row["_planner_sources"] = sources
            rows.append(row)
        rows.sort(
            key=lambda row: (
                str(row.get("failure_site") or "") not in {"", "unknown"},
                len(row.get("_planner_sources") or ()) == 1,
                int(row.get("occurrences") or 0),
                str(row.get("last_seen_at") or ""),
            ),
            reverse=True,
        )
        return rows[0] if rows else None

    def _planner_attempt(
        self,
        problem: dict[str, Any],
        *,
        now: datetime,
        status: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        path = Path(str(problem["_path"]))
        updated = {k: v for k, v in problem.items() if not str(k).startswith("_")}
        attempt = {
            "status": status,
            "observed_at": now.astimezone(timezone.utc).isoformat(),
            "occurrences_seen": int(problem.get("occurrences") or 0),
            **details,
        }
        updated["planner_attempt"] = attempt
        _atomic_json(path, updated)
        safe = str(problem.get("problem_id") or "unknown").replace(":", "-")
        _atomic_json(
            self.root / "repair_plans" / f"{safe}.json",
            {
                "schema_version": "generated-repair-plan-attempt.v1",
                "problem_id": problem.get("problem_id"),
                **attempt,
            },
        )
        return attempt

    @staticmethod
    def _recipe_from_plan(plan: GeneratedRepairPlan) -> RepairRecipe:
        return RepairRecipe(
            repair_kind=_GENERATED_REPAIR_KIND,
            target_files=tuple(plan.target_files),
            implementation_markers=tuple(
                (marker.path, marker.marker)
                for marker in plan.implementation_markers
            ),
            edits=tuple(
                RepairEdit(edit.path, edit.old_text, edit.new_text)
                for edit in plan.edits
            ),
            focused_tests=tuple(plan.focused_tests),
        )

    def _generate_repair_proposal(
        self, problem: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        planner = self._planner()
        if planner is None:
            return {"status": "PLANNER_UNAVAILABLE", "problem_id": problem.get("problem_id")}
        try:
            plan, source_paths, test_paths = asyncio.run(planner.plan(problem))
        except Exception as exc:
            attempt = self._planner_attempt(
                problem,
                now=now,
                status="REJECTED",
                details={
                    "reason": type(exc).__name__ + ":" + str(exc)[:500],
                },
            )
            return {
                **attempt,
                "status": "GENERATED_PLAN_REJECTED",
                "problem_id": problem.get("problem_id"),
                "planner_status": attempt.get("status"),
            }

        recipe = self._recipe_from_plan(plan)
        body = plan.model_dump(mode="json")
        proposal_id = "repair-proposal:generated:" + sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        path = Path(str(problem["_path"]))
        updated = {k: v for k, v in problem.items() if not str(k).startswith("_")}
        updated["status"] = "REPAIR_PROPOSED"
        updated["repair_proposal"] = {
            "schema_version": "repair-proposal.v2",
            "proposal_id": proposal_id,
            "status": "PROPOSED",
            "repair_kind": _GENERATED_REPAIR_KIND,
            "risk": "LOW",
            "reversible": True,
            "target_files": list(recipe.target_files),
            "verification": [
                "old_text matched exactly once inside localized source boundary",
                "approved focused tests must pass",
                "full repository regression must pass before local commit",
                "push and deployment remain unavailable",
            ],
            "planner_confidence": plan.confidence,
            "hypothesis": plan.hypothesis,
            "generated_recipe": body,
        }
        updated["planner_attempt"] = {
            "status": "PROPOSED",
            "observed_at": now.astimezone(timezone.utc).isoformat(),
            "occurrences_seen": int(problem.get("occurrences") or 0),
            "source_paths": source_paths,
            "allowed_tests": test_paths,
            "proposal_id": proposal_id,
        }
        _atomic_json(path, updated)
        safe = str(problem.get("problem_id") or "unknown").replace(":", "-")
        _atomic_json(
            self.root / "repair_plans" / f"{safe}.json",
            {
                "schema_version": "generated-repair-plan.v1",
                "problem_id": problem.get("problem_id"),
                "proposal_id": proposal_id,
                "status": "PROPOSED",
                "observed_at": now.astimezone(timezone.utc).isoformat(),
                "source_paths": source_paths,
                "allowed_tests": test_paths,
                "plan": body,
            },
        )
        return {
            "status": "GENERATED_REPAIR_PROPOSED",
            "problem_id": problem.get("problem_id"),
            "proposal_id": proposal_id,
            "confidence": plan.confidence,
            "target_files": list(recipe.target_files),
            "focused_tests": list(recipe.focused_tests),
        }

    def _recipe_for_proposal(
        self,
        problem: dict[str, Any],
        proposal: dict[str, Any],
    ) -> RepairRecipe | None:
        kind = str(proposal.get("repair_kind") or "")
        if kind != _GENERATED_REPAIR_KIND:
            return self.recipes.get(kind)
        raw = proposal.get("generated_recipe")
        planner = self._planner()
        if not isinstance(raw, dict) or planner is None:
            return None
        try:
            plan = GeneratedRepairPlan.model_validate(raw)
            sources = planner.candidate_source_paths(problem)
            tests = planner.candidate_test_paths(problem, sources)
            validated = planner.validate(
                plan,
                allowed_sources=sources,
                allowed_tests=tests,
            )
            return self._recipe_from_plan(validated)
        except Exception:
            return None

    @staticmethod
    def _implemented(root: Path | None, recipe: RepairRecipe) -> bool:
        if root is None or not root.is_dir():
            return False
        for relative, marker in recipe.implementation_markers:
            path = root / relative
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return False
            if marker not in text:
                return False
        return True

    def _provider(self, recipe: RepairRecipe) -> BoundedCodingProvider:
        assert self.workspace_root is not None
        focused = (
            (
                self.python_executable,
                "-B",
                "-m",
                "pytest",
                "-q",
                *recipe.focused_tests,
            ),
        )
        full = ((self.python_executable, "-B", "-m", "pytest", "-q"),)
        return BoundedCodingProvider(
            CodingPolicy(
                repo_root=self.workspace_root,
                test_profiles={
                    "repair-focused": focused,
                    "repair-full": full,
                },
                branch_prefix="agent/repair-",
                max_test_seconds=240.0,
            )
        )

    def _verify_adopted(
        self, problem: dict[str, Any], recipe: RepairRecipe, now: datetime
    ) -> dict[str, Any]:
        if self.workspace_root is None or not self._implemented(self.workspace_root, recipe):
            return {
                "status": "DEPLOYED_IMPLEMENTATION_PRESENT_WORKSPACE_UNAVAILABLE",
                "problem_id": problem.get("problem_id"),
                "repair_kind": recipe.repair_kind,
            }
        provider = self._provider(recipe)
        test = provider.invoke(CAP_CODE_TEST, {"profile": "repair-focused"})
        if not bool(test.get("ok")):
            return self._record_trial(
                problem,
                status="REJECTED_VERIFICATION_TESTS",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "test": test},
                update_problem=False,
            )
        later = self._later_success(problem)
        if later is None:
            return self._record_trial(
                problem,
                status="OBSERVING_PRODUCTION",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "test": test},
                update_problem=True,
            )

        workspace_sha = self._git_sha(self.workspace_root)
        deployed_sha = self._git_sha(self.deployed_root)
        evidence = {
            "repair_kind": recipe.repair_kind,
            "focused_test_profile": "repair-focused",
            "workspace_commit": workspace_sha,
            "deployed_commit": deployed_sha,
            "production_outcome": later,
        }
        return self._record_trial(
            problem,
            status="ADOPTED_VERIFIED",
            now=now,
            extra=evidence,
            update_problem=True,
        )

    def _build_candidate(
        self, problem: dict[str, Any], recipe: RepairRecipe, now: datetime
    ) -> dict[str, Any]:
        if not recipe.edits:
            return {
                "status": "NO_BOUNDED_EDIT_RECIPE",
                "problem_id": problem.get("problem_id"),
                "repair_kind": recipe.repair_kind,
            }
        provider = self._provider(recipe)
        inspect = provider.invoke(CAP_CODE_INSPECT, {})
        branch = str(inspect.get("branch") or "")
        if not bool(inspect.get("clean")):
            return {
                "status": "WORKSPACE_DIRTY_QUARANTINED",
                "problem_id": problem.get("problem_id"),
                "branch": branch or None,
            }
        if branch.startswith("agent/repair-"):
            return {
                "status": "WORKSPACE_HAS_PENDING_CANDIDATE",
                "problem_id": problem.get("problem_id"),
                "branch": branch,
            }

        provider.invoke(
            CAP_CODE_BRANCH,
            {"task_id": str(problem.get("problem_id") or "self-problem").replace(":", "-")},
        )
        for edit in recipe.edits:
            provider.invoke(
                CAP_CODE_EDIT,
                {
                    "path": edit.path,
                    "old_text": edit.old_text,
                    "new_text": edit.new_text,
                    "expected_replacements": 1,
                },
            )
        focused = provider.invoke(CAP_CODE_TEST, {"profile": "repair-focused"})
        if not bool(focused.get("ok")):
            return self._record_trial(
                problem,
                status="REJECTED_FOCUSED_TESTS_WORKSPACE_QUARANTINED",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "test": focused},
                update_problem=False,
            )
        full = provider.invoke(CAP_CODE_TEST, {"profile": "repair-full"})
        if not bool(full.get("ok")):
            return self._record_trial(
                problem,
                status="REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "focused": focused, "full": full},
                update_problem=False,
            )
        diff = provider.invoke(CAP_CODE_DIFF, {})
        commit = provider.invoke(
            CAP_CODE_COMMIT,
            {"message": "fix: bounded self repair " + recipe.repair_kind.lower()[:96]},
        )
        return self._record_trial(
            problem,
            status="CANDIDATE_COMMITTED",
            now=now,
            extra={
                "repair_kind": recipe.repair_kind,
                "focused": focused,
                "full": full,
                "diff": {
                    "changed_paths": diff.get("changed_paths"),
                    "truncated": diff.get("truncated"),
                },
                "commit": commit,
            },
            update_problem=True,
        )

    def _later_success(self, problem: dict[str, Any]) -> dict[str, Any] | None:
        failed = []
        for value in problem.get("cycles") or ():
            text = str(value)
            tail = text.rsplit("-", 1)[-1]
            if tail.isdigit():
                failed.append(int(tail))
        if not failed:
            return None
        cutoff = max(failed)
        root = self.state_root / "content" / "continuous" / "cycles"
        candidates: list[tuple[int, Path]] = []
        for path in root.glob("v2-*/result.json") if root.exists() else ():
            tail = path.parent.name.rsplit("-", 1)[-1]
            if tail.isdigit() and int(tail) > cutoff:
                candidates.append((int(tail), path))
        for cycle_ts, path in sorted(candidates):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            status = str(result.get("status") or "")
            if status and status not in {"ERROR", "BUSY"}:
                return {
                    "cycle": path.parent.name,
                    "status": status,
                    "result_ref": f"content-cycle:{path.parent.name}:result",
                }
        return None

    def _record_trial(
        self,
        problem: dict[str, Any],
        *,
        status: str,
        now: datetime,
        extra: dict[str, Any],
        update_problem: bool,
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id") or "unknown")
        proposal = dict(problem.get("repair_proposal") or {})
        payload = {
            "schema_version": "self-repair-trial.v1",
            "problem_id": problem_id,
            "proposal_id": proposal.get("proposal_id"),
            "status": status,
            "observed_at": now.astimezone(timezone.utc).isoformat(),
            **extra,
        }
        safe = problem_id.replace(":", "-")
        _atomic_json(self.root / "repair_trials" / f"{safe}.json", payload)
        if update_problem:
            path = Path(str(problem["_path"]))
            updated = {k: v for k, v in problem.items() if k != "_path"}
            updated["status"] = "VERIFIED" if status == "ADOPTED_VERIFIED" else status
            updated["repair_proposal"] = {
                **proposal,
                "status": "VERIFIED" if status == "ADOPTED_VERIFIED" else status,
            }
            updated["repair_trial"] = {
                "status": status,
                "observed_at": payload["observed_at"],
                "trial_ref": f"self-repair-trial:{safe}",
            }
            _atomic_json(path, updated)
        return payload

    @staticmethod
    def _git_sha(root: Path | None) -> str | None:
        if root is None:
            return None
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None


__all__ = [
    "RepairEdit",
    "RepairRecipe",
    "SelfRepairExecutor",
    "DEFAULT_RECIPES",
]
