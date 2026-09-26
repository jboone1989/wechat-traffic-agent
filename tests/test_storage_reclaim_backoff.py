from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.survival_resources import SurvivalResourceManager

NOW = datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc)


def _reconcile(manager, now):
    return manager._reconcile_needs(
        now=now,
        storage_mode="INITIAL",
        compute_pressure=False,
        active_remote=True,
        usable_remote=True,
        degraded_replica=False,
        automatic_replica_gap=False,
        core_standby_capacity_gap=False,
        provisionable_remote=False,
    )


def test_storage_reclaim_no_effect_enters_retry_backoff(tmp_path, monkeypatch):
    manager = SurvivalResourceManager(tmp_path)
    manager.needs_path.parent.mkdir(parents=True, exist_ok=True)
    manager.needs_path.write_text(
        json.dumps([{
            "need_id": "survival-need:storage",
            "domain": "STORAGE",
            "action": "RECLAIM_TRANSIENT_STORAGE",
            "urgency": "NORMAL",
            "reason": "pressure",
            "status": "APPROVED",
            "authorization": "AUTONOMOUS_SAFE_POLICY",
            "created_at": NOW.isoformat(),
            "occurrence_count": 1,
        }]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        manager.storage_lifecycle,
        "reclaim",
        lambda **kwargs: {
            "status": "OK",
            "deleted_count": 0,
            "deleted_tmp_files": 0,
            "reclaimed_bytes_estimated": 0,
        },
    )

    result = manager.execute_approved(now=NOW)

    assert result["results"][0]["status"] == "NO_EFFECT"
    need = manager.needs()[0]
    assert need["status"] == "AWAITING_RESOURCE"
    assert need["retry_count"] == 1
    assert need["last_action_at"] == NOW.isoformat()
    assert need["next_retry_at"] == (NOW + timedelta(seconds=300)).isoformat()

    early = _reconcile(manager, NOW + timedelta(minutes=1))
    assert early[0]["status"] == "AWAITING_RESOURCE"

    due = _reconcile(manager, NOW + timedelta(seconds=301))
    assert due[0]["status"] == "APPROVED"
    assert due[0]["retry_rearmed_at"] == (
        NOW + timedelta(seconds=301)
    ).isoformat()
