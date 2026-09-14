"""Immutable draft storage for generated ContentArtifacts.

Draft creation and public admission are deliberately separate. The inbox only
accepts non-public artifacts and uses a source fingerprint as its write-once
idempotency key.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .content_artifact import ContentArtifact
from .models import TopicSignal


class ContentDraftRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["content.draft.v1"] = "content.draft.v1"
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    artifact: ContentArtifact

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def draft_must_not_be_public(self) -> "ContentDraftRecord":
        if self.artifact.public_safe:
            raise ValueError("draft inbox refuses public-safe artifacts")
        return self


class ContentDraftInbox:
    """Write-once local draft inbox keyed by source fingerprint."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.drafts_dir = self.root / "drafts"

    def path_for(self, source_fingerprint: str) -> Path:
        _validate_fingerprint(source_fingerprint)
        return self.drafts_dir / f"{source_fingerprint}.json"

    def contains(self, source_fingerprint: str) -> bool:
        return self.path_for(source_fingerprint).exists()

    def save(self, record: ContentDraftRecord) -> Path:
        path = self.path_for(record.source_fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.model_dump_json(indent=2) + "\n"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(f"draft already exists for source {record.source_fingerprint}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def load(self, source_fingerprint: str) -> ContentDraftRecord:
        return ContentDraftRecord.model_validate_json(
            self.path_for(source_fingerprint).read_text(encoding="utf-8")
        )

    def list_records(self) -> tuple[ContentDraftRecord, ...]:
        if not self.drafts_dir.exists():
            return ()
        return tuple(
            ContentDraftRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.drafts_dir.glob("*.json"))
        )


def topic_set_fingerprint(topics: list[TopicSignal]) -> str:
    if not topics:
        raise ValueError("cannot fingerprint an empty topic set")
    rows = []
    for item in topics:
        rows.append({
            "title": item.title,
            "source": item.source,
            "source_url": item.source_url,
            "source_revision": item.source_revision,
            "evidence_refs": sorted(item.evidence_refs),
            "observed_at": item.observed_at.isoformat() if item.observed_at else None,
        })
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_fingerprint(value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("source fingerprint must be lowercase sha256 hex")


__all__ = ["ContentDraftInbox", "ContentDraftRecord", "topic_set_fingerprint"]
