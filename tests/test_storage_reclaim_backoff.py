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
        encodi