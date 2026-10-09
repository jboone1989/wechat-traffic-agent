"""Durable, idempotent provider trial receipts and acquisition outcomes.

All persistence for this workflow passes this port. Replayed outcomes keep
their original timestamp and do not trigger another behavior update.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

from agent_runtime.resource_discovery import ResourceAcquisitionOutcome


class ResourceOutcomeRecorder:
    def __init__(self, *, state_root: Path, scout: Any, write_json):
        self.state_root = Path(state_root)
        self.scout = scout
        self.write_json = write_json

    def record_trials(self, execution):
        for measurement, trial in zip(execution.measurements, execution.trials):
            self.scout.record_model_trial(trial)
            self._persist_trial_receipt(measurement.to_payload())

    def _persist_trial_receipt(self, row: dict[str, Any]) -> None:
        folder = self.state_root / "application" / "resource_discovery" / "trial_receipts"
        folder.mkdir(parents=True, exist_ok=True)
        key = sha256(str(row.get("trial_id") or "").encode()).hexdigest()[:24]
        self.write_json(folder / f"{key}.json", row)

    def record_acquisition_outcome(
        self, *, plan_id: str, lead_id: str, status: str, now: datetime,
        friction_kind: str = "NONE", source: str = "RUNTIME",
        detail_summary: str = "", evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("acquisition outcome time requires timezone")
        detail = " ".join(str(detail_summary or "").split())[:500]
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        digest_input = json.dumps({
            "plan_id": str(plan_id), "lead_id": str(lead_id), "status": str(status),
            "friction_kind": str(friction_kind), "source": str(source),
            "detail_summary": detail, "evidence_refs": refs,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        outcome = ResourceAcquisitionOutcome(
            outcome_id="resource-acquisition-outcome:" + sha256(digest_input.encode()).hexdigest()[:24],
            plan_id=str(plan_id), lead_id=str(lead_id), status=str(status),
            observed_at=now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            friction_kind=str(friction_kind), source=str(source), detail_summary=detail,
            evidence_refs=refs,
        )
        folder = self.state_root / "application" / "resource_discovery" / "acquisition_outcomes"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (outcome.outcome_id.split(":")[-1] + ".json")
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
                if str(prior.get("outcome_id") or "") == outcome.outcome_id:
                    return {
                        "outcome_id": outcome.outcome_id, "status": outcome.status,
                        "friction_kind": outcome.friction_kind,
                        "observed_at": str(prior.get("observed_at") or outcome.observed_at),
                        "created": False,
                    }
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        self.write_json(path, {
            "outcome_id": outcome.outcome_id, "plan_id": outcome.plan_id,
            "lead_id": outcome.lead_id, "status": outcome.status,
            "observed_at": outcome.observed_at, "friction_kind": outcome.friction_kind,
            "source": outcome.source, "detail_summary": outcome.detail_summary,
            "evidence_refs": list(outcome.evidence_refs), "schema_version": outcome.schema_version,
        })
        return {
            "outcome_id": outcome.outcome_id, "status": outcome.status,
            "friction_kind": outcome.friction_kind, "observed_at": outcome.observed_at,
            "created": True,
        }

    def acquisition_outcomes(self) -> list[ResourceAcquisitionOutcome]:
        folder = self.state_root / "application" / "resource_discovery" / "acquisition_outcomes"
        rows: list[ResourceAcquisitionOutcome] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                value["evidence_refs"] = tuple(value.get("evidence_refs") or ())
                rows.append(ResourceAcquisitionOutcome(**value))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(rows, key=lambda row: (row.observed_at, row.outcome_id))
