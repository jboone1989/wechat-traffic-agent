"""Content research selection contract."""
from __future__ import annotations
from typing import Literal
from pydantic import Field
from .content_loop import Contract

class EvidenceSelection(Contract):
    title: str = Field(min_length=4, max_length=80)
    evidence_id: str
    topic: str
    related_ids: tuple[str, ...] = Field(max_length=3)
    reader_question: str = Field(min_length=10, max_length=400)
    gap: str = Field(min_length=10, max_length=500)


class WorldviewDecision(Contract):
    action: Literal["DEFER", "COMMIT"]
    proposition: str = Field(min_length=10, max_length=500)
    stance: float = Field(ge=-1.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    support: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=600)
