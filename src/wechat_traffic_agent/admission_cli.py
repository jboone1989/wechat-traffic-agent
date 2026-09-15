"""Private draft inspection and explicit operator admission commands."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .content_admission import AdmissionChecks, ContentAdmission, ContentReview, draft_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and manually review Content Agent drafts")
    parser.add_argument("--content-root", required=True, type=Path,
                        help="Private content directory under Content application state")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("show", "review-template", "export-approved"):
        sub = commands.add_parser(command)
        sub.add_argument("source_fingerprint")
    review = commands.add_parser("record-review")
    review.add_argument("--review", required=True, type=Path)
    args = parser.parse_args(argv)
    admission = ContentAdmission(args.content_root)
    try:
        if args.command == "list":
            result = []
            for draft in admission.inbox.list_records():
                fp = draft.source_fingerprint
                try:
                    status = admission.load_review(fp).decision
                except FileNotFoundError:
                    status = "PENDING_REVIEW"
                result.append({"source_fingerprint": fp, "title": draft.artifact.title,
                               "artifact_id": draft.artifact.artifact_id, "status": status,
                               "draft_sha256": draft_digest(draft), "public_safe": False})
        elif args.command in {"show", "review-template"}:
            draft = admission.inbox.load(args.source_fingerprint)
            if args.command == "show":
                result = {"draft": draft.model_dump(mode="json"), "draft_sha256": draft_digest(draft)}
            else:
                # Deliberately incomplete: no inferred approval or reviewer identity.
                result = {"schema_version": "content.review.v1",
                          "policy_version": "manual-content-admission:v1",
                          "source_fingerprint": draft.source_fingerprint,
                          "draft_sha256": draft_digest(draft), "artifact_id": draft.artifact.artifact_id,
                          "decision": None, "reviewer": None, "rationale": None,
                          "reviewed_at": datetime.now(timezone.utc).isoformat(),
                          "checks": AdmissionChecks().model_dump()}
        elif args.command == "record-review":
            review = ContentReview.model_validate_json(args.review.read_text(encoding="utf-8"))
            path = admission.record_review(review)
            result = {"status": "REVIEW_RECORDED", "decision": review.decision, "path": str(path)}
        else:
            path = admission.export_approved(args.source_fingerprint)
            result = {"status": "EXPORTED", "path": str(path), "published": False}
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
