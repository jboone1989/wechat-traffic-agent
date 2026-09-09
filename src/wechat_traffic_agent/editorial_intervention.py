"""Bind editorial interventions to experiment episodes.

The packaged Experiment stays the original agent candidate. When an editor
changes what actually publishes, that intervention is recorded separately so
the published outcome is not attributed entirely to the agent candidate.
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict

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
    if editor_decision is EditorDecision.REJECT:
        if published_content is not None:
            raise ValueError("rejected episodes cannot bind published content")
        diff = ""
    else:
        if published_content is None:
            raise ValueError("published episodes require published content")
        diff = _edit_diff(original_draft, published_content)
        if editor_decision is EditorDecision.PUBLISH_AS_IS and diff:
            raise ValueError("publish_as_is cannot bind a changed draft")
        if editor_decision is EditorDecision.PUBLISH_WITH_EDITS and not diff:
            raise ValueError("publish_with_edits requires a content change")
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
        and intervention.edit_diff == ""
        and intervention.published_content is not None
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
