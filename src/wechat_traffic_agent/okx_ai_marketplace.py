"""OKX.AI A2A marketplace integration through the official Onchain OS CLI.

This module deliberately keeps the marketplace protocol separate from OKX Orbit.
Onchain OS is the authority for ASP identity, task state, escrow, delivery, and
reward claims. The local ledger is only an auditable mirror of observed state.

Marketplace notifications are treated as activation data, never cognition
instructions. The bridge calls agent next-action and persists the returned
platform decision rather than inferring a workflow transition from free text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def okx_ai_marketplace_enabled() -> bool:
    return _enabled("CONTENT_OKX_AI_MARKETPLACE_ENABLED")


def okx_ai_marketplace_writes_enabled() -> bool:
    return _enabled("CONTENT_OKX_AI_MARKETPLACE_WRITES_ENABLED")


class OnchainOSCommandError(RuntimeError):
    """The official Onchain OS CLI could not complete a requested operation."""


@dataclass(frozen=True)
class OnchainOSResult:
    command: tuple[str, ...]
    payload: dict[str, Any]


Runner = Callable[..., Any]


class OnchainOSCLI:
    """Strict adapter around the public onchainos command."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout_seconds: int = 45,
        runner: Runner | None = None,
    ) -> None:
        self.binary = (
            binary
            or os.environ.get("CONTENT_OKX_AI_ONCHAINOS_BIN", "").strip()
            or "onchainos"
        )
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.runner = runner or subprocess.run

    def _invoke(self, *args: str) -> tuple[tuple[str, ...], str]:
        command = (self.binary, *tuple(str(arg) for arg in args))
        try:
            completed = self.runner(
                list(command),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OnchainOSCommandError(
                f"Onchain OS command could not start/finish: {type(exc).__name__}"
            ) from exc
        if int(getattr(completed, "returncode", 1)) != 0:
            stderr = str(getattr(completed, "stderr", "") or "").strip()
            raise OnchainOSCommandError(
                f"Onchain OS exited {completed.returncode}: {stderr[:500]}"
            )
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        if not stdout:
            raise OnchainOSCommandError("Onchain OS returned empty stdout")
        return command, stdout

    def run(self, *args: str) -> OnchainOSResult:
        command, stdout = self._invoke(*args)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise OnchainOSCommandError(
                "Onchain OS stdout is not JSON; refusing to infer task state"
            ) from exc
        if not isinstance(payload, dict):
            raise OnchainOSCommandError("Onchain OS JSON response must be an object")
        return OnchainOSResult(command=command, payload=payload)

    def run_script(self, *args: str) -> OnchainOSResult:
        """Preserve next-action output exactly, whether JSON or script text."""
        command, stdout = self._invoke(*args)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {"script": stdout}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return OnchainOSResult(command=command, payload=payload)


def _unwrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return dict(data) if isinstance(data, Mapping) else dict(payload)


def _rows(payload: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    raw_data = payload.get("data")
    if isinstance(raw_data, list):
        return [dict(row) for row in raw_data if isinstance(row, Mapping)]
    data = _unwrap(payload)
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, Mapping)]
    if isinstance(data.get("list"), list):
        return [
            dict(row)
            for row in data["list"]
            if isinstance(row, Mapping)
        ]
    return []


