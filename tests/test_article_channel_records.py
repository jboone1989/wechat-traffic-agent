from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import pytest
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.publication.cycle_records import read_cycle_record, write_cycle_record
from wechat_traffic_agent.publication.event_store import PublicationEventStore
from wechat_traffic_agent.publication.models import DistributionTask
from test_binance_square_distribution import event

def migration():
    path = Path(__file__).resolve().parents[1] / "scripts/migrate_publication_records.py"
    spec = importlib.util.spec_from_file_location("migrate_records", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate

def test_channels_share_one_article_and_survive_restart(tmp_path):
    store = PublicationEventStore(tmp_path)
    store.save_event(event())
    store.enqueue_distribution(DistributionTask("x1", "event1", "x"))
    store.records.put("artifact-event1", "binance_square", {"draft_id": "s1", "body": "独立中文研究", "status": "REVIEWED"})
    restarted = PublicationEventStore(tmp_path)
    with restarted.records.repo.connect() as db:
        assert db.execute("SELECT count(*) FROM articles").fetchone()[0] == 1
    assert restarted.records.get("binance_square", "s1")["body"] == "独立中文研究"
    assert restarted.records.repo.list_artifacts() == []
    assert not (tmp_path / "distribution").exists()
    assert not (tmp_path / "square_editorial/drafts").exists()

def test_concurrent_workers_claim_only_once(tmp_path):
    store = PublicationEventStore(tmp_path)
    store.save_event(event())
    store.enqueue_distribution(DistributionTask("x1", "event1", "x"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: PublicationEventStore(tmp_path).claim_next(), range(2)))
    assert sum(c is not None for c in claims) == 1

def test_transaction_rolls_back_all_channel_changes(tmp_path):
    records = ChannelRecords(tmp_path)
    with pytest.raises(RuntimeError):
        with records.transaction():
            records.put("a", "x", {"status": "PENDING"})
            records.put("a", "binance_square", {"status": "REVIEWED"})
            raise RuntimeError("abort")
    assert records.all("x") == records.all("binance_square") == []

def test_migration_keeps_unknown_submission_and_success_without_requeue(tmp_path):
    root = tmp_path / "square_editorial/drafts"
    root.mkdir(parents=True)
    for identity, status in [("a", "SUCCESS"), ("b", "PUBLISHING"), ("c", "UNVERIFIED")]:
        (root / (identity + ".json")).write_text(json.dumps({"draft_id": identity, "status": status, "body": identity}))
    run = migration()
    assert run(tmp_path)["applied"] is False
    assert len(list(root.glob("*.json"))) == 3
    assert run(tmp_path, apply=True)["imported_records"] == 3
    assert not list(root.glob("*.json"))
    assert {r["status"] for r in ChannelRecords(tmp_path).all("binance_square")} == {"SUCCESS", "PUBLISHING", "UNVERIFIED"}
    assert run(tmp_path, apply=True)["imported_records"] == 0

def test_verified_x_status_survives_late_unknown_result(tmp_path):
    path = tmp_path / "content/continuous/cycles/a/x_social_expression.json"
    write_cycle_record(path, {"status": "POSTED", "post_id": "123", "url": "https://x.com/a/status/123"})
    write_cycle_record(path, {"status": "PUBLICATION_UNVERIFIED"})
    assert read_cycle_record(path)["status"] == "POSTED"
    assert not path.exists()
