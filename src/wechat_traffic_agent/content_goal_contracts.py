"""Content research selection contract."""
from __future__ import annotations
from typing import Literal
from pydantic import Field, field_validator
from .content_loop import Contract

class EvidenceSelection(Contract):
    title: str = Field(min_length=4, max_length=80)
    evidence_id: str
    topic: str
    related_ids: tuple[str, ...] = Field(max_length=3)
    reader_question: str = Field(min_length=10, max_length=400)
    gap: str = Field(min_length=10, max_length=500)

    @field_validator("gap", mode="before")
    @classmethod
    def _clamp_gap(cls, v):
        return v[:500] if isinstance(v, str) else v

    @field_validator("reader_question", mode="before")
    @classmethod
    def _clamp_reader_question(cls, v):
        return v[:400] if isinstance(v, str) else v


class WorldviewDecision(Contract):
    action: Literal["DEFER", "COMMIT"]
    proposition: str = Field(min_length=10, max_length=500)
    stance: float = Field(ge=-1.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    support: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=600)


class CognitionDecision(Contract):
    cognition_state: Literal["BELIEF_CHANGED", "INVESTIGATING", "NO_MATERIAL_CHANGE"]
    worth_saying: bool
    expression_kind: Literal["LONGFORM", "RESEARCH_NOTE", "METHOD_NOTE", "THOUGHT", "NONE"]
    what_changed: str = Field(min_length=5, max_length=600)
    what_matters: str = Field(min_length=5, max_length=600)
    belief_proposition: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=10, max_length=600)
