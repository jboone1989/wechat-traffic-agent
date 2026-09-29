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
        self.request_cli = self.root / "repair-request-cli.js"
        if not self.cli.is_file():
            raise ValueError("Pi Remote CLI is unavailable at the configured root")

    def manifest(self) -> Mapping[str, Any]:
        return {
            "provider": self.provider_id,
            "capabilities": [
                {
                    "capability_id": "REQUEST_CODE_REPAIR",
                    "description": "Submit an evidence-bounded Ferro repair request to Pi Remote.",
                    "side_effect_level": "LOCAL_STATE",
                    "required_permissions": ["repair.request"],
                },
                {
                    "capability_id": "QUERY_CODE_REPAIR_STATUS",
                    "description": "Read durable Pi Remote/RRSI status for a previously submitted repair request.",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": ["repair.request"],
                },
            ],
        }

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if capability_id not in {"REQUEST_CODE_REPAIR", "QUERY_CODE_REPAIR_STATUS"}:
            raise LookupError("unsupported Pi Remote capability")
        body = dict(payload or {})
        request = None
        if capability_id == "REQUEST_CODE_REPAIR":
            request = RepairRequest(
            idempotency_key=str(body.get("idempotencyKey") or ""),
            source_problem_id=str(body.get("sourceProblemId") or ""),
            symptom=str(body.get("symptom") or ""),
            failure_site=str(body.get("failureSite") or "unknown"),
            evidence=body.get("evidence") or [],
            failed_tests=body.get("failedTests") or [],
            evaluation_tests=body.get("evaluationTests") or [],
            severity=str(body.get("severity") or ""),
            risk=str(body.get("risk") or ""),
            observed_world_impact=str(body.get("observedWorldImpact") or ""),
            target_files=body.get("targetFiles") or [],
        )
        if capability_id == "REQUEST_CODE_REPAIR":
            if body.get("schema") != "pi-remote.repair-request.v1" or body.get("schemaVersion") != 1:
                raise ValueError("unsupported RepairRequest protocol version")
            request_payload = request.to_pi_remote_payload()
        else:
            request_id = str(body.get("requestId") or body.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("repair status query requires requestId")
            request_payload = {"action": "status", "requestId": request_id}
        child_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "USER": os.environ.get("USER", ""),
            "PI_REMOTE_REPAIR_REQUEST_ONLY": "1",
        }
        for name in (
            "PI_REMOTE_JOB_ROOT",
            "PI_REMOTE_REPAIR_REPO_ROOT",
            "PI_REMOTE_REPAIR_PYTHON",
            "PI_REMOTE_REPAIR_WORKTREE_ROOT",
            "PI_REMOTE_REPAIR_SESSIONS_DIR",
            "PI_REMOTE_REPAIR_RSI_AUTO",
            "PI_REMOTE_REPAIR_RSI_K",
            "PI_REMOTE_REPAIR_RSI_TIMEOUT_SECONDS",
            "PI_REMOTE_REPAIR_RSI_DELTA",
            "PI_REMOTE_REPAIR_RSI_BETA_COST",
            "PI_REMOTE_REPAIR_RSI_MAX_ROUNDS",
            "PI_REMOTE_REPAIR_RSI_RESUME_AFTER_MS",
            "PI_REMOTE_CHATGPT_PYTHON",
            "PI_REMOTE_CHATGPT_COGNITION_CLI",
            "PI_REMOTE_CHATGPT_TIMEOUT_SECONDS",
            "PI_REMOTE_CHATGPT_AGENT_ID",
            "CONTENT_SELF_REPAIR_WORKSPACE",
            "CONTENT_SELF_REPAIR_PYTHON",
        ):
            value = os.environ.get(name)
            if value:
                child_env[name] = value
        if self.request_cli.is_file():
            cli_payload = (
                {"request": request_payload}
                if capability_id == "REQUEST_CODE_REPAIR"
                else request_payload
            )
            argv = [
                self.node,
                str(self.request_cli),
                json.dumps(
                    cli_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
        else:
            if capability_id != "REQUEST_CODE_REPAIR":
                raise RuntimeError("Pi Remote repair status requires repair-request-cli.js")
            argv = [
                self.node,
                str(self.cli),
                "repair-request",
                json.dumps(
                    {"request": request_payload},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
        completed = subprocess.run(
            argv,
            cwd=self.root,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=child_env,
        )
        if completed.returncode != 0:
            raise RuntimeError("Pi Remote rejected RepairRequest")
        try:
            response = json.loads(completed.stdout)
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("Pi Remote returned an invalid RepairRequest receipt") from exc
        if capability_id == "QUERY_CODE_REPAIR_STATUS":
            accepted = response.get("request") or {}
            rsi = response.get("rsi") if isinstance(response.get("rsi"), dict) else {}
            request_id = accepted.get("id")
            if not isinstance(request_id, str):
                raise RuntimeError("Pi Remote returned an incomplete repair status receipt")
            selection = rsi.get("selection") if isinstance(rsi.get("selection"), dict) else {}
            selected_candidate: dict[str, Any] = {}
            selected_candidate_id = str(selection.get("candidateId") or "")
            selected_child_id = str(selection.get("childRequestId") or "")
            for row in list(rsi.get("candidates") or []):
                if not isinstance(row, dict):
                    continue
                if (
                    (selected_candidate_id and str(row.get("candidateId") or "") == selected_candidate_id)
                    or (selected_child_id and str(row.get("childRequestId") or "") == selected_child_id)
                ):
                    selected_candidate = row
                    break
            selected_checkpoint = (
                selected_candidate.get("checkpoint")
                if isinstance(selected_candidate.get("checkpoint"), dict)
                else {}
            )
            rejected = []
            for row in list(rsi.get("proposalRejections") or []):
                if not isinstance(row, dict):
                    continue
                rejected.append({
                    "hypothesis": str(row.get("hypothesis") or "")[:1200],
                    "reason": str(row.get("reason") or "")[:1200],
                })
            for row in list(rsi.get("candidates") or []):
                if not isinstance(row, dict) or row.get("verdict") != "REJECTED":
                    continue
                evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
                rejected.append({
                    "hypothesis": str(row.get("hypothesis") or "")[:1200],
                    "reason": str(evaluation.get("reason") or "REJECTED")[:1200],
                })
            return {
                "status": str(accepted.get("status") or "UNKNOWN"),
                "requestId": request_id,
                "rsiStatus": str(rsi.get("status") or "NOT_STARTED"),
                "rsiRound": int(rsi.get("roundIndex") or 0),
                "selectedChildRequestId": selection.get("childRequestId"),
                "selectedCheckpointId": selection.get("checkpointId"),
                "selectedGitHead": selection.get("gitHead"),
                "selectedBaseCommit": selected_checkpoint.get("baseCommit"),
                "recentRejectedHypotheses": rejected[-8:],
                "rsiAutoResumed": bool(response.get("rsiAutoResumed")),
                "lastError": (
                    dict(rsi.get("lastError"))
                    if isinstance(rsi.get("lastError"), dict)
                    else None
                ),
            }
        accepted = response.get("request") or {}
        request_id = accepted.get("id")
        if not isinstance(request_id, str) or accepted.get("schema") != "pi-remote.repair-request.v1":
            raise RuntimeError("Pi Remote returned an incomplete RepairRequest receipt")
        return {
            "status": accepted.get("status", "RECEIVED"),
            "requestId": request_id,
            "created": bool(response.get("created")),
            "rsiAutoStarted": bool(response.get("rsiAutoStarted")),
        }


def configured_pi_remote_repair_provider() -> PiRemoteRepairProvider | None:
    root = (
        os.environ.get("PI_REMOTE_REPAIR_ROOT", "").strip()
        or os.environ.get("PI_REMOTE_ROOT", "").strip()
    )
    if not root:
        # Production already has an explicit self-repair workspace when this
        # capability is intended to exist.  Older deployments omitted the Pi
        # Remote root from their systemd drop-in; recover the sibling source
        # checkout only under that opt-in and only when the request-only CLI
        # surface is visibly present.
        workspace = os.environ.get("CONTENT_SELF_REPAIR_WORKSPACE", "").strip()
        if workspace:
            candidate = (
                Path(workspace).expanduser().resolve().parent
                / "src"
                / "pi-remote"
            )
            if (
                (candidate / "cli.js").is_file()
                and (candidate / "repair-requests.js").is_file()
            ):
                root = str(candidate)
    if not root:
        return None
    node = os.environ.get("PI_REMOTE_NODE", "node").strip() or "node"
    return PiRemoteRepairProvider(root=Path(root), node=node)


__all__ = ["PiRemoteRepairProvider", "configured_pi_remote_repair_provider"]
