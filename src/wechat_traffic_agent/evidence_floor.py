"""Mandatory counter-evidence floor for high-risk financial content.

W3 requires counter-evidence but deliberately does not require source
independence; the latter remains a separate governed G1 candidate.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_artifact import ContentArtifact

PLAN_SCHEMA = "website-evidence-floor-plan.v1"
CHECK_SCHEMA = "website-evidence-floor-check.v1"
COUNTER_PREFIX = "Counter-evidence:"

HIGH_RISK_TOPICS = frozenset({
    "backtesting", "drawdown", "execution", "expectancy", "live-trading",
    "paper-trading", "portfolio", "prediction", "profitability", "risk",
    "trading-bots",
})
DOMAIN_TERMS = (
    "bitcoin", "btc", "crypto", "stock", "etf", "market", "asset",
    "portfolio", "trading", "trade", "strategy", "return", "profit",
    "drawdown", "leverage", "position",
)
CLAIM_TERMS = (
    "buy", "sell", "outperform", "beat ", "profitable",
    "alpha", "win rate", "sharpe",
    "reduce risk", "reduces risk", "improve returns", "improves returns",
)


class EvidenceFloorSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=200)
    source_kind: Literal["rss", "agent-network", "web-search", "public-api"]
    source: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=2, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    url: str | None = Field(default=None, max_length=1000)
    observed_at: str | None = Field(default=None, max_length=100)
    source_revision: str = Field(min_length=1, max_length=200)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=50)
    search_query: str | None = Field(default=None, max_length=500)
    search_rank: int | None = None


class EvidenceFloorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-evidence-floor-plan.v1"] = PLAN_SCHEMA
    topic: str = Field(min_length=1, max_length=100)
    required: bool
    trigger_cues: tuple[str, ...] = Field(max_length=24)
    status: Literal["NOT_REQUIRED", "SATISFIED", "MISSING"]
    counter_query: str | None = Field(default=None, max_length=500)
    counter_evidence: EvidenceFloorSource | None = None
    counter_match_quote: str | None = Field(default=None, max_length=600)
    counter_match_reason: str | None = Field(default=None, max_length=600)
    independence_required: Literal[False] = False
    independence_checked: Literal[False] = False
    publication_authority: Literal[False] = False
    learning_authority: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        if not self.required:
            if self.status != "NOT_REQUIRED" or self.counter_evidence is not None:
                raise ValueError("non-required Evidence Floor must be NOT_REQUIRED without counter evidence")
        elif self.status == "SATISFIED":
            if self.counter_evidence is None or not self.counter_match_quote or not self.counter_match_reason:
                raise ValueError("satisfied Evidence Floor requires matched counter evidence")
            source_text = _candidate_text(self.counter_evidence)
            if self.counter_match_quote not in source_text:
                raise ValueError("counter match quote must be an exact candidate substring")
        elif self.status != "MISSING" or self.counter_evidence is not None:
            raise ValueError("required Evidence Floor must be SATISFIED or MISSING")
        return self


class EvidenceFloorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["website-evidence-floor-check.v1"] = CHECK_SCHEMA
    required: bool
    status: Literal["PASS", "FAIL"]
    trigger_cues: tuple[str, ...] = Field(max_length=24)
    counter_section_index: int | None = Field(default=None, ge=0)
    counter_source_id: str | None = Field(default=None, max_length=300)
    counter_source_quote: str | None = Field(default=None, max_length=600)
    failure_reason: str | None = Field(default=None, max_length=200)
    independence_required: Literal[False] = False
    independence_checked: Literal[False] = False
    publication_authority: Literal[False] = False
    learning_authority: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "PASS" and self.required:
            if self.counter_section_index is None or not self.counter_source_id or not self.counter_source_quote:
                raise ValueError("passing required Evidence Floor needs verified counter support")
        if self.status == "FAIL" and not self.failure_reason:
            raise ValueError("failed Evidence Floor requires a reason")
        return self


def risk_cues(*, topic: str, text: str) -> tuple[str, ...]:
    """Return deterministic minimum-floor cues; semantic independence is out of scope."""
    normalized = " ".join(str(text).lower().split())
    cues: list[str] = []
    topic = str(topic).strip()
    domains = [term for term in DOMAIN_TERMS if term in normalized]
    claims = [term.strip() for term in CLAIM_TERMS if term in normalized]
    # Topic membership alone is not a claim. A neutral methodology article about trading or
    # backtesting should remain publishable; the floor is for substantive performance/advice claims.
    if topic in HIGH_RISK_TOPICS and claims:
        cues.append("topic:" + topic)
    if domains and claims:
        cues.extend("domain:" + value for value in domains[:4])
        cues.extend("claim:" + value for value in claims[:4])
    return tuple(dict.fromkeys(cues))[:24]


def planned_risk_cues(*, topic: str, title: str, reader_question: str,
                      gap: str, primary_evidence: dict[str, Any]) -> tuple[str, ...]:
    text = "\n".join((
        title, reader_question, gap,
        str(primary_evidence.get("title") or ""),
        str(primary_evidence.get("summary") or ""),
    ))
    return risk_cues(topic=topic, text=text)


def artifact_risk_cues(artifact: ContentArtifact, *, topic: str) -> tuple[str, ...]:
    text = "\n".join([
        artifact.title, artifact.summary,
        *[value for section in artifact.sections for value in (section.heading, *section.paragraphs)],
    ])
    return risk_cues(topic=topic, text=text)


def _candidate_text(candidate: EvidenceFloorSource) -> str:
    return candidate.title + "\n" + candidate.summary


def counter_section_index(artifact: ContentArtifact) -> int | None:
    indexes = []
    for index, section in enumerate(artifact.sections):
        if any(paragraph.strip().startswith(COUNTER_PREFIX) for paragraph in section.paragraphs):
            indexes.append(index)
    if len(indexes) != 1:
        return None
    return indexes[0]


def _support_field(support: Any, name: str):
    if isinstance(support, dict):
        return support.get(name)
    return getattr(support, name, None)


def evaluate_evidence_floor(*, artifact: ContentArtifact, plan: EvidenceFloorPlan,
                            supports: tuple[Any, ...] | list[Any]) -> EvidenceFloorCheck:
    final_cues = artifact_risk_cues(artifact, topic=plan.topic)
    required = plan.required or bool(final_cues)
    cues = tuple(dict.fromkeys((*plan.trigger_cues, *final_cues)))[:24]
    if not required:
        return EvidenceFloorCheck(required=False, status="PASS", trigger_cues=cues)
    if plan.status != "SATISFIED" or plan.counter_evidence is None:
        return EvidenceFloorCheck(
            required=True, status="FAIL", trigger_cues=cues,
            failure_reason="COUNTER_EVIDENCE_NOT_ACQUIRED",
        )
    section_index = counter_section_index(artifact)
    if section_index is None:
        return EvidenceFloorCheck(
            required=True, status="FAIL", trigger_cues=cues,
            failure_reason="COUNTER_EVIDENCE_PARAGRAPH_MISSING_OR_AMBIGUOUS",
        )
    candidate = plan.counter_evidence
    source_text = _candidate_text(candidate)
    allowed_ids = {candidate.evidence_id, *candidate.evidence_refs}
    for support in supports:
        source_id = _support_field(support, "source_id")
        quote = _support_field(support, "source_quote")
        if (
            _support_field(support, "section_index") == section_index
            and source_id in allowed_ids
            and isinstance(quote, str)
            and quote in source_text
        ):
            return EvidenceFloorCheck(
                required=True, status="PASS", trigger_cues=cues,
                counter_section_index=section_index,
                counter_source_id=source_id, counter_source_quote=quote,
            )
    return EvidenceFloorCheck(
        required=True, status="FAIL", trigger_cues=cues,
        counter_section_index=section_index,
        failure_reason="COUNTER_EVIDENCE_EXACT_SUPPORT_MISSING",
    )


def verify_evidence_floor_publication(*, artifact: ContentArtifact,
                                      plan: EvidenceFloorPlan,
                                      check: EvidenceFloorCheck,
                                      supports: tuple[Any, ...] | list[Any]) -> None:
    recomputed = evaluate_evidence_floor(artifact=artifact, plan=plan, supports=supports)
    if recomputed != check or check.status != "PASS":
        raise ValueError("automatic publication requires actual Evidence Floor compliance")


__all__ = [
    "EvidenceFloorSource", "EvidenceFloorPlan", "EvidenceFloorCheck", "COUNTER_PREFIX",
    "planned_risk_cues", "artifact_risk_cues", "evaluate_evidence_floor",
    "verify_evidence_floor_publication",
]
