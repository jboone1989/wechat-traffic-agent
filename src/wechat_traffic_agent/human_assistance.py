"""Private human-assistance queue with optional WeCom delivery."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("human assistance timestamp requires timezone")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class WeComNotifier:
    def __init__(self, webhook_url: str | None = None, *, timeout_seconds: float = 10.0) -> None:
        self.webhook_url = str(webhook_url or os.environ.get("CONTENT_WECOM_WEBHOOK_URL") or "").strip()
        self.timeout_seconds = timeout_seconds
        if self.webhook_url:
            parsed = urlparse(self.webhook_url)
            if parsed.scheme != "https" or parsed.hostname not in {"qyapi.weixin.qq.com", "wecom.qq.com"}:
                raise ValueError("CONTENT_WECOM_WEBHOOK_URL must be an HTTPS WeCom endpoint")

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    def send_text(self, text: str) -> dict[str, Any]:
        content = str(text).strip()
        if not content:
            raise ValueError("WeCom message must not be empty")
        if not self.configured:
            return {"status": "NOT_CONFIGURED"}
        request = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"WeCom HTTP error {exc.code}") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("WeCom delivery failed") from exc
        if not isinstance(payload, dict) or int(payload.get("errcode", -1)) != 0:
            raise RuntimeError("WeCom rejected message")
        return {"status": "SENT"}


class RuntimeMailboxNotifier:
    """Publish private operator messages into Agent Runtime; Runtime owns delivery credentials."""
    def __init__(self, state_dir: Path | str | None = None) -> None:
        raw = str(state_dir or os.environ.get("CONTENT_OPERATOR_STATE_DIR") or "").strip()
        self.state_dir = Path(raw) if raw else None

    @property
    def configured(self) -> bool:
        return self.state_dir is not None and self.state_dir.exists()

    def send_text(self, text: str) -> dict[str, Any]:
        content = str(text).strip()
        if not content:
            raise ValueError("operator message must not be empty")
        if not self.configured:
            return {"status": "NOT_CONFIGURED"}
        from agent_runtime.communication.mailbox import RuntimeMailbox
        mailbox = RuntimeMailbox(self.state_dir)
        title = content.splitlines()[0].strip()[:160] or "Ferro update"
        notification = mailbox.publish_notification(
            agent_id="content-agent", cycle=0, kind="CONTENT_AGENT_PRIVATE",
            title=title, message=content, evidence_refs=["content-agent:private-operator-message"],
        )
        return {"status": "SENT", "transport": "runtime-mailbox", "notification_id": notification.notification_id}


def operator_notifier():
    runtime = RuntimeMailboxNotifier()
    if runtime.configured:
        return runtime
    return WeComNotifier()


class HumanAssistanceService:
    def __init__(
        self,
        *,
        state_root: Path,
        notifier: WeComNotifier | None = None,
        evolution_runtime: Any | None = None,
    ) -> None:
        self.root = Path(state_root) / "application" / "human_assistance"
        self.notifier = notifier or WeComNotifier()
        self.evolution_runtime = evolution_runtime

    def request(self, *, kind: str, summary: str, requested_action: str, now: datetime,
                urgency: str = "NORMAL", details: dict[str, Any] | None = None) -> dict[str, Any]:
        kind = str(kind).strip().upper()
        summary = str(summary).strip()
        requested_action = str(requested_action).strip()
        urgency = str(urgency).strip().upper()
        if not kind or not summary or not requested_action:
            raise ValueError("human assistance request fields must be non-empty")
        if urgency not in {"NORMAL", "HIGH", "CRITICAL"}:
            raise ValueError("unsupported human assistance urgency")
        created_at = _iso(now)
        key_payload = {"kind": kind, "summary": summary, "requested_action": requested_action}
        request_id = "human-request:" + sha256(json.dumps(key_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (request_id.replace(":", "-") + ".json")
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            changed = False
            if row.get("delivery_status") != "SENT" and self.notifier.configured:
                delivery = self.notifier.send_text(self._message(row))
                row["delivery_status"] = delivery["status"]
                row["last_delivery_at"] = created_at
                changed = True
            if row.get("evolution_observation_status") != "RECORDED":
                row = self._record_evolution(row, now=now)
                changed = True
            if changed:
                self._write(path, row)
            return row
        row = {
            "schema_version": "content-human-assistance.v1",
            "request_id": request_id,
            "kind": kind,
            "urgency": urgency,
            "summary": summary,
            "requested_action": requested_action,
            "details": dict(details or {}),
            "created_at": created_at,
            "delivery_status": "PENDING_CONFIGURATION" if not self.notifier.configured else "PENDING",
            "last_delivery_at": None,
        }
        if self.notifier.configured:
            delivery = self.notifier.send_text(self._message(row))
            row["delivery_status"] = delivery["status"]
            row["last_delivery_at"] = created_at
        row = self._record_evolution(row, now=now)
        self._write(path, row)
        return row

    def backfill_evolution_observations(self, *, now: datetime) -> dict[str, Any]:
        if not self.root.exists():
            return {"status": "OK", "recorded": 0, "failed": 0}
        recorded = 0
        failed = 0
        for path in sorted(self.root.glob("human-request-*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if row.get("evolution_observation_status") == "RECORDED":
                continue
            updated = self._record_evolution(row, now=now)
            if updated.get("evolution_observation_status") == "RECORDED":
                recorded += 1
            elif updated.get("evolution_observation_status") == "FAILED":
                failed += 1
            self._write(path, updated)
        return {"status": "OK", "recorded": recorded, "failed": failed}

    def _record_evolution(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        updated = dict(row)
        runtime = self.evolution_runtime
        observer = getattr(runtime, "observe_evolution_fact", None)
        if not callable(observer):
            updated["evolution_observation_status"] = "UNAVAILABLE"
            return updated
        details = updated.get("details") if isinstance(updated.get("details"), dict) else {}
        kind = str(updated.get("kind") or "HUMAN_ASSISTANCE").upper()
        urgency = str(updated.get("urgency") or "NORMAL").upper()
        if kind == "MODEL_RESOURCE_ACCESS" and details.get("credential_env"):
            subject = "protected-credential-handoff"
            recurrence_key = "HUMAN_DEPENDENCY:protected-credential-handoff"
            need_hint = (
                "Reduce recurring human dependency for protected credential "
                "handoff without exposing secret values"
            )
        else:
            subject = kind.lower().replace("_", "-")
            recurrence_key = f"HUMAN_DEPENDENCY:{subject}"
            need_hint = f"Reduce recurring human dependency around '{subject}'"
        severity = {"NORMAL": 0.50, "HIGH": 0.75, "CRITICAL": 0.95}.get(
            urgency, 0.50
        )
        try:
            fact = observer(
                kind="HUMAN_DEPENDENCY",
                subject=subject,
                summary=f"Human assistance was required for {subject}",
                severity=severity,
                evidence_refs=[str(updated.get("request_id") or "")],
                recurrence_key=recurrence_key,
                need_hint=need_hint,
                metadata={
                    "request_kind": kind,
                    "credential_env": str(details.get("credential_env") or ""),
                    "provider_label": str(details.get("provider_label") or ""),
                },
            )
            persist = getattr(runtime, "persist", None)
            if callable(persist):
                persist()
            updated["evolution_observation_status"] = "RECORDED"
            fact_id = getattr(fact, "fact_id", None)
            if fact_id:
                updated["evolution_fact_id"] = str(fact_id)
            updated["evolution_observed_at"] = _iso(now)
        except Exception as exc:  # evolution must never block a human request
            updated["evolution_observation_status"] = "FAILED"
            updated["evolution_error_type"] = type(exc).__name__
        return updated

    def fulfill_matching(
        self, *, kind: str, detail_key: str, detail_value: str, now: datetime,
        evidence_refs: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        fulfilled = []
        for path in sorted(self.root.glob("human-request-*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            details = row.get("details") or {}
            if str(row.get("kind") or "").upper() != str(kind).upper():
                continue
            if str(details.get(detail_key) or "") != str(detail_value):
                continue
            if row.get("resolution_status") == "FULFILLED":
                fulfilled.append(row)
                continue
            row["resolution_status"] = "FULFILLED"
            row["fulfilled_at"] = _iso(now)
            row["resolution_evidence_refs"] = list(dict.fromkeys(str(x) for x in evidence_refs if x))
            self._write(path, row)
            fulfilled.append(row)
        return fulfilled

    @staticmethod
    def _message(row: dict[str, Any]) -> str:
        return (
            f"Ferro 需要你的帮助 [{row['urgency']}]\n"
            f"{row['summary']}\n"
            f"需要你做：{row['requested_action']}"
        )

    @staticmethod
    def _write(path: Path, row: dict[str, Any]) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)


__all__ = ["WeComNotifier", "RuntimeMailboxNotifier", "operator_notifier", "HumanAssistanceService"]
