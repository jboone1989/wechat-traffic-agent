"""Bind editorial interventions to experiment episodes.

The packaged Experiment stays the original agent candidate. When an editor
changes what actually publishes, that intervention is recorded separately so
the published outcome is not attributed entirely to the agent candidate.
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from .models import PerformanceObservation, TietuDraft


class EditorDecision(str, Enum):
    PUBLISH_AS_IS = "publish_as_is"
    PUBLISH_WITH_EDITS = "publish_with_edits"
    REJECT = "reject"


class EditorialIntervention(BaseModel):
    """Immutable binding of editor action onto one experiment episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    post_id: str
    original_draft: TietuDraft
    published_content: TietuDraft | None = None
    edit_diff: str
    editor_decision: EditorDecision
    policy_version: str
    model_version: str
    config_version: str

    @model_validator(mode="after")
    def _decision_matches_content(self) -> EditorialIntervention:
        if self.editor_decision is EditorDecision.REJECT:
            if self.published_content is not None:
                raise ValueError("rejected episodes cannot bind published content")
            if self.edit_diff:
                raise ValueError("rejected episodes cannot carry an edit diff")
            return self
        if self.published_content is None:
            raise ValueError("published episodes require published content")
        canonical = _edit_diff(self.original_draft, self.published_content)
        if self.edit_diff != canonical:
            raise ValueError("edit_diff must match original vs published content")
        if self.editor_decision is EditorDecision.PUBLISH_AS_IS and canonical:
            raise ValueError("publish_as_is cannot bind a changed draft")
        if self.editor_decision is EditorDecision.PUBLISH_WITH_EDITS and not canonical:
            raise ValueError("publish_with_edits requires a content change")
        return self


def _edit_diff(original: TietuDraft, published: TietuDraft) -> str:
    original_dump = original.model_dump()
    published_dump = published.model_dump()
    lines: list[str] = []
    for key in original_dump:
        if original_dump[key] != published_dump[key]:
            lines.append(f"{key}: {original_dump[key]!r} -> {published_dump[key]!r}")
    return "\n".join(lines)


def bind_editorial_intervention(
    *,
    experiment_id: str,
    post_id: str,
    original_draft: TietuDraft,
    published_content: TietuDraft | None,
    editor_decision: EditorDecision,
    policy_version: str,
    model_version: str,
    config_version: str,
) -> EditorialIntervention:
    """Record original candidate, published content, diff, decision, and versions."""
    diff = (
        ""
        if published_content is None
        else _edit_diff(original_draft, published_content)
    )
    return EditorialIntervention(
        experiment_id=experiment_id,
        post_id=post_id,
        original_draft=original_draft,
        published_content=published_content,
        edit_diff=diff,
        editor_decision=editor_decision,
        policy_version=policy_version,
        model_version=model_version,
        config_version=config_version,
    )


def agent_candidate_owns_outcome(intervention: EditorialIntervention) -> bool:
    """True only when the published artifact is the unmodified agent candidate."""
    return (
        intervention.editor_decision is EditorDecision.PUBLISH_AS_IS
        and intervention.published_content is not None
        and intervention.original_draft == intervention.published_content
    )


def agent_attributable_history(
    history: Sequence[PerformanceObservation],
    interventions: Sequence[EditorialIntervention] | None = None,
) -> list[PerformanceObservation]:
    """Drop observations whose published outcome is editor-modified or rejected."""
    if not interventions:
        return list(history)
    blocked = {
        item.post_id for item in interventions if not agent_candidate_owns_outcome(item)
    }
    return [obs for obs in history if obs.post_id not in blocked]
