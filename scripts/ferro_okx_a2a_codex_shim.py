#!/usr/bin/env python3
"""Codex-protocol shim that hands OKX A2A inbound messages to Ferro.

okx-a2a currently supports four named AI providers.  Ferro is its own persistent
harness, so the daemon is configured with provider=codex only as a transport
adapter name and this executable overrides the Codex binary.  No Codex model is
invoked here.

The shim validates the exact JSON payload supplied by okx-a2a, atomically writes
it to Ferro's durable OKX inbox, schedules an immediate wake, and emits the
minimal Codex JSONL lifecycle that okx-a2a requires to mark dispatch successful.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT", "").strip()
    return Path(raw or "/home/ubuntu/content-agent-state").expanduser().resolve()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _prompt(argv: list[str]) -> str:
    if not argv:
        raise ValueError("missing Codex-compatible prompt argument")
    value = str(argv[-1]).strip()
    if not value:
        raise ValueError("empty OKX A2A prompt")
    return value


def _session_id(prompt: str) -> str:
    existing = os.environ.get("OKX_AGENT_TASK_CODEX_SESSION_ID", "").strip()
    if existing:
        return existing
    key = "\0".join(
        (
            os.environ.get("OKX_A2A_CURRENT_SESSION_KEY", ""),
            os.environ.get("OKX_A2A_CURRENT_JOB_ID", ""),
            prompt,
        )
    )
    return "ferro-okx-" + sha256(key.encode("utf-8")).hexdigest()[:32]


def _validate_payload(prompt: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(prompt)
    except json.JSONDecodeError as exc:
        raise ValueError("OKX A2A inbound prompt is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("OKX A2A inbound payload must be an object")

    agent_id = str(payload.get("agentId") or "").strip()
    message = payload.get("message")
    if not agent_id or not isinstance(message, dict):
        raise ValueError(
            "OKX A2A inbound payload must contain agentId and message object"
        )

    env_agent_id = os.environ.get("OKX_A2A_CURRENT_AGENT_ID", "").strip()
    if env_agent_id and agent_id != env_agent_id:
        raise ValueError("payload agentId does not match daemon dispatch agent")

    payload_job_id = str(
        message.get("jobId") or payload.get("jobId") or ""
    ).strip()
    env_job_id = os.environ.get("OKX_A2A_CURRENT_JOB_ID", "").strip()
    if env_job_id and payload_job_id and payload_job_id != env_job_id:
        raise ValueError("payload jobId does not match daemon dispatch job")

    return payload, payload_job_id


def _inbox_path(root: Path, payload: Mapping[str, Any], prompt: str) -> tuple[Path, str]:
    message = payload.get("message")
    assert isinstance(message, Mapping)
    message_id = os.environ.get("OKX_A2A_CURRENT_MESSAGE_ID", "").strip()
    identity = "\0".join(
        (
            str(payload.get("agentId") or ""),
            str(message.get("jobId") or payload.get("jobId") or ""),
            message_id,
            prompt,
        )
    )
    receipt_id = sha256(identity.encode("utf-8")).hexdigest()[:32]

    if str(message.get("source") or "") == "system":
        event = str(message.get("event") or "").strip()
        if not event:
            raise ValueError("system notification is missing event")
        return (
            root
            / "application"
            / "okx_ai_marketplace"
            / "notifications"
            / "inbox"
            / f"{receipt_id}.json",
            receipt_id,
        )

    if message.get("isDirectCommunication") is True:
        return (
            root
            / "application"
            / "okx_ai_marketplace"
            / "peer_messages"
            / "inbox"
            / f"{receipt_id}.json",
            receipt_id,
        )

    # Non-system A2A envelopes can still contain task-scoped peer messages.
    return (
        root
        / "application"
        / "okx_ai_marketplace"
        / "peer_messages"
        / "inbox"
        / f"{receipt_id}.json",
        receipt_id,
    )


def _already_recorded(inbox: Path) -> bool:
    root = inbox.parent.parent
    name = inbox.name
    return any(
        candidate.exists()
        for candidate in (
            inbox,
            root / "processed" / name,
            root / "failed" / name,
        )
    )


def _schedule_wake(root: Path, *, receipt_id: str, job_id: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "schema_version": "content-wake-request.v1",
        "wake_id": "content:OKX_AI_MARKETPLACE",
        "at": now.isoformat(),
        "reason": "process authenticated OKX.AI A2A inbound event",
        "task": "OKX_AI_MARKETPLACE",
        "relevant_refs": [
            f"okx-a2a-ingress:{receipt_id}",
            *([f"okx-job:{job_id}"] if job_id else []),
        ],
        "cancel_conditions": [],
        "preemptible": False,
    }
    path = (
        root
        / "application"
        / "wake_requests"
        / f"okx-a2a-{receipt_id}.json"
    )
    _atomic_json(path, payload)


def _emit_codex_success(session_id: str, receipt_id: str) -> None:
    events = (
        {"type": "thread.started", "thread_id": session_id},
        {
            "type": "item.completed",
            "item": {
                "id": "ferro-okx-ingress-" + receipt_id,
                "type": "agent_message",
                "status": "completed",
                "text": "OKX A2A message durably handed to Ferro.",
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    )
    for event in events:
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    prompt = _prompt(args)
    payload, job_id = _validate_payload(prompt)
    root = _state_root()
    inbox, receipt_id = _inbox_path(root, payload, prompt)

    if not _already_recorded(inbox):
        _atomic_json(inbox, payload)
        _schedule_wake(root, receipt_id=receipt_id, job_id=job_id)

    _emit_codex_success(_session_id(prompt), receipt_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
