"""OKX.AI A2A marketplace integration for Ferro (Onchain OS v4.6.3+).

The OKX CLI is authoritative for identity, task state, assignment, delivery, and
protocol progression.  This adapter mirrors state and exposes exact provider
mutations; it never resurrects the retired ASP bidding/apply flow.
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
    """Strict JSON/script adapter around the official onchainos CLI."""

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
            stdout = str(getattr(completed, "stdout", "") or "").strip()
            detail = stderr or stdout
            raise OnchainOSCommandError(
                f"Onchain OS exited {completed.returncode}: {detail[:700]}"
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
                "Onchain OS stdout is not JSON; refusing to infer platform state"
            ) from exc
        if not isinstance(payload, dict):
            raise OnchainOSCommandError("Onchain OS JSON response must be an object")
        return OnchainOSResult(command=command, payload=payload)

    def run_progression(self, *args: str) -> OnchainOSResult:
        """next-action may return structured JSON or a trusted Markdown playbook."""
        command, stdout = self._invoke(*args)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {"playbook": stdout}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return OnchainOSResult(command=command, payload=payload)


def _data(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return dict(value) if isinstance(value, Mapping) else dict(payload)


def _rows(payload: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    raw = payload.get("data")
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, Mapping)]
    data = _data(payload)
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, Mapping)]
    payload_row = data.get("payload")
    if isinstance(payload_row, Mapping):
        for key in keys:
            value = payload_row.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _agent_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _rows(payload, "list", "agents", "items")
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


def _task_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _rows(payload, "items", "tasks", "jobs")


def _task_id(row: Mapping[str, Any]) -> str:
    for key in ("jobId", "job_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


class OKXAIMarketplace:
    """Ferro's ASP-side OKX.AI task surface.

    Reads are always allowed when the component is enabled.  Assignment,
    rejection and delivery writes additionally require the explicit production
    write switch.  Assignment decisions themselves belong to Ferro cognition,
    not to this transport adapter.
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
        self.enabled = okx_ai_marketplace_enabled() if enabled is None else bool(enabled)
        self.writes_enabled = (
            okx_ai_marketplace_writes_enabled()
            if writes_enabled is None
            else bool(writes_enabled)
        )

    def owned_asps(self) -> list[dict[str, Any]]:
        payload = self.cli.run("agent", "get-my-agents", "--role", "asp").payload
        return _agent_rows(payload)

    def resolve_agent_id(self) -> str:
        if self.agent_id:
            return self.agent_id
        ids: list[str] = []
        for row in self.owned_asps():
            value = str(
                row.get("agentId")
                or row.get("agent_id")
                or row.get("id")
                or ""
            ).strip()
            if value:
                ids.append(value)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise OnchainOSCommandError("no registered ASP identity is available")
        if len(ids) != 1:
            raise OnchainOSCommandError(
                "CONTENT_OKX_AI_AGENT_ID is required when ASP identity is not unique"
            )
        self.agent_id = ids[0]
        return self.agent_id

    def service_list(
        self,
        *,
        agent_id: str | None = None,
        service_id: str | None = None,
    ) -> list[dict[str, Any]]:
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        args = ["agent", "service-list", "--agent-id", asp_id]
        if service_id:
            args.extend(["--service-id", str(service_id)])
        payload = self.cli.run(*args).payload
        return _rows(payload, "services", "items", "list")

    def list_tasks(
        self,
        *,
        agent_id: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "asp", "list-tasks",
            "--agent-id", asp_id,
            "--page", str(max(1, int(page))),
            "--limit", str(max(1, min(int(limit), 100))),
        ).payload

    def task_status(
        self,
        job_id: str,
        *,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "asp", "status", str(job_id), "--agent-id", asp_id
        ).payload

    def next_action(
        self,
        *,
        message: Mapping[str, Any],
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        event = str(message.get("event") or "").strip()
        if not event:
            raise ValueError("OKX.AI system envelope requires a non-empty event")
        return self.cli.run_progression(
            "agent", "next-action",
            "--role", "auto",
            "--agentId", asp_id,
            "--message", json.dumps(
                dict(message), ensure_ascii=False, separators=(",", ":")
            ),
        ).payload

    def accept_job(self, job_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        self._require_writes()
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "accept-job-by-provider", str(job_id), "--agent-id", asp_id
        ).payload

    def decline_job(
        self,
        job_id: str,
        *,
        reason: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_writes()
        text = str(reason).strip()
        if not text or len(text) > 512:
            raise ValueError("provider decline reason must be 1..512 characters")
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "decline-job-by-provider", str(job_id),
            "--agent-id", asp_id,
            "--reason", text,
        ).payload

    def accept_subscription(
        self,
        job_id: str,
        *,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_writes()
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "accept-subscription", str(job_id), "--agent-id", asp_id
        ).payload

    def decline_subscription(
        self,
        job_id: str,
        *,
        reason: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_writes()
        text = str(reason).strip()
        if not text or len(text) > 512:
            raise ValueError("subscription decline reason must be 1..512 characters")
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "decline-subscription", str(job_id),
            "--agent-id", asp_id,
            "--reason", text,
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
        asp_id = str(agent_id or self.resolve_agent_id()).strip()
        return self.cli.run(
            "agent", "deliver", str(job_id),
            "--agent-id", asp_id,
            "--deliverable-text", deliverable,
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
                "schema_version": "ferro-okx-ai-task.v2",
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
        agent_id: str,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """Resolve trusted system envelopes exactly once through next-action."""
        inbox, processed, failed = self._notification_paths()
        inbox.mkdir(parents=True, exist_ok=True)
        outputs: list[dict[str, Any]] = []
        for path in sorted(inbox.glob("*.json"))[: max(1, int(limit))]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise ValueError("notification must be a JSON object")
                envelope_agent_id = str(raw.get("agentId") or "").strip()
                message = raw.get("message")
                if not envelope_agent_id or not isinstance(message, Mapping):
                    raise ValueError(
                        "notification must match {agentId,message:{source,event,...}}"
                    )
                if envelope_agent_id != agent_id:
                    raise ValueError("notification agentId does not match Ferro ASP")
                if str(message.get("source") or "") != "system":
                    raise ValueError("only trusted source=system notifications are accepted")
                progression = self.next_action(message=message, agent_id=agent_id)
                receipt = {
                    "schema_version": "ferro-okx-ai-next-action.v2",
                    "source_file": path.name,
                    "observed_at": now.astimezone(timezone.utc).isoformat(),
                    "agent_id": agent_id,
                    "event": str(message.get("event") or ""),
                    "job_id": str(message.get("jobId") or ""),
                    "next_action": progression,
                }
                _atomic_json(processed / path.name, receipt)
                path.unlink()
                outputs.append(
                    {
                        "source_file": path.name,
                        "status": "RESOLVED",
                        "event": receipt["event"],
                        "job_id": receipt["job_id"],
                        "next_action": progression,
                    }
                )
            except Exception as exc:
                receipt = {
                    "schema_version": "ferro-okx-ai-next-action.v2",
                    "source_file": path.name,
                    "observed_at": now.astimezone(timezone.utc).isoformat(),
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:700],
                }
                _atomic_json(failed / path.name, receipt)
                # Keep the source notification for explicit recovery/replay.
                outputs.append(receipt)
        return outputs

    def tick(self, *, now: datetime) -> dict[str, Any]:
        if not self.enabled:
            return {"task": "OKX_AI_MARKETPLACE", "status": "DISABLED"}

        now = now.astimezone(timezone.utc)
        try:
            agent_id = self.resolve_agent_id()
        except OnchainOSCommandError as exc:
            snapshot = {
                "schema_version": "ferro-okx-ai-marketplace.v2",
                "task": "OKX_AI_MARKETPLACE",
                "status": "NOT_REGISTERED",
                "observed_at": now.isoformat(),
                "message": str(exc),
            }
            _atomic_json(self.root / "latest.json", snapshot)
            return snapshot

        listing = self.list_tasks(agent_id=agent_id, page=1, limit=20)
        tasks = _task_rows(listing)
        compact: list[dict[str, Any]] = []
        for row in tasks:
            job_id = _task_id(row)
            if not job_id:
                raise OnchainOSCommandError(
                    "agent asp list-tasks returned a task without jobId"
                )
            detail = self.task_status(job_id, agent_id=agent_id)
            self._record_task(job_id, detail, now)
            compact.append({"job_id": job_id, "detail": detail})

        notifications = self.process_notifications(
            now=now, agent_id=agent_id, limit=16
        )
        snapshot = {
            "schema_version": "ferro-okx-ai-marketplace.v2",
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


DEFAULT_FERRO_SERVICES: tuple[dict[str, Any], ...] = (
    {
        "serviceName": "Deep Research Report",
        "serviceDescription": (
            "Evidence-backed technical or market research for teams and builders, "
            "with concise conclusions and source-aware uncertainty."
        ),
        "serviceType": "A2A",
        "fee": "8",
        "subscription": [],
    },
    {
        "serviceName": "AI Agent Engineering",
        "serviceDescription": (
            "Agent architecture, debugging, evaluation design, code review, and "
            "implementation analysis for AI agent developers."
        ),
        "serviceType": "A2A",
        "fee": "12",
        "subscription": [],
    },
    {
        "serviceName": "Web Automation Build",
        "serviceDescription": (
            "Website and workflow automation implementation and debugging for "
            "operators who need a verifiable delivered automation."
        ),
        "serviceType": "A2A",
        "fee": "20",
        "subscription": [],
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
        raise ValueError("picture_url must be the CDN URL returned by agent upload")
    return [
        "agent", "create",
        "--role", "asp",
        "--name", str(name).strip(),
        "--description", str(description).strip(),
        "--picture", picture,
        "--service", json.dumps(
            DEFAULT_FERRO_SERVICES, ensure_ascii=False, separators=(",", ":")
        ),
    ]


def ferro_validate_listing_argv(
    *,
    name: str = "Ferro",
    description: str = (
        "Persistent AI agent for evidence-backed research, agent/software engineering, "
        "and web automation delivery."
    ),
) -> list[str]:
    return [
        "agent", "validate-listing",
        "--role", "asp",
        "--name", str(name).strip(),
        "--description", str(description).strip(),
        "--service", json.dumps(
            DEFAULT_FERRO_SERVICES, ensure_ascii=False, separators=(",", ":")
        ),
    ]


__all__ = [
    "DEFAULT_FERRO_SERVICES",
    "OKXAIMarketplace",
    "OnchainOSCLI",
    "OnchainOSCommandError",
    "OnchainOSResult",
    "ferro_registration_argv",
    "ferro_validate_listing_argv",
    "okx_ai_marketplace_enabled",
    "okx_ai_marketplace_writes_enabled",
]
