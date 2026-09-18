"""Evidence-first provider diagnostics backed by Agent Runtime diagnostic memory."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from agent_runtime.agency.diagnosis import (
    DiagnosticCase,
    DiagnosticMemory,
    ObservationKind,
)
from agent_runtime.resource_discovery import ModelTrial


_AUTH_CODES = {"HTTP_401", "HTTP_403"}
_MODEL_TARGET_CODES = {"HTTP_404", "HTTP_410"}
_RATE_CODES = {"HTTP_429"}


def diagnose_provider_trials(
    *,
    state_root: Path,
    provider_label: str,
    credential_env: str,
    lead_id: str,
    rows: Iterable[ModelTrial],
    now: datetime,
    quality_floor: float = 0.75,
) -> dict[str, Any]:
    history = list(rows)
    if not history:
        return {
            "status": "NO_TRIAL_EVIDENCE",
            "lead_id": lead_id,
            "provider_label": provider_label,
            "credential_env": credential_env,
        }

    tags = (
        "external-api",
        "model-provider",
        "resource-acquisition",
        "scope-overgeneralization",
    )
    memory = DiagnosticMemory(Path(state_root) / "application")
    recalled = memory.lessons_for(tags, limit=3)
    case = DiagnosticCase.start(
        symptom=f"{provider_label} model trial history contains failures or weak results",
        scope_ladder=("request", "model", "provider"),
        tags=tags,
        now=now,
    )

    success_rows = [row for row in history if row.status == "COMPLETED"]
    failed_rows = [row for row in history if row.status == "FAILED"]
    observations = []
    for row in history:
        scope = "model" if row.model_id else "request"
        refs = tuple(dict.fromkeys((row.trial_id, *row.evidence_refs)))
        if row.status == "COMPLETED":
            statement = (
                f"model {row.model_id or '<unknown>'} completed a request"
                + (
                    f" with quality={float(row.quality_score):.3f}"
                    if row.quality_score is not None else ""
                )
            )
            kind = ObservationKind.SUCCESS
        else:
            code = row.error_code or "UNKNOWN_ERROR"
            statement = f"model {row.model_id or '<unknown>'} failed with {code}"
            kind = ObservationKind.FAILURE
        observations.append(
            case.observe(
                statement=statement,
                scope=scope,
                kind=kind,
                evidence_refs=refs,
                now=now,
            )
        )

    provider_success = None
    if success_rows:
        provider_success = case.observe(
            statement="at least one authenticated model request completed, proving provider reachability",
            scope="provider",
            kind=ObservationKind.COUNTEREXAMPLE,
            evidence_refs=tuple(row.trial_id for row in success_rows),
            now=now,
        )

    by_model: dict[str, list[ModelTrial]] = defaultdict(list)
    for row in history:
        by_model[row.model_id or "<unknown>"].append(row)

    incompatible_models: list[str] = []
    rate_limited_models: list[str] = []
    low_quality_models: list[str] = []
    request_failure_models: list[str] = []
    scoped_findings = []

    for model_id, model_rows in sorted(by_model.items()):
        failed = [row for row in model_rows if row.status == "FAILED"]
        completed = [row for row in model_rows if row.status == "COMPLETED"]
        codes = {row.error_code for row in failed if row.error_code}
        if failed and len(failed) == len(model_rows) and codes and codes <= _MODEL_TARGET_CODES:
            incompatible_models.append(model_id)
            scoped_findings.append(
                case.observe(
                    statement=f"model target {model_id} is incompatible or unavailable under the current protocol",
                    scope="model",
                    kind=ObservationKind.FAILURE,
                    evidence_refs=tuple(row.trial_id for row in failed),
                    now=now,
                )
            )
        if failed and len(failed) == len(model_rows) and codes == _RATE_CODES:
            rate_limited_models.append(model_id)
            scoped_findings.append(
                case.observe(
                    statement=f"model target {model_id} is temporarily rate limited",
                    scope="model",
                    kind=ObservationKind.FAILURE,
                    evidence_refs=tuple(row.trial_id for row in failed),
                    now=now,
                )
            )
        quality = [
            float(row.quality_score)
            for row in completed
            if row.quality_score is not None
        ]
        if completed and quality and sum(quality) / len(quality) < float(quality_floor):
            low_quality_models.append(model_id)
            scoped_findings.append(
                case.observe(
                    statement=f"model target {model_id} is callable but below the current task quality floor",
                    scope="model",
                    kind=ObservationKind.FAILURE,
                    evidence_refs=tuple(row.trial_id for row in completed),
                    now=now,
                )
            )
        if any((row.error_code or "") == "REQUEST_FAILED" for row in failed):
            request_failure_models.append(model_id)

    provider_unavailable = case.hypothesize(
        statement="the provider API is unavailable as a whole",
        scope="provider",
        predicts="provider-level probes fail independently of a particular model target",
        falsifier="any authenticated compatible model request completes successfully",
    )
    auth_rejected = case.hypothesize(
        statement="the credential is rejected by the provider",
        scope="provider",
        predicts="authenticated requests consistently return 401 or 403 before model execution",
        falsifier="any authenticated request completes successfully",
    )
    model_scoped = case.hypothesize(
        statement="the observed failures are model/request scoped rather than provider scoped",
        scope="model",
        predicts="the provider remains reachable while individual targets can fail, rate-limit, or miss the task quality floor",
        falsifier="a provider-scope probe fails independently of model choice",
    )

    if provider_success is not None:
        case.refute(provider_unavailable.hypothesis_id, (provider_success.observation_id,))
        case.refute(auth_rejected.hypothesis_id, (provider_success.observation_id,))

    auth_failed = bool(failed_rows) and not success_rows and all(
        (row.error_code or "") in _AUTH_CODES for row in failed_rows
    )
    if auth_failed:
        auth_observation = case.observe(
            statement="all observed authenticated requests were rejected with 401/403",
            scope="provider",
            kind=ObservationKind.FAILURE,
            evidence_refs=tuple(row.trial_id for row in failed_rows),
            now=now,
        )
        case.support(auth_rejected.hypothesis_id, (auth_observation.observation_id,))
        conclusion = case.conclude(
            auth_rejected.hypothesis_id,
            summary="credential authentication is rejected; provider reachability is not the same as credential usability",
            now=now,
        )
        classification = "AUTHENTICATION_REJECTED"
        availability_status = "REACHABLE_BUT_AUTH_REJECTED"
        next_probe = "verify credential scope/permissions with one provider-level authentication probe"
    elif failed_rows and provider_success is not None:
        support_ids = [provider_success.observation_id]
        support_ids.extend(item.observation_id for item in scoped_findings)
        if len(support_ids) == 1:
            support_ids.extend(
                item.observation_id
                for item in observations
                if item.kind == ObservationKind.FAILURE
            )
        case.support(model_scoped.hypothesis_id, support_ids)
        conclusion = case.conclude(
            model_scoped.hypothesis_id,
            summary="provider is reachable; observed failures are localized below provider scope",
            now=now,
        )
        classification = "MODEL_OR_REQUEST_LEVEL_ISSUES_PROVIDER_REACHABLE"
        availability_status = "REACHABLE"
        if incompatible_models:
            next_probe = "refresh the official model catalogue and test one current compatible model with a minimal completion"
        elif rate_limited_models:
            next_probe = "back off and retry through a different free target/router instead of invalidating the provider"
        elif low_quality_models:
            next_probe = "run a task-representative benchmark on another callable model; keep availability separate from quality"
        else:
            next_probe = "run the cheapest provider-level discriminating probe before widening the failure claim"
    elif incompatible_models:
        case.support(
            model_scoped.hypothesis_id,
            tuple(item.observation_id for item in scoped_findings),
        )
        conclusion = case.conclude(
            model_scoped.hypothesis_id,
            summary="model target/protocol incompatibility observed; provider-wide unavailability remains unproven",
            now=now,
        )
        classification = "MODEL_TARGET_INCOMPATIBILITY"
        availability_status = "UNVERIFIED"
        next_probe = "refresh the official model catalogue and issue one minimal completion to a current target"
    elif rate_limited_models:
        case.support(
            model_scoped.hypothesis_id,
            tuple(item.observation_id for item in scoped_findings),
        )
        conclusion = case.conclude(
            model_scoped.hypothesis_id,
            summary="rate limiting is observed at model/request scope; provider-wide unavailability remains unproven",
            now=now,
        )
        classification = "TEMPORARY_RATE_LIMIT"
        availability_status = "UNVERIFIED"
        next_probe = "retry after backoff or use another current free target"
    elif low_quality_models:
        case.support(
            model_scoped.hypothesis_id,
            tuple(item.observation_id for item in scoped_findings),
        )
        conclusion = case.conclude(
            model_scoped.hypothesis_id,
            summary="model is callable but unsuitable for the current benchmark; availability remains distinct from quality",
            now=now,
        )
        classification = "MODEL_TASK_MISMATCH"
        availability_status = "REACHABLE"
        next_probe = "evaluate another callable model with a task-representative benchmark"
    else:
        conclusion = None
        classification = "NEEDS_DISCRIMINATING_PROBE"
        availability_status = "UNVERIFIED"
        next_probe = "capture the exact error and run the cheapest probe that separates credential, transport, protocol, model, and task-quality causes"
        case.plan_probe(
            description=next_probe,
            target_hypothesis_ids=(
                provider_unavailable.hypothesis_id,
                auth_rejected.hypothesis_id,
                model_scoped.hypothesis_id,
            ),
            target_scope="provider",
            expected_if_true="provider-level evidence identifies a broader outage/authentication cause",
            expected_if_false="successful provider-level evidence keeps the failure localized",
        )

    lesson_id = None
    if conclusion is not None and provider_success is not None and failed_rows:
        case.record_remediation(
            "keep the provider eligible; remediate model targets, rate limits, and task suitability independently"
        )
        case.verify(tuple(row.trial_id for row in success_rows))
        lesson = case.to_lesson(
            principle=(
                "Do not generalize request/model failure into provider failure without provider-scope evidence; "
                "a successful authenticated request is a counterexample to global unavailability."
            ),
            anti_patterns=(
                "treating HTTP 404/410 on a model as proof that the provider API is unusable",
                "treating HTTP 429 as proof that the credential or provider is permanently broken",
                "treating a low benchmark score as proof that the API cannot be used",
            ),
            now=now,
        )
        memory.remember(lesson)
        lesson_id = lesson.lesson_id

    memory.persist_case(case)
    return {
        "status": "DIAGNOSED" if conclusion is not None else "PROBE_REQUIRED",
        "case_id": case.case_id,
        "lesson_id": lesson_id,
        "lead_id": lead_id,
        "provider_label": provider_label,
        "credential_env": credential_env,
        "availability_status": availability_status,
        "classification": classification,
        "failure_scope": None if conclusion is None else conclusion.scope,
        "conclusion": None if conclusion is None else conclusion.summary,
        "provider_unavailable_hypothesis": provider_unavailable.status.value,
        "specific_findings": {
            "incompatible_models": incompatible_models,
            "rate_limited_models": rate_limited_models,
            "low_quality_models": low_quality_models,
            "request_failure_models": request_failure_models,
        },
        "next_probe": next_probe,
        "recalled_lesson_ids": [item.lesson_id for item in recalled],
    }


__all__ = ["diagnose_provider_trials"]