def _ready(payload: Mapping[str, Any]) -> bool:
    data = _unwrap(payload)
    for key in ("ready", "eligible", "ok", "passed"):
        if key in data:
            return bool(data[key])
    status = str(data.get("status") or "").strip().upper()
    return status in {"READY", "OK", "PASSED", "ACTIVE", "ELIGIBLE"}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _safe_job_id(row: Mapping[str, Any]) -> str:
    for key in ("jobId", "job_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _agent_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(payload, "agents", "items")
    flattened: list[dict[str, Any]] = []
    for row in rows:
        nested = row.get("agentList")
        if isinstance(nested, list):
            flattened.extend(
                dict(agent) for agent in nested if isinstance(agent, Mapping)
            )
        else:
            flattened.append(row)
    return flattened


class OKXAIMarketplace:
    """Observe and drive Ferro's OKX.AI A2A ASP lane.

    Normal polling is read-only. Economic/task transitions are only executed
    from an explicit platform next-action response or by direct primitive calls
    from an already-authorized higher-level policy.
    """

    def __init__(
        self,
        *,
        state_root: Path,
        cli: OnchainOSCLI | None = None,
        agent_id: str | None = None,
        enabled: bool | None = None,
        writes_enabled: bool | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "application" / "okx_ai_marketplace"
        self.cli = cli or OnchainOSCLI()
        self.agent_id = (
            str(agent_id or "").strip()
            or os.environ.get("CONTENT_OKX_AI_AGENT_ID", "").strip()
        )
        self.enabled = (
            okx_ai_marketplace_enabled() if enabled is None else bool(enabled)
        )
        self.writes_enabled = (
            okx_ai_marketplace_writes_enabled()
            if writes_enabled is None
            else bool(writes_enabled)
        )

    def _resolve_agent_id(self) -> str:
        if self.agent_id:
            return self.agent_id
        response = self.cli.run("agent", "get-my-agents", "--role", "asp").payload
        candidates = _agent_rows(response)
        ids: list[str] = []
        for row in candidates:
            value = str(
                row.get("agentId")
                or row.get("agent_id")
                or row.get("id")
                or ""
            ).strip()
            if value:
                ids.append(value)
        ids = list(dict.fromkeys(ids))
        if len(ids) != 1:
            raise OnchainOSCommandError(
                "CONTENT_OKX_AI_AGENT_ID is required when ASP identity is not unique"
            )
        self.agent_id = ids[0]
        return self.agent_id

    def owned_asps(self) -> list[dict[str, Any]]:
        response = self.cli.run("agent", "get-my-agents", "--role", "asp").payload
        return _agent_rows(response)

    def service_list(self, *, agent_id: str | None = None) -> list[dict[str, Any]]:
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        payload = self.cli.run(
            "agent", "service-list", "--agent-id", asp_id
        ).payload
        return _rows(payload, "services", "items")

    def gate_check(self) -> dict[str, Any]:
        return self.cli.run("agent", "gate-check", "--role", "asp").payload

    def active_tasks(self) -> list[dict[str, Any]]:
        payload = self.cli.run("agent", "active-tasks", "--role", "asp").payload
        return _rows(payload, "tasks", "jobs", "items")

    def task_status(self, job_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "status", str(job_id), "--agent-id", asp_id
        ).payload

    def quote(
        self,
        job_id: str,
        *,
        amount: str,
        symbol: str = "USDT",
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_writes()
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        symbol = str(symbol).upper()
        if symbol not in {"USDT", "USDG"}:
            raise ValueError("OKX.AI A2A quote symbol must be USDT or USDG")
        value = str(amount).strip()
        if not value:
            raise ValueError("quote amount is required")
        return self.cli.run(
            "agent", "apply", str(job_id),
            "--token-amount", value,
            "--token-symbol", symbol,
            "--agent-id", asp_id,
        ).payload

    def deliver_text(
        self,
        job_id: str,
        *,
        text: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_writes()
        deliverable = str(text).strip()
        if not deliverable:
            raise ValueError("deliverable text is required")
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "deliver", str(job_id),
            "--agent-id", asp_id,
            "--deliverable-text", deliverable,
        ).payload

    def claim_rewards(self, *, agent_id: str | None = None) -> dict[str, Any]:
        self._require_writes()
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "asp-claim-rewards", "--agent-id", asp_id
        ).payload

    def next_action(
        self,
        *,
        message: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        asp_id = str(agent_id or self._resolve_agent_id()).strip()
        return self.cli.run_script(
            "agent", "next-action",
            "--role", "auto",
            "--agentId", asp_id,
            "--message", json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")),
        ).payload

    def _require_writes(self) -> None:
        if not self.writes_enabled:
            raise PermissionError(
                "CONTENT_OKX_AI_MARKETPLACE_WRITES_ENABLED is not enabled"
            )

    def _record_task(self, job_id: str, payload: Mapping[str, Any], now: datetime) -> None:
        _atomic_json(
            self.root / "tasks" / f"{job_id}.json",
            {
                "schema_version": "ferro-okx-ai-task.v1",
                "job_id": job_id,
                "observed_at": now.astimezone(timezone.utc).isoformat(),
                "platform_status": dict(payload),
            },
        )

    def _notification_paths(self) -> tuple[Path, Path, Path]:
        base = self.root / "notifications"
        return base / "inbox", base / "processed", base / "failed"

    def process_notifications(
        self,
        *,
        now: datetime,
        agent_id: str | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """Resolve platform wake messages through next-action."""
        inbox, processed, failed = self._notification_paths()
        inbox.mkdir(parents=True, exist_ok=True)
        outputs: list[dict[str, Any]] = []
        for path in sorted(inbox.glob("*.json"))[: max(1, int(limit))]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise ValueError("notification must be a JSON object")
                message = raw.get("message")
                if not isinstance(message, Mapping):
                    message = raw
                action = self.next_action(message=message, agent_id=agent_id)
                receipt = {
                    "schema_version": "ferro-okx-ai-next-action.v1",
                    "source_file": path.name,
                    "observed_at": now.astimezone(timezone.utc).isoformat(),
                    "message": dict(message),
                    "next_action": action,
                }
                _atomic_json(processed / path.name, receipt)
                path.unlink()
                outputs.append({
                    "source_file": path.name,
                    "status": "RESOLVED",
                    "next_action": action,
                })
            except Exception as exc:
                receipt = {
                    "schema_version": "ferro-okx-ai-next-action.v1",
                    "source_file": path.name,
                    "observed_at": now.astimezone(timezone.utc).isoformat(),
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
                _atomic_json(failed / path.name, receipt)
                outputs.append(receipt)
        return outputs

    def tick(self, *, now: datetime) -> dict[str, Any]:
        if not self.enabled:
            return {"task": "OKX_AI_MARKETPLACE", "status": "DISABLED"}

        now = now.astimezone(timezone.utc)
        gate = self.gate_check()
        if not _ready(gate):
            snapshot = {
                "task": "OKX_AI_MARKETPLACE",
                "status": "NOT_READY",
                "gate_check": gate,
                "observed_at": now.isoformat(),
            }
            _atomic_json(self.root / "latest.json", snapshot)
            return snapshot

        agent_id = self._resolve_agent_id()
        tasks = self.active_tasks()
        compact: list[dict[str, Any]] = []
        for row in tasks:
            job_id = _safe_job_id(row)
            if not job_id:
                raise OnchainOSCommandError(
                    "active-tasks returned a task without a bounded job id"
                )
            status = self.task_status(job_id, agent_id=agent_id)
            self._record_task(job_id, status, now)
            compact.append({"job_id": job_id, "status": status})

        notifications = self.process_notifications(
            now=now, agent_id=agent_id, limit=16
        )
        snapshot = {
            "schema_version": "ferro-okx-ai-marketplace.v1",
            "task": "OKX_AI_MARKETPLACE",
            "status": "OK",
            "agent_id": agent_id,
            "observed_at": now.isoformat(),
            "writes_enabled": self.writes_enabled,
            "active_task_count": len(compact),
            "active_tasks": compact,
            "notification_results": notifications,
        }
        _atomic_json(self.root / "latest.json", snapshot)
        return snapshot


DEFAULT_FERRO_SERVICES: tuple[dict[str, str], ...] = (
    {
        "serviceName": "Deep Research Report",
        "serviceDescription": "Evidence-backed technical or market research with concise conclusions and source-aware uncertainty.",
        "serviceType": "A2A",
        "fee": "8",
    },
    {
        "serviceName": "AI Agent Engineering",
        "serviceDescription": "Agent architecture, debugging, evaluation design, code review, and implementation-oriented engineering analysis.",
        "serviceType": "A2A",
        "fee": "12",
    },
    {
        "serviceName": "Web Automation Build",
        "serviceDescription": "Website and workflow automation implementation, debugging, and delivery with verifiable acceptance criteria.",
        "serviceType": "A2A",
        "fee": "20",
    },
)


def ferro_registration_argv(
    *,
    picture_url: str,
    name: str = "Ferro",
    description: str = (
        "Persistent AI agent for evidence-backed research, agent/software engineering, "
        "and web automation delivery."
    ),
) -> list[str]:
    picture = str(picture_url).strip()
    if not picture.startswith(("https://", "http://")):
        raise ValueError("a public HTTP(S) picture URL is required by OKX.AI registration")
    return [
        "agent", "create",
        "--name", str(name).strip(),
        "--role", "asp",
        "--description", str(description).strip(),
        "--picture", picture,
        "--service", json.dumps(
            DEFAULT_FERRO_SERVICES,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ]


__all__ = [
    "DEFAULT_FERRO_SERVICES",
    "OKXAIMarketplace",
    "OnchainOSCLI",
    "OnchainOSCommandError",
    "OnchainOSResult",
    "ferro_registration_argv",
    "okx_ai_marketplace_enabled",
    "okx_ai_marketplace_writes_enabled",
]
