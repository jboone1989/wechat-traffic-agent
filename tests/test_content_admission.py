from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.admission_cli import main
from wechat_traffic_agent.content_admission import (
    AdmissionChecks, ContentAdmission, ContentReview, draft_digest,
)
from wechat_traffic_agent.content_artifact import ContentArtifact, ContentSection, ContentSourceRef
from wechat_traffic_agent.draft_inbox import ContentDraftRecord
from wechat_traffic_agent.site_cli import build_site


def make_draft(root: Path, fingerprint="a" * 64):
    admission = ContentAdmission(root)
    record = ContentDraftRecord(
        source_fingerprint=fingerprint,
        generated_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        artifact=ContentArtifact(
            artifact_id="research-" + fingerprint[:8], kind="research", slug="reviewed-research",
            title="What research evidence can establish", summary="Research still requires review.",
            sections=(ContentSection(heading="Evidence", paragraphs=("A bounded observation.",)),),
            source_refs=(ContentSourceRef(ref_id="trading:report:1", source="agent-network:trading.cai.001"),),
            created_at=datetime(2026, 9, 14, tzinfo=timezone.utc), public_safe=False,
        ),
    )
    admission.inbox.save(record)
    return admission, record


def review_for(draft, **changes):
    payload = dict(
        source_fingerprint=draft.source_fingerprint, draft_sha256=draft_digest(draft),
        artifact_id=draft.artifact.artifact_id, decision="APPROVE", reviewer="fixture-editor",
        rationale="Fixture review, not an approval of production content.",
        reviewed_at=draft.generated_at + timedelta(minutes=1),
        checks=AdmissionChecks(**{name: "PASS" for name in AdmissionChecks.model_fields}),
    )
    payload.update(changes)
    return ContentReview(**payload)


def test_approval_export_is_separate_immutable_and_restart_safe(tmp_path):
    admission, draft = make_draft(tmp_path)
    original_bytes = admission.inbox.path_for(draft.source_fingerprint).read_bytes()
    review = review_for(draft)
    review_path = admission.record_review(review)
    exported = admission.export_approved(draft.source_fingerprint)
    exported_bytes = exported.read_bytes()
    restored = ContentAdmission(tmp_path)
    assert restored.record_review(review) == review_path
    assert restored.export_approved(draft.source_fingerprint) == exported
    assert exported.read_bytes() == exported_bytes
    assert restored.inbox.path_for(draft.source_fingerprint).read_bytes() == original_bytes
    assert restored.inbox.load(draft.source_fingerprint).artifact.public_safe is False
    assert ContentArtifact.model_validate_json(exported_bytes).public_safe is True
    assert exported.stat().st_mode & 0o777 == 0o600
    assert len(list(admission.reviews_dir.glob("*.json"))) == 1
    with pytest.raises(FileExistsError):
        restored.record_review(review_for(draft, decision="REJECT"))


@pytest.mark.parametrize("status", ["FAIL", "UNKNOWN"])
@pytest.mark.parametrize("field", list(AdmissionChecks.model_fields))
def test_incomplete_checks_cannot_approve(tmp_path, field, status):
    _, draft = make_draft(tmp_path)
    checks = {name: "PASS" for name in AdmissionChecks.model_fields}
    checks[field] = status
    with pytest.raises(ValidationError, match="requires PASS"):
        review_for(draft, checks=AdmissionChecks(**checks))
    assert not (tmp_path / "reviews").exists()


def test_pending_and_rejected_drafts_cannot_export(tmp_path):
    admission, draft = make_draft(tmp_path)
    assert admission.approved_artifacts() == []
    with pytest.raises(FileNotFoundError):
        admission.export_approved(draft.source_fingerprint)
    admission.record_review(review_for(draft, decision="REJECT", checks=AdmissionChecks()))
    assert admission.approved_artifacts() == []
    with pytest.raises(ValueError, match="not been approved"):
        admission.export_approved(draft.source_fingerprint)
    assert not admission.exports_dir.exists()


