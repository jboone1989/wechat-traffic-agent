"""Local, request-only adapter to Pi Remote's RepairRequest CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .repair_request import RepairRequest


class PiRemoteRepairProvider:
    provider_id = "pi_remote_repair"

    def __init__(self, *, root: Path, node: str = "node", timeout_seconds: int = 10) -> None:
        self.root = Path(root).resolve()
        self.node = str(node)
        self.timeout_seconds = min(30, max(1, int(timeout_seconds)))
        self.cli = self.root / "cli.js"
        if not self.cli.is_file():
            raise ValueError("Pi Remote CLI is unavailable at the configured root")

    def manifest(self) -> Mapping[str, Any]:
        return {
            "provider": self.provider_id,
            "capabilities": [{
                "capability_id": "REQUEST_CODE_REPAIR",
                "description": "Submit an evidence-bounded Ferro repair request to Pi Remote.",
                "side_effect_level": "LOCAL_STATE",
                "required_permissions": ["repair.request"],
            }],
        }

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if capability_id != "REQUEST_CODE_REPAIR":
            raise LookupError("unsupported Pi Remote capability")
        body = dict(payload or {})
        request = RepairRequest(
            idempotency_key=str(body.get("idempotencyKey") or ""),
            source_problem_id=str(body.get("sourceProblemId") or ""),
            symptom=str(body.get("symptom") or ""),
            failure_site=str(body.get("failureSite") or "unknown"),
            evidence=body.get("evidence") or [],
            failed_tests=body.get("failedTests") or [],
            severity=str(body.get("severity") or ""),
            risk=str(body.get("risk") or ""),
            observed_world_impact=str(body.get("observedWorldImpact") or ""),
            target_files=body.get("targetFiles") or [],
        )
        if body.get("schema") != "pi-remote.repair-request.v1" or body.get("schemaVersion") != 1:
            raise ValueError("unsupported RepairRequest protocol version")
        request_payload = request.to_pi_remote_payload()
        completed = subprocess.run(
            [
                self.node,
                str(self.cli),
                "repair-request",
                json.dumps({"request": request_payload}, sort_keys=True, separators=(",", ":")),
            ],
            cwd=self.root,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env={**os.environ, "PI_REMOTE_REPAIR_REQUEST_ONLY": "1"},
        )
        if completed.returncode != 0:
            raise RuntimeError("Pi Remote rejected RepairRequest")
        try:
            response = json.loads(completed.stdout)
            accepted = response.get("request") or {}
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("Pi Remote returned an invalid RepairRequest receipt") from exc
        request_id = accepted.get("id")
        if not isinstance(request_id, str) or accepted.get("schema") != "pi-remote.repair-request.v1":
            raise RuntimeError("Pi Remote returned an incomplete RepairRequest receipt")
        return {
            "status": accepted.get("status", "RECEIVED"),
            "requestId": request_id,
            "created": bool(response.get("created")),
        }


def configured_pi_remote_repair_provider() -> PiRemoteRepairProvider | None:
    root = os.environ.get("PI_REMOTE_REPAIR_ROOT", "").strip()
    if not root:
        return None
    node = os.environ.get("PI_REMOTE_NODE", "node").strip() or "node"
    return PiRemoteRepairProvider(root=Path(root), node=node)


__all__ = ["PiRemoteRepairProvider", "configured_pi_remote_repair_provider"]
