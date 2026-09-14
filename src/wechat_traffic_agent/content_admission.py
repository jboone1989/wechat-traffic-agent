"""Explicit operator review of immutable drafts; no publishing actuator."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .content_artifact import ContentArtifact
from .draft_inbox import ContentDraftInbox, ContentDraftRecord


class AdmissionChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provenance: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    factual_support: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    privacy: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    originality: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    uncertainty: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"


class ContentReview(BaseModel):
    """Operator attestations bound to the exact persisted draft, not its title."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["content.review.v1"] = "content.review.v1"
    policy_version: Literal["manual-content-admission:v1"] = "manual-content-admission:v1"
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: str = Field(min_length=1, max_length=200)
    decision: Literal["APPROVE", "REJECT"]
    reviewer: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime
    checks: AdmissionChecks

    @field_validator("reviewer", "rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewer and rationale must not be blank")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def approval_requires_all_checks(self) -> "ContentReview":
        if self.decision == "APPROVE" and any(
            result != "PASS" for result in self.checks.model_dump().values()
        ):
            raise ValueError("APPROVE requires PASS for every review check")
        return self


def draft_digest(record: ContentDraftRecord) -> str:
    raw = json.dumps(record.model_dump(mode="json"), sort_keys=True,
                     ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_once(path: Path, payload: str) -> None:
    """Publish complete bytes atomically, with no overwrite even across processes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != payload:
                raise FileExistsError("conflicting immutable review or export") from None
    finally:
        Path(name).unlink(missing_ok=True)


class ContentAdmission:
    """Local operator interface; never invoked by the generation worker/Network."""

    def __init__(self, content_root: Path) -> None:
        self.inbox = ContentDraftInbox(content_root)
        self.reviews_dir = Path(content_root) / "reviews"
        self.exports_dir = Path(content_root) / "approved"

    def _review_path(self, fingerprint: str) -> Path:
        # Reuse the inbox's fingerprint validation before constructing any path.
        return self.reviews_dir / self.inbox.path_for(fingerprint).name

    def _bound_draft(self, review: ContentReview) -> ContentDraftRecord:
        record = self.inbox.load(review.source_fingerprint)
        if record.source_fingerprint != review.source_fingerprint:
            raise ValueError("draft filename and source fingerprint disagree")
        if record.artifact.artifact_id != review.artifact_id or draft_digest(record) != review.draft_sha256:
            raise ValueError("review does not match the immutable draft content")
        if review.reviewed_at < record.generated_at:
            raise ValueError("review predates draft creation")
        return record

    def record_review(self, review: ContentReview) -> Path:
        review = ContentReview.model_validate_json(review.model_dump_json())
        record = self._bound_draft(review)
        if review.decision == "APPROVE" and not record.artifact.source_refs:
            raise ValueError("approval requires source provenance")
        path = self._review_path(review.source_fingerprint)
        _write_once(path, review.model_dump_json(indent=2) + "\n")
        return path

    def load_review(self, fingerprint: str) -> ContentReview:
        review = ContentReview.model_validate_json(self._review_path(fingerprint).read_text(encoding="utf-8"))
        if review.source_fingerprint != fingerprint:
            raise ValueError("review filename and source fingerprint disagree")
        self._bound_draft(review)
        return review

    def approved_artifact(self, fingerprint: str) -> ContentArtifact:
        review = self.load_review(fingerprint)
        if review.decision != "APPROVE":
            raise ValueError("draft has not been approved")
        draft = self._bound_draft(review).artifact
        if not draft.source_refs:
            raise ValueError("approval requires source provenance")
        # Only a separately validated derivative is admitted; the inbox is untouched.
        payload = draft.model_dump(mode="json")
        payload["public_safe"] = True
        return ContentArtifact.model_validate(payload)

    def export_approved(self, fingerprint: str) -> Path:
        artifact = self.approved_artifact(fingerprint)
        path = self.exports_dir / self.inbox.path_for(fingerprint).name
        _write_once(path, artifact.model_dump_json(indent=2) + "\n")
        return path

    def approved_artifacts(self) -> list[ContentArtifact]:
        if not self.inbox.root.is_dir():
            raise FileNotFoundError("private content root does not exist")
        artifacts = []
        for path in sorted(self.reviews_dir.glob("*.json")):
            review = self.load_review(path.stem)
            if review.decision == "APPROVE":
                artifacts.append(self.approved_artifact(path.stem))
        return artifacts