@pytest.mark.parametrize("change", ["hash", "artifact", "time", "fingerprint"])
def test_review_must_bind_exact_existing_draft(tmp_path, change):
    admission, draft = make_draft(tmp_path)
    updates = {
        "hash": {"draft_sha256": "0" * 64}, "artifact": {"artifact_id": "other"},
        "time": {"reviewed_at": draft.generated_at - timedelta(seconds=1)},
        "fingerprint": {"source_fingerprint": "b" * 64},
    }
    with pytest.raises((ValueError, FileNotFoundError)):
        admission.record_review(review_for(draft, **updates[change]))
    assert not admission.reviews_dir.exists()


def test_tampered_draft_and_review_are_rejected_on_reload(tmp_path):
    admission, draft = make_draft(tmp_path)
    path = admission.record_review(review_for(draft))
    draft_path = admission.inbox.path_for(draft.source_fingerprint)
    payload = json.loads(draft_path.read_text())
    payload["artifact"]["summary"] = "Modified after editorial review."
    draft_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="does not match"):
        ContentAdmission(tmp_path).export_approved(draft.source_fingerprint)
    draft_path.write_text(draft.model_dump_json())
    review = json.loads(path.read_text())
    review["source_fingerprint"] = "b" * 64
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="filename"):
        ContentAdmission(tmp_path).approved_artifacts()


def test_missing_provenance_cannot_be_manually_passed(tmp_path):
    admission, draft = make_draft(tmp_path)
    payload = draft.model_dump(mode="json")
    payload["artifact"]["source_refs"] = []
    draft = ContentDraftRecord.model_validate(payload)
    admission.inbox.path_for(draft.source_fingerprint).write_text(draft.model_dump_json())
    with pytest.raises(ValueError, match="provenance"):
        admission.record_review(review_for(draft))


def test_cli_template_requires_explicit_review_and_never_publishes(tmp_path, capsys):
    admission, draft = make_draft(tmp_path / "content")
    prefix = ["--content-root", str(tmp_path / "content")]
    assert main(prefix + ["list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "PENDING_REVIEW"
    assert main(prefix + ["review-template", draft.source_fingerprint]) == 0
    template = json.loads(capsys.readouterr().out)
    assert template["decision"] is None
    assert set(template["checks"].values()) == {"UNKNOWN"}
    path = tmp_path / "review.json"
    path.write_text(json.dumps(template))
    assert main(prefix + ["record-review", "--review", str(path)]) == 1
    capsys.readouterr()
    path.write_text(review_for(draft).model_dump_json())
    assert main(prefix + ["record-review", "--review", str(path)]) == 0
    capsys.readouterr()
    assert main(prefix + ["export-approved", draft.source_fingerprint]) == 0
    assert json.loads(capsys.readouterr().out)["published"] is False
    assert admission.inbox.load(draft.source_fingerprint).artifact.public_safe is False


def test_site_only_includes_explicitly_approved_content(tmp_path):
    admission, draft = make_draft(tmp_path / "private")
    config = tmp_path / "config.json"
    config.write_text('{"site_name":"Fixture","base_url":"https://example.com","description":"Fixture evidence.","indexable":false}')
    starter = tmp_path / "starter.json"
    base = draft.artifact.model_dump(mode="json")
    base.update(artifact_id="starter", slug="starter", public_safe=True)
    starter.write_text(json.dumps(base))
    result = build_site(config, starter, tmp_path / "before", approved_content_root=tmp_path / "private")
    assert result["artifact_ids"] == ["starter"]
    admission.record_review(review_for(draft))
    # Merely recording approval does not opt the default site build into new content.
    assert build_site(config, starter, tmp_path / "default")["artifact_count"] == 1
    result = build_site(config, starter, tmp_path / "approved", approved_content_root=tmp_path / "private")
    assert result["artifact_count"] == 2
    assert (tmp_path / "approved/research/reviewed-research/index.html").exists()
    assert not list((tmp_path / "approved").rglob("*.json"))
    # Export files are not a trust source: builds rederive from the verified review.
    export = admission.export_approved(draft.source_fingerprint)
    export.write_text("tampered export")
    assert build_site(config, starter, tmp_path / "again", approved_content_root=tmp_path / "private")["artifact_count"] == 2
    with pytest.raises(ValueError, match="separate"):
        build_site(config, starter, tmp_path / "private/site", approved_content_root=tmp_path / "private")
