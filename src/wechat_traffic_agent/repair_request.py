"""Stable, credential-free interface for Ferro to request supervised code repair."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RepairEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=128)
    id: str = Field(min_length=1, max_length=512)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class RepairRequest(BaseModel):
    """Ferro may submit evidence and request repair; it carries no editing authority."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["pi-remote.repair-request.v1"] = "pi-remote.repair-request.v1"
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    source_problem_id: str = Field(min_length=1, max_length=256)
    symptom: str = Field(min_length=1, max_length=2_000)
    failure_site: str = Field(min_length=1, max_length=1_000)
    evidence: list[RepairEvidenceRef] = Field(default_factory=list, max_length=32)
    failed_tests: list[str] = Field(default_factory=list, max_length=32)
    evaluation_tests: list[str] = Field(default_factory=list, max_length=32)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    observed_world_impact: str = Field(min_length=1, max_length=2_000)
    target_files: list[str] = Field(default_factory=list, max_length=2)

    def to_pi_remote_payload(self) -> dict[str, Any]:
        return {
            "schema": self.protocol,
            "schemaVersion": 1,
            "idempotencyKey": self.idempotency_key,
            "sourceProblemId": self.source_problem_id,
            "symptom": self.symptom,
            "failureSite": self.failure_site,
            "evidence": [item.model_dump(exclude_none=True) for item in self.evidence],
            "failedTests": self.failed_tests,
            "evaluationTests": self.evaluation_tests,
            "severity": self.severity,
            "risk": self.risk,
            "observedWorldImpact": self.observed_world_impact,
            "targetFiles": self.target_files,
        }


def submit_repair_request(capability_runtime: Any, request: RepairRequest) -> dict[str, Any]:
    """Invoke exactly one capability; never exposes shell, Git, edit, merge, or deploy tools."""
    if capability_runtime is None:
        return {"status": "CAPABILITY_UNAVAILABLE"}
    available = getattr(capability_runtime, "available_capabilities", None)
    if callable(available):
        try:
            if "REQUEST_CODE_REPAIR" not in set(available()):
                return {"status": "CAPABILITY_UNAVAILABLE"}
        except Exception:
            return {"status": "CAPABILITY_UNAVAILABLE"}
    invoke = getattr(capability_runtime, "invoke", None)
    if not callable(invoke):
        return {"status": "CAPABILITY_UNAVAILABLE"}
    try:
        receipt = invoke("REQUEST_CODE_REPAIR", request.to_pi_remote_payload())
    except Exception as exc:
        return {"status": "REQUEST_FAILED", "error_type": type(exc).__name__}
    # ApplicationRuntime wraps provider output in CapabilityInvocationResult.
    body = getattr(receipt, "payload", receipt)
    if not isinstance(body, dict):
        return {"status": "REQUEST_FAILED", "error_type": "InvalidCapabilityReceipt"}
    # Persist only stable, non-secret receipt fields. Provider output and raw payloads are excluded.
    result = {"status": str(body.get("status") or "REQUESTED")}
    for key in ("request_id", "requestId", "job_id", "jobId", "state", "created"):
        value = body.get(key)
        if isinstance(value, (str, int, bool)):
            result[key] = value
    receipt_id = getattr(receipt, "receipt_id", None)
    if isinstance(receipt_id, str):
        result["capability_receipt_id"] = receipt_id
    return result



def query_repair_status(capability_runtime: Any, request_id: str) -> dict[str, Any]:
    """Read the durable Pi Remote/RRSI receipt for one submitted repair."""
    request_id = str(request_id or "").strip()
    if not request_id:
        raise ValueError("repair status query requires request_id")
    if capability_runtime is None:
        return {"status": "CAPABILITY_UNAVAILABLE", "requestId": request_id}
    available = getattr(capability_runtime, "available_capabilities", None)
    if callable(available) and "QUERY_CODE_REPAIR_STATUS" not in set(available()):
        return {"status": "CAPABILITY_UNAVAILABLE", "requestId": request_id}
    invoke = getattr(capability_runtime, "invoke", None)
    if not callable(invoke):
        return {"status": "CAPABILITY_UNAVAILABLE", "requestId": request_id}
    try:
        receipt = invoke("QUERY_CODE_REPAIR_STATUS", {"requestId": request_id})
    except Exception as exc:
        return {
            "status": "STATUS_QUERY_FAILED",
            "requestId": request_id,
            "error_type": type(exc).__name__,
        }
    body = getattr(receipt, "payload", receipt)
    if not isinstance(body, dict):
        return {
            "status": "STATUS_QUERY_FAILED",
            "requestId": request_id,
            "error_type": "InvalidCapabilityReceipt",
        }
    result = {
        "status": str(body.get("status") or "UNKNOWN"),
        "requestId": str(body.get("requestId") or request_id),
        "rsiStatus": str(body.get("rsiStatus") or "NOT_STARTED"),
        "rsiRound": int(body.get("rsiRound") or 0),
    }
    for key in (
        "selectedChildRequestId",
        "selectedCheckpointId",
        "selectedGitHead",
        "recentRejectedHypotheses",
        "rsiAutoResumed",
        "lastError",
    ):
        value = body.get(key)
        if value is not None:
            result[key] = value
    return result


__all__ = [
    "RepairEvidenceRef",
    "RepairRequest",
    "submit_repair_request",
    "query_repair_status",
]
