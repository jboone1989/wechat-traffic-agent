"""Evidence revisions are indexed in SQLite, not one JSON file per row."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from test_channel_evidence import T, row
from wechat_traffic_agent.channel_evidence import ChannelEvidenceStore
from wechat_traffic_agent.web_editorial import digest


def test_new_records_live_only_in_sqlite(tmp_path):
    store = ChannelEvidenceStore(tmp_path / "evidence")
    first = row(evidence_refs=("capture:first",))
    path = store.record(first)
    assert path.name == "evidence.sqlite3"
    assert path.is_file()
    assert not list(store.root.glob("*/*.json"))
    assert store.record(first) == path
    assert store.all_evidence_refs() == {"capture:first"}
    assert store.as_of(T + timedelta(days=4)) == [first]


def test_legacy_import_and_as_of_is_independent_of_old_files(tmp_path):
    root = tmp_path / "content/channel_evidence"
    first = row()
    second = row(value=21, revision=2, observed_at=T + timedelta(days=3))
    for item in (first, second):
        folder = root / digest(item.identity())
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{item.revision:05d}.json").write_text(item.model_dump_json())
    store = ChannelEvidenceStore(root)
    report = store.migrate_legacy()
    assert report == {"records": 2, "legacy_files_seen": 2}
    assert store.as_of(T + timedelta(days=2)) == [first]
    assert store.as_of(T + timedelta(days=4)) == [second]
    # Once imported, the JSON trees are not consulted at query time.
    for path in root.glob("*/*.json"):
        path.write_text("this is no longer a database")
    assert store.as_of(T + timedelta(days=4)) == [second]
    assert store.migrate_legacy() == {"records": 2, "legacy_files_seen": 0}


def test_legacy_path_identity_conflict_aborts_transaction(tmp_path):
    root = tmp_path / "evidence"
    folder = root / "invalid"
    folder.mkdir(parents=True)
    (folder / "00001.json").write_text(row().model_dump_json())
    store = ChannelEvidenceStore(root)
    with pytest.raises(ValueError, match="path/identity"):
        store.migrate_legacy()
    (folder / "00001.json").unlink()
    assert store.migrate_legacy() == {"records": 0, "legacy_files_seen": 0}


def test_revisions_retain_original_cutoff_and_conflict_behavior(tmp_path):
    store = ChannelEvidenceStore(tmp_path / "records")
    first = row()
    store.record(first)
    store.record(row(revision=2, value=45, observed_at=T + timedelta(days=5)))
    assert store.as_of(T + timedelta(days=3))[0].value == 10
    assert store.as_of(T + timedelta(days=6))[0].value == 45
    with pytest.raises(FileExistsError):
        store.record(row(value=22))
    assert len(store.as_of(T + timedelta(days=6))) == 1
