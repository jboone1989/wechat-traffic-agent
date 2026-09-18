"""Prospective, restart-stable Website writing experiment binding.

The binding is decided from the cycle before source selection or outcome data.
It constrains one writing surface only and carries no publication authority.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .content_admission import _write_once
from .content_artifact import ContentArtifact
from .website_experiment_governance import V1, V2, active_conclusion

SCHEMA = "website-experiment-binding.v1"
EXPERIMENT_VERSION = V1
PROMOTED_EXPERIMENT_VERSION = V2
ASSIGNMENT_METHOD = "sha256-cycle:v1"
PROMOTED_ASSIGNMENT_METHOD = "sha256-cycle-champion-holdout:v2"
TREATMENTS = ("reader_question_first", "evidence_first")


class WebsiteExperimentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-experiment-binding.v1"] = SCHEMA
    experiment_id: str = Field(min_length=1, max_length=100)
    cycle: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    variable_under_test: Literal["opening_mode"] = "opening_mode"
    treatment_value: Literal["reader_question_first", "evidence_first"]
    experiment_version: Literal["website-opening-mode:v1", "website-opening-mode:v2"] = EXPERIMENT_VERSION
    assignment_method: Literal["sha256-cycle:v1", "sha256-cycle-champion-holdout:v2"] = ASSIGNMENT_METHOD
    champion_treatment: Literal["reader_question_first", "evidence_first"] | None = None
    conclusion_ref: str | None = None
    champion_share_percent: int = Field(default=50, ge=0, le=100)
    assignment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_version: str = Field(min_length=1, max_length=160)
    generation_authority: Literal["OPENING_CONSTRAINT_ONLY"] = "OPENING_CONSTRAINT_ONLY"
    publication_authority: Literal[False] = False
    learning_authority: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("experiment decision timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def assignment_matches_cycle(self):
        if self.experiment_version == EXPERIMENT_VERSION:
            expected = _assignment_fields(self.cycle)
        else:
            if self.champion_treatment is None or not self.conclusion_ref:
                raise ValueError("promoted Website experiment requires champion provenance")
            expected = _promoted_assignment_fields(
                self.cycle,
                champion_treatment=self.champion_treatment,
                conclusion_ref=self.conclusion_ref,
            )
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError("Website experiment assignment does not match its cycle")
        return self


def bind_website_experiment(
    path: Path, *, cycle: str, decided_at: datetime, state_root: Path | None = None
) -> WebsiteExperimentBinding:
    """Bind prospectively; a governed conclusion promotes future cycles to 90/10."""
    path = Path(path)
    if path.exists():
        saved = WebsiteExperimentBinding.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.cycle != str(cycle).strip():
            raise ValueError("persisted Website experiment binding disagrees with cycle")
        return saved
    if decided_at.utcoffset() is None:
        raise ValueError("experiment decision timestamp must be timezone-aware")
    conclusion = active_conclusion(state_root) if state_root is not None else None
    if conclusion is None:
        fields = _assignment_fields(cycle)
    else:
        fields = _promoted_assignment_fields(
            cycle,
            champion_treatment=conclusion.favored_treatment,
            conclusion_ref=conclusion.conclusion_id,
        )
    bound = WebsiteExperimentBinding(decided_at=decided_at, **fields)
    _write_once(path, bound.model_dump_json(indent=2) + "\n")
    return bound


def experiment_instruction(binding: WebsiteExperimentBinding) -> str:
    if binding.treatment_value == "reader_question_first":
        return (
            'Prospective experiment constraint: the FIRST paragraph must begin exactly "Reader question:" '
            'and contain a genuine question mark within its first 240 characters. Do not apply this prefix '
            'to later paragraphs merely to satisfy the check.'
        )
    return (
        'Prospective experiment constraint: the FIRST paragraph must begin exactly "Observation:" and '
        'state a source-grounded observation before interpretation. Do not use "Reader question:" as its prefix.'
    )


def experiment_compliance(
    artifact: ContentArtifact, binding: WebsiteExperimentBinding
) -> dict:
    paragraph = ""
    if artifact.sections and artifact.sections[0].paragraphs:
        paragraph = artifact.sections[0].paragraphs[0].strip()
    if binding.treatment_value == "reader_question_first":
        passed = paragraph.startswith("Reader question:") and any(
            marker in paragraph[:240] for marker in ("?", "？")
        )
    else:
        passed = paragraph.startswith("Observation:")
    return {
        "schema_version": "website-experiment-compliance.v1",
        "experiment_id": binding.experiment_id,
        "variable_under_test": binding.variable_under_test,
        "treatment_value": binding.treatment_value,
        "status": "PASS" if passed else "FAIL",
        "checked_surface": "sections[0].paragraphs[0]",
        "publication_authority": False,
        "learning_authority": False,
    }


def _assignment_fields(cycle: str) -> dict:
    cycle = str(cycle).strip()
    if not cycle:
        raise ValueError("Website experiment cycle must be non-empty")
    assignment_digest = hashlib.sha256(
        f"{EXPERIMENT_VERSION}|{cycle}".encode("utf-8")
    ).hexdigest()
    treatment = TREATMENTS[int(assignment_digest[:8], 16) % len(TREATMENTS)]
    return {
        "experiment_id": f"webexp-{assignment_digest[:20]}",
        "cycle": cycle,
        "variable_under_test": "opening_mode",
        "treatment_value": treatment,
        "assignment_method": ASSIGNMENT_METHOD,
        "assignment_digest": assignment_digest,
        "strategy_version": f"{EXPERIMENT_VERSION}:{treatment}",
    }


def _promoted_assignment_fields(
    cycle: str, *, champion_treatment: str, conclusion_ref: str
) -> dict:
    cycle = str(cycle).strip()
    conclusion_ref = str(conclusion_ref).strip()
    if not cycle or champion_treatment not in TREATMENTS or not conclusion_ref:
        raise ValueError("promoted Website experiment assignment is incomplete")
    assignment_digest = hashlib.sha256(
        f"{PROMOTED_EXPERIMENT_VERSION}|{cycle}|{champion_treatment}|{conclusion_ref}".encode("utf-8")
    ).hexdigest()
    bucket = int(assignment_digest[:8], 16) % 100
    holdout = next(item for item in TREATMENTS if item != champion_treatment)
    treatment = champion_treatment if bucket < 90 else holdout
    return {
        "experiment_id": f"webexp-{assignment_digest[:20]}",
        "cycle": cycle,
        "variable_under_test": "opening_mode",
        "treatment_value": treatment,
        "experiment_version": PROMOTED_EXPERIMENT_VERSION,
        "assignment_method": PROMOTED_ASSIGNMENT_METHOD,
        "champion_treatment": champion_treatment,
        "conclusion_ref": conclusion_ref,
        "champion_share_percent": 90,
        "assignment_digest": assignment_digest,
        "strategy_version": f"{PROMOTED_EXPERIMENT_VERSION}:{treatment}",
    }


__all__ = [
    "WebsiteExperimentBinding", "bind_website_experiment", "experiment_instruction",
    "experiment_compliance", "EXPERIMENT_VERSION", "PROMOTED_EXPERIMENT_VERSION", "TREATMENTS",
]
