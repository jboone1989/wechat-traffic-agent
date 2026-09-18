"""Deterministic governance for Website experiment promotion and revocation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .content_admission import _write_once

V1 = "website-opening-mode:v1"
V2 = "website-opening-mode:v2"
TREATMENTS = ("reader_question_first", "evidence_first")
CHAMPION_SHARE_PERCENT = 90


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("governance timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class ExperimentConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-experiment-conclusion.v1"] = "website-experiment-conclusion.v1"
    conclusion_id: str = Field(pattern=r"^webexp-conclusion:[0-9a-f]{24}$")
    experiment_version: Literal["website-opening-mode:v1"] = V1
    promoted_version: Literal["website-opening-mode:v2"] = V2
    variable_under_test: Literal["opening_mode"] = "opening_mode"
    favored_treatment: Literal["reader_question_first", "evidence_first"]
    evaluation_ref: str = Field(min_length=1)
    evidence_cutoff: datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    champion_share_percent: Literal[90] = CHAMPION_SHARE_PERCENT
    strategy_control: Literal[True] = True
    causal_claim: Literal[False] = False

    @field_validator("evidence_cutoff")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


class ExperimentRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-experiment-revocation.v1"] = "website-experiment-revocation.v1"
    revocation_id: str = Field(pattern=r"^webexp-revocation:[0-9a-f]{24}$")
    conclusion_id: str = Field(min_length=1)
    experiment_version: Literal["website-opening-mode:v2"] = V2
    reason: Literal["CONCURRENT_HOLDOUT_REVERSAL"] = "CONCURRENT_HOLDOUT_REVERSAL"
    favored_treatment: Literal["reader_question_first", "evidence_first"]
    evaluation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revoked_at: datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    strategy_control: Literal[True] = True
    causal_claim: Literal[False] = False

    @field_validator("revoked_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


def _root(state_root: Path) -> Path:
    return Path(state_root) / "content" / "website_experiment_governance"


def record_conclusion(
    *, state_root: Path, evaluation: Mapping[str, Any], concluded_at: datetime
) -> ExperimentConclusion:
    if evaluation.get("status") != "EVIDENCE_FAVORS_TREATMENT":
        raise ValueError("only favorable fixed-horizon evaluation can conclude an experiment")
    favored = str(evaluation.get("favored_treatment") or "")
    if favored not in TREATMENTS:
        raise ValueError("experiment conclusion requires favored treatment")
    evaluation_ref = str(evaluation.get("evaluation_ref") or "").strip()
    refs = tuple(dict.fromkeys(
        str(ref).strip() for ref in evaluation.get("evidence_refs") or () if str(ref).strip()
    ))
    if not evaluation_ref or not refs:
        raise ValueError("experiment conclusion requires evaluation provenance")
    cutoff_raw = evaluation.get("evidence_cutoff")
    cutoff = datetime.fromisoformat(str(cutoff_raw)) if cutoff_raw else concluded_at
    identity = {
        "experiment_version": V1,
        "favored_treatment": favored,
        "evaluation_ref": evaluation_ref,
    }
    conclusion = ExperimentConclusion(
        conclusion_id="webexp-conclusion:" + _digest(identity)[:24],
        favored_treatment=favored,
        evaluation_ref=evaluation_ref,
        evidence_cutoff=_utc(cutoff),
        evidence_refs=refs,
    )
    path = _root(state_root) / "conclusions" / f"{conclusion.conclusion_id.split(':', 1)[1]}.json"
    _write_once(path, conclusion.model_dump_json(indent=2) + "\n")
    return conclusion


def record_revocation(
    *, state_root: Path, conclusion: ExperimentConclusion,
    favored_treatment: str, evaluation_payload: Mapping[str, Any],
    revoked_at: datetime, evidence_refs: tuple[str, ...],
) -> ExperimentRevocation:
    if favored_treatment not in TREATMENTS or favored_treatment == conclusion.favored_treatment:
        raise ValueError("revocation requires concurrent evidence favoring the holdout")
    payload = {
        "conclusion_id": conclusion.conclusion_id,
        "favored_treatment": favored_treatment,
        "evaluation": dict(evaluation_payload),
    }
    evaluation_digest = _digest(payload)
    refs = tuple(dict.fromkeys(str(ref).strip() for ref in evidence_refs if str(ref).strip()))
    if not refs:
        raise ValueError("revocation requires evidence_refs")
    revocation = ExperimentRevocation(
        revocation_id="webexp-revocation:" + _digest({
            "conclusion_id": conclusion.conclusion_id,
            "evaluation_digest": evaluation_digest,
        })[:24],
        conclusion_id=conclusion.conclusion_id,
        favored_treatment=favored_treatment,
        evaluation_digest=evaluation_digest,
        revoked_at=_utc(revoked_at),
        evidence_refs=refs,
    )
    path = _root(state_root) / "revocations" / f"{revocation.revocation_id.split(':', 1)[1]}.json"
    _write_once(path, revocation.model_dump_json(indent=2) + "\n")
    return revocation


def conclusions(state_root: Path) -> list[ExperimentConclusion]:
    root = _root(state_root) / "conclusions"
    rows = [
        ExperimentConclusion.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.json"))
    ] if root.exists() else []
    rows.sort(key=lambda row: (row.evidence_cutoff, row.conclusion_id))
    return rows


def revocations(state_root: Path) -> list[ExperimentRevocation]:
    root = _root(state_root) / "revocations"
    rows = [
        ExperimentRevocation.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.json"))
    ] if root.exists() else []
    rows.sort(key=lambda row: (row.revoked_at, row.revocation_id))
    return rows


def active_conclusion(state_root: Path) -> ExperimentConclusion | None:
    revoked = {row.conclusion_id for row in revocations(state_root)}
    active = [row for row in conclusions(state_root) if row.conclusion_id not in revoked]
    return active[-1] if active else None


__all__ = [
    "CHAMPION_SHARE_PERCENT", "ExperimentConclusion", "ExperimentRevocation",
    "V1", "V2", "active_conclusion", "conclusions", "record_conclusion",
    "record_revocation", "revocations",
]
