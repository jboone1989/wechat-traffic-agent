"""Binance Square operator receipts use the shared mailbox, not X goal code."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from wechat_traffic_agent.publication.models import DistributionTask


def notify_square_distribution(task: DistributionTask, *, operator_root: Path, now: datetime) -> Path:
    """Write one idempotent mailbox notice for a completed Square attempt."""
    if task.channel != "binance_square":
        raise ValueError("not a Binance Square receipt")
    if task.status not in {"SUCCESS", "FAILED_PERMANENT", "RETRY_PENDING"}:
        raise ValueError("Square attempt has not completed")
    success = task.status == "SUCCESS"
    if success and not str(task.published_url or "").startswith("https://www.binance.com/square/post/"):
        raise ValueError("Square success requires a verified post URL")
    key = f"square:{task.distribution_id}:{task.status}:{task.attempts}"
    digest = sha256(key.encode()).hexdigest()[:24]
    attempt_at = datetime.fromisoformat(task.attempt_times[-1]) if task.attempt_times else now
    notification_id = ("note_zzferro_" +
                       attempt_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + digest)
    path = Path(operator_root) / "mailbox" / "notifications" / (notification_id + ".json")
    payload = {
        "notification_id": notification_id,
        "created_at": now.isoformat(),
        "agent_id": "content-agent",
        "cycle": 0,
        "kind": "FERRO_SQUARE_POSTING",
        "title": "Ferro 币安广场已发布" if success else "Ferro 币安广场发布失败",
        "message": ((task.published_url or "") if success else
                    f"{task.status}: {task.last_error or 'unverified'}")
                    + "\nPublicationEvent: " + task.event_id,
        "evidence_refs": ["ferro-square-posting:" + digest],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        pass
    return path
