"""Bounded RepairProposal execution and production verification."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

from .generated_repair import GeneratedRepairPlan, GeneratedRepairPlanner, PLANNER_VERSION
from .repair_request import RepairEvidenceRef, RepairRequest, submit_repair_request

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
_GENERATED_REPAIR_RESOURCE_RETRY_SECONDS = 900
_GENERATED_REPAIR_OUTPUT_RETRY_SECONDS = 300
_GENERATED_REPAIR_MAX_TRANSIENT_RETRIES = 3
_REJECTED_CANDIDATE_TRIALS = frozenset({
    "REJECTED_FOCUSED_TESTS_WORKSPACE_QUARANTINED",
    "REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
    "REPAIR_PAUSED_BASELINE_UNHEALTHY",
    "REPAIR_PAUSED_WORKSPACE_RESTORE_FAILED",
})


def _planner_retry_policy(value: object) -> tuple[str, int] | None:
    name = type(value).__name__
    message = str(value)
    if (
        name == "LLMProviderError"
        or "all eligible LLM resources failed" in message
        or "no eligible LLM resources" in message
    ):
        return "DEFERRED_RESOURCE", _GENERATED_REPAIR_RESOURCE_RETRY_SECONDS
    if (
        name == "ValidationError"
        or "repair planner did not return a JSON object" in message
        or "Invalid JSON:" in message
    ):
        return "DEFERRED_MODEL_OUTPUT", _GENERATED_REPAIR_OUTPUT_RETRY_SECONDS
    return None


def _transient_planner_reason(value: object) -> bool:
    text = str(value or "")
    return (
        text.startswith("LLMProviderError:")
        or text.startswith("ValidationError:")
        or "all eligible LLM resources failed" in text
        or "no eligible LLM resources" in text
        or "repair planner did not return a JSON object" in text
        or "Invalid JSON:" in text
    )


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
        capability_runtime: Any | None = None,
        test_pythonpath: str | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.deployed_root = Path(deployed_root).resolve() if deployed_root else None
        self.recipes = dict(recipes or DEFAULT_RECIPES)
        self.python_executable = str(python_executable or sys.executable)
        self.planner_runtime = planner_runtime
        self.capability_runtime = capability_runtime
        self.test_pythonpath = str(test_pythonpath or "").strip()
        if "\n" in self.test_pythonpath or "\0" in self.test_pythonpath:
            raise ValueError("self repair test_pythonpath must be a single-line path list")
        self.root = self.state_root / "application" / "self_maintenance"

    def run_once(self, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("self repair requires timezone-aware time")

        problem = self._next_problem(
            statuses={
                "REPAIR_PROPOSED",
                "REPAIR_PAUSED_BASELINE_UNHEALTHY",
                "REPAIR_PAUSED_WORKSPACE_RESTORE_FAILED",
            }
        )
        if problem is not None:
            return self._run_proposal(problem, now)

        diagnosing = self._next_diagnosing_problem(now=now)
        if diagnosing is not None:
            return self._generate_repair_proposal(diagnosing, now)

        observing = self._next_problem(statuses={"OBSERVING_PRODUCTION"})
        if observing is not None:
            return self._run_proposal(observing, now)
        return {"status": "NO_REPAIR_WORK"}

    def _run_proposal(self, problem: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Submit a LOW-risk request; code changes are exclusively owned by Pi Remote."""
        proposal = problem.get("repair_proposal") or {}
        if str(proposal.get("risk") or "").upper() != "LOW":
            return {
                "status": "NEEDS_HUMAN_REVIEW",
                "problem_id": problem.get("problem_id"),
                "repair_kind": proposal.get("repair_kind"),
            }
        problem_id = str(problem.get("problem_id") or "unknown")
        evidence = [
            RepairEvidenceRef(type="FerroEvidence", id=str(ref))
            for ref in (problem.get("evidence_refs") or [])[:32]
            if str(ref).strip()
        ]
        trial = problem.get("repair_trial")
        failed_tests = [
            str(item) for item in (trial.get("failed_tests") or [])
            if str(item).strip()
        ][:32] if isinstance(trial, dict) else []
        impact = str(problem.get("observed_world_impact") or "").strip()
        if not impact:
            impact = "Recorded failure prevented the affected cognition cycle from completing; downstream impact is not established."
        request = RepairRequest(
            idempotency_key=f"{problem_id}:{proposal.get('proposal_id') or 'proposal'}",
            source_problem_id=problem_id,
            symptom=str(problem.get("symptom") or problem.get("error_type") or "unknown failure"),
            failure_site=str(problem.get("failure_site") or "unknown"),
            evidence=evidence,
            failed_tests=failed_tests,
            severity=str(problem.get("severity") or "LOW").upper(),
            risk="LOW",
            observed_world_impact=impact,
            target_files=[str(item) for item in proposal.get("target_files") or []][:2],
        )
        receipt = submit_repair_request(self.capability_runtime, request)
        payload = request.to_pi_remote_payload()
        trial_record = self._record_trial(
            problem,
            status="REQUESTED_TO_PI_REMOTE" if receipt.get("status") in {"REQUESTED", "RECEIVED", "ACCEPTED"} else receipt.get("status", "REQUEST_FAILED"),
            now=now,
            extra={
                "repair_kind": proposal.get("repair_kind"),
                "request_sha256": sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                "evidence_refs": [item.model_dump(exclude_none=True) for item in evidence],
                "receipt": receipt,
                "authority": "REQUEST_CODE_REPAIR",
            },
            update_problem=True,
        )
        return {
            "status": trial_record.get("status"),
            "problem_id": problem_id,
            "repair_request": receipt,
            "trial_ref": f"self-repair-trial:{problem_id.replace(':', '-')}",
        }

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

    def _next_diagnosing_problem(self, *, now: datetime) -> dict[str, Any] | None:
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
            same_attempt = (
                int(attempt.get("occurrences_seen") or 0) == int(row.get("occurrences") or 0)
                and str(attempt.get("planner_version") or "") == PLANNER_VERSION
            )
            attempt_status = str(attempt.get("status") or "")
            if (
                attempt_status == "REJECTED"
                and same_attempt
                and not _transient_planner_reason(attempt.get("reason"))
            ):
                continue
            if attempt_status.startswith("DEFERRED_") and same_attempt:
                retry_after_raw = str(attempt.get("retry_after") or "")
                try:
                    retry_after = datetime.fromisoformat(retry_after_raw)
                except ValueError:
                    retry_after = now
                if retry_after.tzinfo is None:
                    retry_after = retry_after.replace(tzinfo=timezone.utc)
                if now.astimezone(timezone.utc) < retry_after.astimezone(timezone.utc):
                    continue
            sources = planner.candidate_source_paths(row)
            if not sources:
                continue
            row["_path"] = str(path)
            row["_planner_sources"] = sources
            rows.append(row)
        rows.sort(
            key=lambda row: (
                (
                    isinstance(row.get("repair_trial"), dict)
                    and row["repair_trial"].get("baseline_status") == "PASSED"
                    and row["repair_trial"].get("candidate_replay_status") == "FAILED"
                ),
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
            "planner_version": PLANNER_VERSION,
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
    def _recipe_from_plan(
        plan: GeneratedRepairPlan,
        materialized_edits: list[tuple[str, str, str]],
    ) -> RepairRecipe:
        return RepairRecipe(
            repair_kind=_GENERATED_REPAIR_KIND,
            target_files=tuple(plan.target_files),
            implementation_markers=tuple(
                (path, new_text)
                for path, _old_text, new_text in materialized_edits
            ),
            edits=tuple(
                RepairEdit(path, old_text, new_text)
                for path, old_text, new_text in materialized_edits
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
            plan, source_paths, test_paths, editable_blocks = asyncio.run(planner.plan(problem))
        except Exception as exc:
            reason = type(exc).__name__ + ":" + str(exc)[:500]
            retry_policy = _planner_retry_policy(exc)
            if retry_policy is not None:
                retry_status, retry_seconds = retry_policy
                previous = (
                    problem.get("planner_attempt")
                    if isinstance(problem.get("planner_attempt"), dict)
                    else {}
                )
                same_previous = (
                    int(previous.get("occurrences_seen") or 0)
                    == int(problem.get("occurrences") or 0)
                    and str(previous.get("planner_version") or "") == PLANNER_VERSION
                    and str(previous.get("status") or "").startswith("DEFERRED_")
                )
                retry_count = (
                    int(previous.get("retry_count") or 0) + 1
                    if same_previous
                    else 1
                )
                if retry_count > _GENERATED_REPAIR_MAX_TRANSIENT_RETRIES:
                    attempt = self._planner_attempt(
                        problem,
                        now=now,
                        status="REJECTED",
                        details={
                            "reason": "transient planner retry budget exhausted: " + reason,
                            "retry_count": retry_count,
                        },
                    )
                    return {
                        **attempt,
                        "status": "GENERATED_PLAN_RETRY_EXHAUSTED",
                        "problem_id": problem.get("problem_id"),
                        "planner_status": attempt.get("status"),
                    }
                retry_after = now.astimezone(timezone.utc) + timedelta(
                    seconds=retry_seconds
                )
                attempt = self._planner_attempt(
                    problem,
                    now=now,
                    status=retry_status,
                    details={
                        "reason": reason,
                        "retry_after": retry_after.isoformat(),
                        "retry_count": retry_count,
                    },
                )
                return {
                    **attempt,
                    "status": "GENERATED_PLAN_DEFERRED",
                    "problem_id": problem.get("problem_id"),
                    "planner_status": attempt.get("status"),
                    "retry_seconds": retry_seconds,
                }
            attempt = self._planner_attempt(
                problem,
                now=now,
                status="REJECTED",
                details={"reason": reason},
            )
            return {
                **attempt,
                "status": "GENERATED_PLAN_REJECTED",
                "problem_id": problem.get("problem_id"),
                "planner_status": attempt.get("status"),
            }

        recipe = self._recipe_from_plan(
            plan,
            planner.materialize_edits(plan, editable_blocks),
        )
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
                "server-generated block_id still resolves to exactly one authoritative source block",
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
            "planner_version": PLANNER_VERSION,
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
                "planner_version": PLANNER_VERSION,
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
            editable_blocks = planner.editable_blocks(problem, sources)
            validated = planner.validate(
                plan,
                allowed_sources=sources,
                allowed_tests=tests,
                editable_blocks=editable_blocks,
            )
            return self._recipe_from_plan(
                validated,
                planner.materialize_edits(validated, editable_blocks),
            )
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

    def _test_command(self, *pytest_args: str) -> tuple[str, ...]:
        test_home = self.root / "test_home"
        test_home.mkdir(parents=True, exist_ok=True)
        command = (
            self.python_executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            *pytest_args,
        )
        environment = (
            "env",
            "-i",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME=" + str(test_home),
            "LANG=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
        )
        if self.test_pythonpath:
            environment = (
                *environment,
                "PYTHONPATH=" + self.test_pythonpath,
            )
        return (*environment, *command)

    def _provider(self, recipe: RepairRecipe) -> BoundedCodingProvider:
        assert self.workspace_root is not None
        focused = (self._test_command(*recipe.focused_tests),)
        full = (self._test_command(),)
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

    def _existing_rejected_candidate_trial(
        self, problem: dict[str, Any]
    ) -> dict[str, Any] | None:
        problem_id = str(problem.get("problem_id") or "unknown")
        safe = problem_id.replace(":", "-")
        path = self.root / "repair_trials" / f"{safe}.json"
        try:
            trial = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(trial, dict):
            return None
        if str(trial.get("status") or "") not in _REJECTED_CANDIDATE_TRIALS:
            return None
        proposal = problem.get("repair_proposal") or {}
        if str(trial.get("proposal_id") or "") != str(proposal.get("proposal_id") or ""):
            return None
        return trial

    @staticmethod
    def _test_counterevidence(test_result: object) -> dict[str, Any]:
        if not isinstance(test_result, dict):
            return {"failed_tests": [], "assertion_evidence": []}
        failed: list[str] = []
        evidence: list[str] = []
        for result in test_result.get("results") or ():
            if not isinstance(result, dict):
                continue
            text = str(result.get("stdout") or "") + "\n" + str(result.get("stderr") or "")
            for raw in text.splitlines():
                line = raw.strip()
                if raw.startswith("FAILED "):
                    test_id = raw[len("FAILED "):].split(" - ", 1)[0].strip()
                    if test_id and test_id not in failed:
                        failed.append(test_id)
                if (
                    raw.startswith("E   ")
                    or raw.startswith("E       ")
                    or (line.startswith("assert ") and len(line) < 500)
                ):
                    if line and line not in evidence:
                        evidence.append(line[:500])
                if len(failed) >= 12 and len(evidence) >= 24:
                    break
        return {
            "failed_tests": failed[:12],
            "assertion_evidence": evidence[:24],
        }

    def _restore_workspace_baseline(self, baseline_sha: str) -> dict[str, Any]:
        if self.workspace_root is None or not baseline_sha:
            return {"ok": False, "reason": "workspace or baseline sha unavailable"}
        commands = (
            ("git", "restore", "--staged", "--worktree", "."),
            ("git", "switch", "--detach", baseline_sha),
        )
        for command in commands:
            result = subprocess.run(
                command,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "reason": (result.stderr or result.stdout or "git restore failed")[:1000],
                }
        return {
            "ok": True,
            "baseline_sha": baseline_sha,
            "workspace_sha": self._git_sha(self.workspace_root),
        }

    def _requeue_failed_candidate(
        self,
        problem: dict[str, Any],
        recipe: RepairRecipe,
        trial: dict[str, Any],
        *,
        now: datetime,
        baseline_sha: str | None = None,
    ) -> dict[str, Any]:
        baseline_sha = baseline_sha or self._git_sha(self.workspace_root)
        problem_id = str(problem.get("problem_id") or "unknown")
        safe = problem_id.replace(":", "-")
        proposal = dict(problem.get("repair_proposal") or {})

        restore_before = self._restore_workspace_baseline(str(baseline_sha or ""))
        baseline_full: dict[str, Any] = {
            "ok": False,
            "profile": "repair-full",
            "results": [],
            "reason": "workspace baseline restore failed",
        }
        candidate_replay: dict[str, Any] = {
            "ok": False,
            "profile": "repair-full",
            "results": [],
            "reason": "candidate replay not attempted",
        }
        replay_error: str | None = None

        if restore_before.get("ok"):
            baseline_full = self._provider(recipe).invoke(
                CAP_CODE_TEST, {"profile": "repair-full"}
            )

        if baseline_full.get("ok"):
            replay_provider = self._provider(recipe)
            try:
                for edit in recipe.edits:
                    replay_provider.invoke(
                        CAP_CODE_EDIT,
                        {
                            "path": edit.path,
                            "old_text": edit.old_text,
                            "new_text": edit.new_text,
                            "expected_replacements": 1,
                        },
                    )
                candidate_replay = replay_provider.invoke(
                    CAP_CODE_TEST, {"profile": "repair-full"}
                )
            except Exception as exc:  # bounded replay failure is evidence, not authority
                replay_error = type(exc).__name__ + ":" + str(exc)[:1000]
                candidate_replay = {
                    "ok": False,
                    "profile": "repair-full",
                    "results": [],
                    "reason": "candidate replay could not be reproduced",
                    "replay_error": replay_error,
                }

        restore_after = self._restore_workspace_baseline(str(baseline_sha or ""))
        counterevidence = self._test_counterevidence(candidate_replay)
        if replay_error and not counterevidence.get("assertion_evidence"):
            counterevidence["assertion_evidence"] = [replay_error]

        test_environment = {
            "python_executable": self.python_executable,
            "pythonpath": self.test_pythonpath or None,
        }
        augmented_trial = {
            **trial,
            "baseline": baseline_full,
            "baseline_sha": baseline_sha,
            "candidate_replay": candidate_replay,
            "counterevidence": counterevidence,
            "test_environment": test_environment,
            "workspace_restore_before": restore_before,
            "workspace_restore_after": restore_after,
        }

        path = Path(str(problem["_path"]))
        updated = {
            k: v for k, v in problem.items()
            if not str(k).startswith("_")
        }
        trial_summary = {
            "status": str(trial.get("status") or "REJECTED"),
            "observed_at": str(
                trial.get("observed_at")
                or now.astimezone(timezone.utc).isoformat()
            ),
            "trial_ref": f"self-repair-trial:{safe}",
            "baseline_status": "PASSED" if baseline_full.get("ok") else "FAILED",
            "candidate_replay_status": (
                "PASSED" if candidate_replay.get("ok") else "FAILED"
            ),
            "previous_proposal_id": proposal.get("proposal_id"),
            "previous_hypothesis": proposal.get("hypothesis"),
            "previous_edits": [
                {
                    "path": row.get("path"),
                    "block_id": row.get("block_id"),
                    "new_text": str(row.get("new_text") or "")[:1800],
                }
                for row in (
                    (proposal.get("generated_recipe") or {}).get("edits") or ()
                )
                if isinstance(row, dict)
            ][:3],
            "test_environment": test_environment,
            **counterevidence,
        }
        updated["repair_trial"] = trial_summary

        if not restore_after.get("ok"):
            augmented_trial["status"] = "REPAIR_PAUSED_WORKSPACE_RESTORE_FAILED"
            _atomic_json(
                self.root / "repair_trials" / f"{safe}.json",
                augmented_trial,
            )
            updated["status"] = "REPAIR_PAUSED_WORKSPACE_RESTORE_FAILED"
            updated["repair_proposal"] = {
                **proposal,
                "status": "PAUSED_WORKSPACE_RESTORE_FAILED",
            }
            _atomic_json(path, updated)
            return {
                "status": "REPAIR_PAUSED_WORKSPACE_RESTORE_FAILED",
                "problem_id": problem_id,
                "workspace_restore": restore_after,
            }

        if not baseline_full.get("ok"):
            augmented_trial["status"] = "REPAIR_PAUSED_BASELINE_UNHEALTHY"
            _atomic_json(
                self.root / "repair_trials" / f"{safe}.json",
                augmented_trial,
            )
            updated["status"] = "REPAIR_PAUSED_BASELINE_UNHEALTHY"
            updated["repair_proposal"] = {
                **proposal,
                "status": "PAUSED_BASELINE_UNHEALTHY",
            }
            _atomic_json(path, updated)
            return {
                "status": "REPAIR_PAUSED_BASELINE_UNHEALTHY",
                "problem_id": problem_id,
                "baseline": baseline_full,
                "trial_status": trial.get("status"),
            }

        if candidate_replay.get("ok"):
            augmented_trial["status"] = "REPLAY_PASSED_ENVIRONMENT_CORRECTED"
            _atomic_json(
                self.root / "repair_trials" / f"{safe}.json",
                augmented_trial,
            )
            updated["status"] = "REPAIR_PROPOSED"
            updated["repair_proposal"] = {
                **proposal,
                "status": "PROPOSED",
            }
            updated["repair_trial"] = {
                **trial_summary,
                "status": "REPLAY_PASSED_ENVIRONMENT_CORRECTED",
                "failed_tests": [],
                "assertion_evidence": [],
            }
            _atomic_json(path, updated)
            return {
                "status": "CANDIDATE_REPLAY_PASSED_RETRY_QUEUED",
                "problem_id": problem_id,
                "baseline_status": "PASSED",
                "candidate_replay_status": "PASSED",
                "retry_seconds": 60,
            }

        augmented_trial["status"] = "REJECTED_BY_AUTHORITATIVE_REPLAY"
        _atomic_json(
            self.root / "repair_trials" / f"{safe}.json",
            augmented_trial,
        )
        updated["status"] = "DIAGNOSING"
        updated["repair_proposal"] = None
        updated["planner_attempt"] = {
            "status": "COUNTEREVIDENCE_READY",
            "planner_version": PLANNER_VERSION,
            "observed_at": now.astimezone(timezone.utc).isoformat(),
            "occurrences_seen": int(problem.get("occurrences") or 0),
            "previous_proposal_id": proposal.get("proposal_id"),
            "failed_tests": counterevidence.get("failed_tests"),
        }
        _atomic_json(path, updated)
        return {
            "status": "CANDIDATE_REJECTED_REPLAN_QUEUED",
            "problem_id": problem_id,
            "trial_status": trial.get("status"),
            "baseline_status": "PASSED",
            "candidate_replay_status": "FAILED",
            "counterevidence": counterevidence,
            "retry_seconds": 60,
        }

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

        baseline_sha = self._git_sha(self.workspace_root)
        problem_id = str(problem.get("problem_id") or "self-problem")
        proposal = problem.get("repair_proposal")
        proposal_id = (
            str(proposal.get("proposal_id") or "")
            if isinstance(proposal, dict)
            else ""
        )
        lineage = sha256(
            (problem_id + "\0" + proposal_id + "\0" + now.isoformat()).encode("utf-8")
        ).hexdigest()[:16]
        branch_task_id = (
            problem_id.replace(":", "-")[:40].rstrip("-._")
            + "-"
            + lineage
        )
        provider.invoke(
            CAP_CODE_BRANCH,
            {"task_id": branch_task_id},
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
            trial = self._record_trial(
                problem,
                status="REJECTED_FOCUSED_TESTS_WORKSPACE_QUARANTINED",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "test": focused},
                update_problem=False,
            )
            return self._requeue_failed_candidate(
                problem,
                recipe,
                trial,
                now=now,
                baseline_sha=baseline_sha,
            )
        full = provider.invoke(CAP_CODE_TEST, {"profile": "repair-full"})
        if not bool(full.get("ok")):
            trial = self._record_trial(
                problem,
                status="REJECTED_FULL_TESTS_WORKSPACE_QUARANTINED",
                now=now,
                extra={"repair_kind": recipe.repair_kind, "focused": focused, "full": full},
                update_problem=False,
            )
            return self._requeue_failed_candidate(
                problem,
                recipe,
                trial,
                now=now,
                baseline_sha=baseline_sha,
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
