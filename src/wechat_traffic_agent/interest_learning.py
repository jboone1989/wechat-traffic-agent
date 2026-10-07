"""Project research evidence into neural interest signals; no independent state."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _unit(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return default


def topic_contexts(
    state_root: Path, topics: list[str], projection: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, float]]:
    """Use durable research/VirtualBrain state instead of hashed topic names."""
    projection = projection or {}
    interests = {str(row.get("name")): row for row in projection.get("interests", [])
                 if isinstance(row, Mapping) and row.get("name")}
    path = Path(state_root) / "content" / "learning" / "research.json"
    try:
        research = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        research = {}
    inquiries = research.get("inquiries") or {}
    context = {}
    for topic in dict.fromkeys(str(t).strip() for t in topics if str(t).strip()):
        interest = interests.get(topic, {})
        inquiry = inquiries.get(topic, {}) if isinstance(inquiries, Mapping) else {}
        status = str(inquiry.get("question_status") or "")
        progress = str(inquiry.get("last_progress") or "")
        familiarity = _unit(interest.get("familiarity"), 0.0)
        salience = _unit(interest.get("salience"), 0.0)
        last_observed = interest.get("last_observed_at")
        observed = None
        if isinstance(last_observed, str) and last_observed.strip():
            try:
                observed = datetime.fromisoformat(
                    last_observed.strip().replace("Z", "+00:00")
                )
            except ValueError:
                observed = None
        if observed is not None and (
            observed.tzinfo is None or observed.utcoffset() is None
        ):
            observed = None
        if observed is not None:
            elapsed_days = max(
                0.0, (datetime.now(timezone.utc) - observed).total_seconds() / 86400.0,
            )
            salience *= math.exp(-elapsed_days / 21.0)
        context[topic] = {
            "novelty": round(1.0 - familiarity, 6),
            "knowledge_gap": 0.85 if status in {"PARTIAL", "BLOCKED"} else
                             (0.18 if status == "ANSWERED" else 0.55),
            "progress_potential": 0.7 if progress in {"LEARNED", "DEEPENED"} else
                                  (0.35 if progress in {"REPEATED", "UNRESOLVED"} else 0.55),
            "preference": _unit(interest.get("preference"), 0.5),
            "salience": round(salience, 6),
        }
    return context


def learning_reward(result: Mapping[str, Any]) -> dict[str, Any]:
    """Only evidence-backed learning can reinforce neural interest."""
    application = result.get("application") or {}
    assessment = application.get("assessment") if isinstance(application, Mapping) else {}
    status = str(assessment.get("status") or "") if isinstance(assessment, Mapping) else ""
    application_evidence = application.get("evidence") if isinstance(application, Mapping) else []
    tested_against_new_evidence = any(
        isinstance(row, Mapping) and row.get("evidence_id") and row.get("quote")
        for row in (application_evidence or [])
    )
    sources = {str(row.get("evidence_id") or "") for row in result.get("sources", [])
               if isinstance(row, Mapping) and row.get("evidence_id")}
    facts = [row for row in result.get("facts") or []
             if isinstance(row, Mapping) and str(row.get("evidence_id") or "") in sources
             and str(row.get("quote") or "").strip()
             and str(row.get("observation") or "").strip()]
    progress = str(result.get("progress") or "")
    if status in {"CONFIRMED", "REFUTED"} and tested_against_new_evidence:
        utility, kind = (0.85 if status == "CONFIRMED" else 0.95), "TESTED_LEARNING"
    elif (progress in {"LEARNED", "DEEPENED"} and len(result.get("reasoning_steps") or []) >= 2
          and facts and len(str(result.get("learning_delta") or "").strip()) >= 20):
        utility = 0.50 if str(result.get("question_status")) == "ANSWERED" else 0.25
        kind = "GROUNDED_UNDERSTANDING"
    else:
        utility, kind = 0.0, "NO_VERIFIED_PROGRESS"
    return {
        "schema_version": "ferro-interest-learning.v1",
        "cycle": str(result.get("cycle") or ""),
        "topic": str(result.get("topic") or ""),
        "utility": utility, "kind": kind,
        "application_status": status or None,
        "source_evidence_ids": sorted(sources),
    }
