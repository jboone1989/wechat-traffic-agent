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

from wechat_traffic_agent.content_admission import _write_once
from wechat_traffic_agent.content_artifact import ContentArtifact

SCHEMA = "website-experiment-binding.v1"
EXPERIMENT_VERSION = "website-opening-mode:v1"
ASSIGNMENT_METHOD = "sha256-cycle:v1"
TREATMENTS = ("reader_question_first", "evidence_first")


class WebsiteExperimentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-experiment-binding.v1"] = SCHEMA
    experiment_id: str = Field(min_length=1, max_length=100)
    cycle: str = Field(min_length=1, max_length=100)
    decided_at: datetime
    variable_under_test: Literal["opening_mode"] = "opening_mode"
    treatment_value: Literal["reader_question_first", "evidence_first"]
    assignment_method: Literal["sha256-cycle:v1"] = ASSIGNMENT_METHOD
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
        expected = _assignment_fields(self.cycle)
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError("Website experiment assignment does not match its cycle")
        return self


def bind_website_experiment(
    path: Path, *, cycle: str, decided_at: datetime
) -> WebsiteExperimentBinding:
    """Bind one treatment before article selection; reload exactly on restart."""
    path = Path(path)
    fields = _assignment_fields(cycle)
    if path.exists():
        saved = WebsiteExperimentBinding.model_validate_json(path.read_text(encoding="utf-8"))
        for field, expected in fields.items():
            if getattr(saved, field) != expected:
                raise ValueError("persisted Website experiment binding disagrees with cycle assignment")
        return saved
    if decided_at.utcoffset() is None:
        raise ValueError("experiment decision timestamp must be timezone-aware")
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


__all__ = [
    "WebsiteExperimentBinding", "bind_website_experiment", "experiment_instruction",
    "experiment_compliance", "EXPERIMENT_VERSION", "TREATMENTS",
]
