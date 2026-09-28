"""Decision-conditioned retrieval for Ferro cognition.

The builder is information-need centric: it plans what one decision needs, retrieves
across live state and canonical memory, reranks evidence, expands dependencies, and
allocates a bounded fact budget.  It does not call an LLM and grants no authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .memory_retrieval import MemoryRouter


_STOP = frozenset({
    "the", "and", "for", "from", "with", "that", "this", "what", "when",
    "where", "which", "how", "why", "current", "next", "goal", "task",
    "action", "agent", "owner", "evidence", "context", "using", "use",
})
_NEGATIVE_TERMS = frozenset({
    "fail", "failed", "failure", "blocked", "blocker", "risk", "conflict",
    "contradict", "invalid", "error", "regression", "breach", "missed",
    "失败", "错误", "阻塞", "风险", "冲突", "违约", "回归", "反例", "推翻",
})
_RESOURCE_TERMS = frozenset({
    "resource", "budget", "cost", "compute", "token", "disk", "storage",
    "financial", "资源", "预算", "成本", "磁盘", "存储", "额度",
})


@dataclass(frozen=True)
class InformationNeed:
    need_id: str
    question: str
    source_hints: tuple[str, ...]
    priority: float
    contradictory: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "question": self.question,
            "source_hints": list(self.source_hints),
            "priority": self.priority,
            "contradictory": self.contradictory,
        }


def _clip(value: Any, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)] + "...[truncated]"


def _terms(value: Any) -> set[str]:
    lowered = str(value or "").lower()
    terms = {
        token
        for token in re.findall(r"[a-z0-9_.:-]{2,}", lowered)
        if token not in _STOP and not token.isdigit()
    }
    for block in re.findall(r"[\u3400-\u9fff]+", lowered):
        for size in (2, 3, 4):
            if len(block) >= size:
                terms.update(
                    block[index : index + size]
                    for index in range(len(block) - size + 1)
                )
    return terms


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value or "")


def plan_information_needs(
    *,
    prompt: str,
    decision_frame: Mapping[str, Any],
    owner_scope: Mapping[str, Any] | None,
    missions: Sequence[Mapping[str, Any]],
    resource_pressure: Any = None,
) -> list[InformationNeed]:
    """Turn one delegated decision into explicit evidence questions."""

    decision_type = str(decision_frame.get("decision_type") or "GENERAL_COGNITION")
    objective_parts = [
        str(prompt or ""),
        str((owner_scope or {}).get("description") or ""),
        str((owner_scope or {}).get("topic") or ""),
        *[str(row.get("description") or "") for row in missions],
    ]
    objective = " ".join(part.strip() for part in objective_parts if part.strip())
    objective = _clip(objective, 1400)

    needs: list[InformationNeed] = [
        InformationNeed(
            "objective",
            f"What exact objective, priority, commitment, and success condition govern this decision? {objective}",
            ("goal", "mission", "commitment", "plan"),
            1.0,
        ),
    ]

    if decision_type in {
        "OWNER_GOAL_ACTION", "PLANNING", "SELECTION", "VERIFICATION",
        "PREDICTION", "LEARNING_UPDATE", "RESEARCH",
    }:
        needs.append(
            InformationNeed(
                "current_constraints",
                f"What current blockers, permissions, resources, capabilities, or operational constraints change the feasible action? {objective}",
                ("resource", "capability", "operational", "mission", "plan"),
                0.95,
            )
        )

    if decision_type in {"OWNER_GOAL_ACTION", "PLANNING", "SELECTION"}:
        needs.extend([
            InformationNeed(
                "past_outcomes",
                f"What similar past attempts, failures, successful recoveries, and measured world outcomes should change this choice? {objective}",
                ("experience", "memory", "experiment", "belief_change"),
                0.95,
            ),
            InformationNeed(
                "dependencies",
                f"What mission, goal, plan, promise, blocker, or outcome is causally/dependently linked to the candidate action? {objective}",
                ("mission", "goal", "plan", "commitment", "memory"),
                0.9,
            ),
        ])
    elif decision_type == "VERIFICATION":
        needs.append(
            InformationNeed(
                "verification_evidence",
                f"What observed result, prior prediction, failure, and evidence source can verify or falsify the claim? {objective}",
                ("experience", "memory", "belief_change", "operational"),
                1.0,
            )
        )
    elif decision_type == "PREDICTION":
        needs.append(
            InformationNeed(
                "prediction_base_rate",
                f"What historical outcomes, calibrated errors, and current state determine the prediction base rate? {objective}",
                ("experience", "memory", "experiment", "belief"),
                0.95,
            )
        )
    elif decision_type in {"LEARNING_UPDATE", "RESEARCH"}:
        needs.append(
            InformationNeed(
                "learning_evidence",
                f"What repeated evidence, counterexample, and world outcome justifies changing a belief or strategy? {objective}",
                ("experience", "memory", "belief", "belief_change", "experiment"),
                0.95,
            )
        )
    else:
        needs.append(
            InformationNeed(
                "relevant_history",
                f"What durable history or current state would materially change the answer to this decision? {objective}",
                ("memory", "experience", "belief", "relationship"),
                0.8,
            )
        )

    needs.append(
        InformationNeed(
            "counterevidence",
            f"What evidence, failure, contradiction, broken promise, blocker, or risk could overturn the obvious/current choice? {objective}",
            ("experience", "memory", "belief_change", "relationship", "operational"),
            0.9,
            contradictory=True,
        )
    )

    pressure_text = str(resource_pressure or "").strip()
    if pressure_text and pressure_text.upper() not in {"", "NORMAL", "OK", "HEALTHY", "NONE"}:
        needs.append(
            InformationNeed(
                "internal_pressure",
                f"How should current internal/resource pressure constrain this decision, and what prior recovery evidence applies? pressure={pressure_text}; {objective}",
                ("resource", "internal", "experience", "memory"),
                0.92,
            )
        )

    # Stable de-duplication by need identity.
    return list({need.need_id: need for need in needs}.values())


def candidate(
    *,
    context_id: str,
    source: str,
    kind: str,
    payload: Any,
    text: str | None = None,
    importance: float = 0.65,
    confidence: float = 0.75,
    current: bool = False,
    refs: Iterable[str] = (),
    entities: Iterable[str] = (),
    topics: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "context_id": str(context_id),
        "source": str(source),
        "kind": str(kind),
        "text": _clip(text if text is not None else _text(payload)),
        "payload": payload,
        "importance": max(0.0, min(1.0, float(importance))),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "current": bool(current),
        "refs": list(dict.fromkeys(str(item) for item in refs if str(item)))[:48],
        "entities": list(dict.fromkeys(str(item) for item in entities if str(item)))[:24],
        "topics": list(dict.fromkeys(str(item) for item in topics if str(item)))[:24],
    }


def canonical_memory_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "memory_id": row.get("memory_id"),
        "memory_type": row.get("memory_type"),
        "kind": row.get("kind"),
        "text": row.get("text"),
        "created_at": row.get("created_at"),
        "observed_at": row.get("observed_at"),
        "status": row.get("status"),
        "importance": row.get("importance"),
        "confidence": row.get("confidence"),
        "source_type": row.get("source_type"),
        "source_ref": row.get("source_ref"),
        "evidence_refs": list(row.get("evidence_refs") or [])[:24],
        "entities": list(row.get("entities") or [])[:16],
        "topics": list(row.get("topics") or [])[:16],
        "payload": row.get("payload") if isinstance(row.get("payload"), Mapping) else {},
    }
    refs = [
        str(row.get("source_ref") or ""),
        *[str(item) for item in row.get("source_refs") or []],
        *[str(item) for item in row.get("evidence_refs") or []],
    ]
    return candidate(
        context_id=f"memory:{row.get('memory_id')}",
        source="memory",
        kind=str(row.get("memory_type") or row.get("kind") or "MEMORY"),
        payload=payload,
        text=str(row.get("text") or ""),
        importance=float(row.get("importance") or 0.0),
        confidence=float(row.get("confidence") or 0.0),
        current=False,
        refs=refs,
        entities=row.get("entities") or (),
        topics=row.get("topics") or (),
    )


def _source_match(source: str, hints: Sequence[str]) -> bool:
    value = str(source)
    return any(value == hint or value.startswith(hint + ":") for hint in hints)


def _rank_candidate(
    row: Mapping[str, Any],
    need: InformationNeed,
    *,
    anchor_terms: set[str],
    owner_goal_ids: set[str],
    owner_mission_ids: set[str],
    recall_rank: int | None,
) -> tuple[float, list[str]]:
    text = str(row.get("text") or "")
    terms = _terms(text)
    need_terms = _terms(need.question)
    overlap = len(terms & need_terms)
    normalized_overlap = overlap / max(1.0, (len(need_terms) ** 0.5))
    lexical = min(1.8, normalized_overlap * 0.72)
    score = lexical
    reasons: list[str] = []
    if overlap:
        reasons.append(f"need_term_overlap:{overlap}")

    if recall_rank is not None:
        recall_score = 1.6 / (1.0 + 0.24 * max(0, recall_rank - 1))
        score += recall_score
        reasons.append(f"memory_router_rank:{recall_rank}")

    blob = _text(row.get("payload"))
    dependency_score = 0.0
    if owner_goal_ids and any(
        goal_id and goal_id in blob for goal_id in owner_goal_ids
    ):
        dependency_score += 1.15
        reasons.append("owner_goal_dependency")
    if owner_mission_ids and any(
        mission_id and mission_id in blob for mission_id in owner_mission_ids
    ):
        dependency_score += 1.05
        reasons.append("owner_mission_dependency")
    score += dependency_score

    anchor_overlap = len(terms & anchor_terms) if anchor_terms else 0
    if anchor_overlap:
        score += min(0.85, 0.15 * anchor_overlap)
        reasons.append(f"decision_anchor_overlap:{anchor_overlap}")

    source = str(row.get("source") or "")
    source_matches = _source_match(source, need.source_hints)
    semantic_evidence = lexical > 0 or recall_rank is not None or dependency_score > 0 or anchor_overlap > 0

    # Some compact current-state summaries are inherently needed for the
    # corresponding question. This prior is deliberately narrow: it does not
    # make every row in a hinted source relevant.
    current_prior = 0.0
    if bool(row.get("current")):
        if need.need_id == "objective" and source in {"goal", "mission", "commitment"}:
            current_prior = 0.75
        elif need.need_id in {"current_constraints", "internal_pressure"} and source in {
            "resource", "operational", "internal",
        }:
            current_prior = 0.7
        if current_prior:
            score += current_prior
            reasons.append("current_need_prior")

    if source_matches and (semantic_evidence or current_prior):
        score += 0.28
        reasons.append("source_matches_information_need")

    if bool(row.get("current")) and (semantic_evidence or current_prior):
        score += 0.12
        reasons.append("current_state")

    lowered = text.lower()
    if need.contradictory and (
        terms & _NEGATIVE_TERMS
        or any(
            marker in lowered
            for marker in ("failed", "failure", "blocked", "risk")
        )
    ) and (semantic_evidence or current_prior):
        score += 0.75
        reasons.append("negative_or_conflicting_evidence")

    if need.need_id in {"current_constraints", "internal_pressure"} and (
        terms & _RESOURCE_TERMS
    ) and (semantic_evidence or current_prior):
        score += 0.38
        reasons.append("resource_constraint")

    # Salience and confidence are tie-breakers/modulators, not relevance
    # generators. An unrelated high-importance memory should still score zero.
    if score > 0:
        importance = float(row.get("importance") or 0.0)
        confidence = float(row.get("confidence") or 0.0)
        score += 0.22 * importance + 0.12 * confidence
        if importance >= 0.85:
            reasons.append("high_importance")
        if confidence >= 0.85:
            reasons.append("high_confidence")

    return score * need.priority, reasons


def _dependency_tokens(row: Mapping[str, Any]) -> set[str]:
    tokens = set(str(item) for item in row.get("refs") or [] if str(item))
    tokens.update(str(item) for item in row.get("entities") or [] if str(item))
    tokens.update(str(item) for item in row.get("topics") or [] if str(item))
    payload = row.get("payload")
    if isinstance(payload, Mapping):
        for key in (
            "goal_id", "mission_id", "owner_goal_id", "external_mission_id",
            "experiment_id", "event_id", "outcome_id", "action_id",
        ):
            value = str(payload.get(key) or "")
            if value:
                tokens.add(value)
    return {item for item in tokens if len(item) >= 3}


def build_decision_context(
    *,
    prompt: str,
    decision_frame: Mapping[str, Any],
    owner_scope: Mapping[str, Any] | None,
    missions: Sequence[Mapping[str, Any]],
    local_candidates: Sequence[Mapping[str, Any]],
    state_root: Any = None,
    now: datetime | None = None,
    resource_pressure: Any = None,
    budget_chars: int = 22_000,
    max_facts: int = 28,
) -> dict[str, Any]:
    """Retrieve and select decision-changing context across all known sources."""

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    needs = plan_information_needs(
        prompt=prompt,
        decision_frame=decision_frame,
        owner_scope=owner_scope,
        missions=missions,
        resource_pressure=resource_pressure,
    )
    owner_goal_ids = {
        str((owner_scope or {}).get("goal_id") or ""),
        *[str(row.get("goal_id") or "") for row in missions],
    } - {""}
    owner_mission_ids = {
        str((owner_scope or {}).get("mission_id") or ""),
        *[str(row.get("mission_id") or "") for row in missions],
    } - {""}

    by_id: dict[str, dict[str, Any]] = {}
    for raw in local_candidates:
        row = dict(raw)
        cid = str(row.get("context_id") or "")
        if cid:
            by_id[cid] = row

    recall_ranks: dict[tuple[str, str], int] = {}
    memory_errors: list[str] = []
    memory_recall_count = 0
    if state_root is not None:
        try:
            router = MemoryRouter(state_root)
            for need in needs:
                try:
                    rows = router.recall(need.question, limit=14, now=current_time)
                except Exception as exc:
                    memory_errors.append(
                        f"{need.need_id}:{type(exc).__name__}"
                    )
                    continue
                for rank, memory in enumerate(rows, start=1):
                    row = canonical_memory_candidate(memory)
                    cid = str(row["context_id"])
                    by_id[cid] = row
                    recall_ranks[(need.need_id, cid)] = rank
                    memory_recall_count += 1
        except Exception as exc:
            memory_errors.append(f"router:{type(exc).__name__}")

    candidates = list(by_id.values())
    anchor_terms = _terms(
        " ".join([
            str(prompt or ""),
            str((owner_scope or {}).get("description") or ""),
            str((owner_scope or {}).get("topic") or ""),
            *[str(row.get("description") or "") for row in missions],
        ])
    )

    scored_by_need: dict[str, list[tuple[float, str, list[str]]]] = {}
    for need in needs:
        scored: list[tuple[float, str, list[str]]] = []
        for row in candidates:
            cid = str(row.get("context_id") or "")
            score, reasons = _rank_candidate(
                row,
                need,
                anchor_terms=anchor_terms,
                owner_goal_ids=owner_goal_ids,
                owner_mission_ids=owner_mission_ids,
                recall_rank=recall_ranks.get((need.need_id, cid)),
            )
            if score > 0.6:
                scored.append((score, cid, reasons))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        scored_by_need[need.need_id] = scored

    selected: dict[str, dict[str, Any]] = {}
    selected_chars = 0
    trace: dict[str, dict[str, Any]] = {}

    # Need-centric round-robin prevents one large source/section from consuming
    # the full window. Higher-priority needs get more passes.
    ordered_needs = sorted(needs, key=lambda item: item.priority, reverse=True)
    for pass_index in range(4):
        for need in ordered_needs:
            if len(selected) >= max_facts:
                break
            ranked = scored_by_need.get(need.need_id) or []
            if pass_index >= len(ranked):
                continue
            score, cid, reasons = ranked[pass_index]
            row = by_id[cid]
            row_chars = len(_text(row.get("payload"))) + len(str(row.get("text") or ""))
            if cid not in selected and selected_chars + row_chars > budget_chars:
                continue
            if cid not in selected:
                selected[cid] = row
                selected_chars += row_chars
            entry = trace.setdefault(
                cid,
                {"score": 0.0, "need_ids": [], "reasons": []},
            )
            entry["score"] = max(float(entry["score"]), round(score, 4))
            if need.need_id not in entry["need_ids"]:
                entry["need_ids"].append(need.need_id)
            for reason in reasons:
                if reason not in entry["reasons"]:
                    entry["reasons"].append(reason)

    # Dependency expansion: once a decision-changing fact is selected, recover
    # linked goal/mission/outcome/promise evidence even if lexical similarity is low.
    dependency_keys = set()
    for row in selected.values():
        dependency_keys.update(_dependency_tokens(row))
    if dependency_keys and len(selected) < max_facts:
        expansion: list[tuple[float, str, list[str]]] = []
        for row in candidates:
            cid = str(row.get("context_id") or "")
            if cid in selected:
                continue
            shared = dependency_keys & _dependency_tokens(row)
            if not shared:
                continue
            score = (
                1.8
                + 0.7 * min(3, len(shared))
                + 0.5 * float(row.get("importance") or 0.0)
                + 0.3 * float(row.get("confidence") or 0.0)
            )
            expansion.append(
                (score, cid, ["dependency_expansion:" + ",".join(sorted(shared)[:4])])
            )
        expansion.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for score, cid, reasons in expansion:
            if len(selected) >= max_facts:
                break
            row = by_id[cid]
            row_chars = len(_text(row.get("payload"))) + len(str(row.get("text") or ""))
            if selected_chars + row_chars > budget_chars:
                continue
            selected[cid] = row
            selected_chars += row_chars
            trace[cid] = {
                "score": round(score, 4),
                "need_ids": ["dependencies"],
                "reasons": reasons,
            }

    selected_rows = sorted(
        selected.values(),
        key=lambda row: (
            -float((trace.get(str(row.get("context_id"))) or {}).get("score") or 0.0),
            str(row.get("context_id") or ""),
        ),
    )
    facts = []
    for row in selected_rows:
        cid = str(row.get("context_id") or "")
        meta = trace.get(cid) or {}
        facts.append({
            "context_id": cid,
            "source": row.get("source"),
            "kind": row.get("kind"),
            "text": row.get("text"),
            "payload": row.get("payload"),
            "importance": row.get("importance"),
            "confidence": row.get("confidence"),
            "current": row.get("current"),
            "selection": {
                "score": meta.get("score"),
                "need_ids": meta.get("need_ids") or [],
                "reasons": meta.get("reasons") or [],
            },
        })

    selected_ids = {str(row.get("context_id") or "") for row in selected_rows}
    selected_sources = sorted(
        {str(row.get("source") or "") for row in selected_rows if row.get("source")}
    )
    rejected: list[dict[str, Any]] = []
    for row in candidates:
        cid = str(row.get("context_id") or "")
        if cid in selected_ids:
            continue
        best_score = 0.0
        best_need = ""
        best_reasons: list[str] = []
        for need in needs:
            matches = next(
                (
                    item
                    for item in scored_by_need.get(need.need_id) or []
                    if item[1] == cid
                ),
                None,
            )
            if matches is not None and float(matches[0]) > best_score:
                best_score = float(matches[0])
                best_need = need.need_id
                best_reasons = list(matches[2])
        rejected.append({
            "context_id": cid,
            "source": row.get("source"),
            "best_score": round(best_score, 4),
            "best_need_id": best_need or None,
            "reason": (
                "budget_or_lower_decision_utility"
                if best_score > 0.6
                else "below_decision_relevance_floor"
            ),
            "signals": best_reasons[:8],
        })
    rejected.sort(
        key=lambda item: (float(item.get("best_score") or 0.0), str(item.get("context_id") or "")),
        reverse=True,
    )
    return {
        "schema_version": "ferro-decision-context.v1",
        "information_needs": [need.as_dict() for need in needs],
        "selected_facts": facts,
        "candidate_count": len(candidates),
        "selected_count": len(facts),
        "excluded_count": max(0, len(candidates) - len(facts)),
        "selected_ids": sorted(selected_ids),
        "rejected_candidates": rejected[:24],
        "selected_sources": selected_sources,
        "selected_chars_estimate": selected_chars,
        "budget_chars": int(budget_chars),
        "memory_router": {
            "enabled": state_root is not None,
            "recalled_rows_across_needs": memory_recall_count,
            "errors": memory_errors[:12],
        },
    }


def selected_payloads(
    decision_context: Mapping[str, Any],
    source: str,
) -> list[Any]:
    rows = []
    for fact in decision_context.get("selected_facts") or []:
        if not isinstance(fact, Mapping) or str(fact.get("source") or "") != source:
            continue
        rows.append(fact.get("payload"))
    return rows



def extract_commitments(runtime: Any) -> list[dict[str, Any]]:
    social = getattr(runtime, "social", None)
    rows: list[dict[str, Any]] = []
    for item in list(getattr(social, "commitments", ()) or ()):
        state = str(getattr(getattr(item, "state", ""), "value", getattr(item, "state", "")) or "")
        if state in {"FULFILLED", "REVOKED"}:
            continue
        deadline = getattr(getattr(item, "deadline", None), "at", None)
        constraints = []
        for constraint in list(getattr(item, "constraints", ()) or ())[:12]:
            constraints.append({
                "constraint_id": str(getattr(constraint, "constraint_id", "") or ""),
                "forbidden_action_kinds": sorted(
                    str(value)
                    for value in (getattr(constraint, "forbidden_action_kinds", ()) or ())
                ),
                "forbidden_resources": sorted(
                    str(value)
                    for value in (getattr(constraint, "forbidden_resources", ()) or ())
                ),
                "applies_to_triggers": sorted(
                    str(value)
                    for value in (getattr(constraint, "applies_to_triggers", ()) or ())
                ),
                "reason": _clip(getattr(constraint, "reason", ""), 600),
                "evidence_refs": list(getattr(constraint, "evidence_refs", ()) or ())[:16],
            })
        rows.append({
            "commitment_id": str(getattr(item, "commitment_id", "") or ""),
            "counterpart_id": str(getattr(item, "counterpart_id", "") or ""),
            "statement": _clip(getattr(item, "statement", ""), 1200),
            "state": state,
            "visibility": str(
                getattr(
                    getattr(item, "visibility", ""),
                    "value",
                    getattr(item, "visibility", ""),
                )
                or ""
            ),
            "deadline": deadline.isoformat() if hasattr(deadline, "isoformat") else None,
            "constraints": constraints,
            "evidence_refs": list(getattr(item, "evidence_refs", ()) or ())[:24],
        })
    return rows


def build_ferro_decision_context(
    *,
    prompt: str,
    decision_frame: Mapping[str, Any],
    owner_scope: Mapping[str, Any] | None,
    mission_state: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    projection: Mapping[str, Any],
    resources: Mapping[str, Any],
    capabilities: Sequence[str],
    capability_beliefs: Sequence[Mapping[str, Any]],
    relationship_rows: Sequence[Mapping[str, Any]],
    commitments: Sequence[Mapping[str, Any]],
    life_experience: Sequence[Mapping[str, Any]],
    wake: Mapping[str, Any],
    behavioral_state: Mapping[str, Any],
    latest_plan: Mapping[str, Any] | None,
    state_root: Any,
    now: datetime,
    budget_chars: int,
) -> dict[str, Any]:
    local: list[dict[str, Any]] = []

    for index, row in enumerate(intents):
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "GOAL").upper()
        source = "goal" if kind == "GOAL" else "mission"
        identifier = str(
            row.get("goal_id") or row.get("mission_id") or f"{source}-{index}"
        )
        local.append(candidate(
            context_id=f"{source}:{identifier}",
            source=source,
            kind=kind,
            payload=dict(row),
            importance=float(row.get("priority") or 0.9),
            confidence=1.0,
            current=True,
            refs=[identifier, str(row.get("goal_id") or "")],
        ))

    for index, row in enumerate(list(mission_state.get("active") or [])):
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("mission_id") or f"mission-{index}")
        local.append(candidate(
            context_id=f"mission:{identifier}",
            source="mission",
            kind="MISSION",
            payload=dict(row),
            importance=0.98,
            confidence=1.0,
            current=True,
            refs=[identifier, str(row.get("goal_id") or "")],
        ))

    for index, row in enumerate(list(mission_state.get("recent_experiments") or [])):
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("experiment_id") or f"experiment-{index}")
        local.append(candidate(
            context_id=f"experiment:{identifier}",
            source="experiment",
            kind="MISSION_EXPERIMENT",
            payload=dict(row),
            importance=0.86,
            confidence=0.9,
            refs=[identifier, str(row.get("mission_id") or "")],
        ))

    for source, values in (
        ("interest", list(projection.get("interests") or [])),
        ("inquiry", list(projection.get("open_inquiries") or [])),
        ("belief", list(projection.get("beliefs") or [])),
        ("belief_change", list(projection.get("recent_belief_changes") or [])),
    ):
        for index, row in enumerate(values):
            if not isinstance(row, Mapping):
                continue
            identifier = str(
                row.get("inquiry_id")
                or row.get("proposition")
                or row.get("name")
                or f"{source}-{index}"
            )
            local.append(candidate(
                context_id=f"{source}:{identifier}",
                source=source,
                kind=source.upper(),
                payload=dict(row),
                importance=float(row.get("salience") or 0.7),
                confidence=float(row.get("confidence") or 0.75),
                refs=list(row.get("evidence_refs") or []),
            ))

    if isinstance(latest_plan, Mapping):
        local.append(candidate(
            context_id="plan:latest",
            source="plan",
            kind="CURRENT_PLAN",
            payload=dict(latest_plan),
            importance=0.92,
            confidence=0.9,
            current=True,
            refs=[
                str(latest_plan.get("owner_goal_id") or ""),
                str(latest_plan.get("external_mission_id") or ""),
            ],
        ))

    for index, topic in enumerate(
        list((projection.get("strategy_learning") or {}).get("topics") or [])
    ):
        local.append(candidate(
            context_id=f"strategy:{index}:{str(topic)[:80]}",
            source="strategy",
            kind="STRATEGY_TOPIC",
            payload={"topic": str(topic)},
            importance=0.62,
            confidence=0.75,
        ))

    for index, row in enumerate(life_experience):
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("event_id") or f"experience-{index}")
        local.append(candidate(
            context_id=f"experience:{identifier}",
            source="experience",
            kind="LIFE_EXPERIENCE",
            payload=dict(row),
            importance=min(
                1.0,
                max(0.55, 0.5 + 0.4 * abs(float(row.get("outcome") or 0.0))),
            ),
            confidence=float(row.get("confidence") or 0.75),
            refs=[identifier, *list(row.get("evidence_refs") or [])],
        ))

    if resources:
        local.append(candidate(
            context_id="resource:current",
            source="resource",
            kind="CURRENT_RESOURCES",
            payload=dict(resources),
            importance=0.95,
            confidence=1.0,
            current=True,
        ))

    for item in capabilities:
        value = str(item)
        if not value:
            continue
        local.append(candidate(
            context_id=f"capability:{value}",
            source="capability",
            kind="AVAILABLE_CAPABILITY",
            payload=value,
            text=value,
            importance=0.78,
            confidence=1.0,
            current=True,
        ))

    for row in capability_beliefs:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("capability") or "")
        local.append(candidate(
            context_id=f"capability_belief:{name}",
            source="capability_belief",
            kind="CAPABILITY_BELIEF",
            payload=dict(row),
            importance=0.84,
            confidence=float(row.get("confidence") or 0.7),
            current=True,
        ))

    for index, row in enumerate(relationship_rows):
        if not isinstance(row, Mapping):
            continue
        actor = str(row.get("actor") or index)
        local.append(candidate(
            context_id=f"relationship:{actor}",
            source="relationship",
            kind="RELATIONSHIP_STATE",
            payload=dict(row),
            importance=0.8,
            confidence=0.85,
            current=True,
            refs=[actor],
        ))

    for index, row in enumerate(commitments):
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("commitment_id") or index)
        local.append(candidate(
            context_id=f"commitment:{identifier}",
            source="commitment",
            kind="COMMITMENT",
            payload=dict(row),
            importance=0.98,
            confidence=1.0,
            current=True,
            refs=[identifier, *list(row.get("evidence_refs") or [])],
        ))

    neural = wake.get("neural_life")
    if isinstance(neural, Mapping):
        compact = {
            key: neural.get(key)
            for key in ("affect", "body", "drives", "intention", "last_authority_action")
            if neural.get(key) is not None
        }
        if compact:
            local.append(candidate(
                context_id="internal:neural",
                source="internal",
                kind="NEURAL_INTERNAL_STATE",
                payload=compact,
                importance=0.84,
                confidence=0.9,
                current=True,
            ))

    if behavioral_state:
        local.append(candidate(
            context_id="internal:behavioral",
            source="internal",
            kind="BEHAVIORAL_STATE",
            payload=dict(behavioral_state),
            importance=0.8,
            confidence=0.9,
            current=True,
        ))

    operational = {
        key: wake.get(key)
        for key in (
            "state", "attention", "resource_pressure", "last_cognition_at",
            "last_action_at", "pending_plan_count", "recent_stimulus_kinds",
        )
        if wake.get(key) is not None
    }
    if operational:
        local.append(candidate(
            context_id="operational:current",
            source="operational",
            kind="OPERATIONAL_STATE",
            payload=operational,
            importance=0.9,
            confidence=1.0,
            current=True,
        ))

    result = build_decision_context(
        prompt=prompt,
        decision_frame=decision_frame,
        owner_scope=owner_scope,
        missions=[
            row
            for row in list(mission_state.get("active") or [])
            if isinstance(row, Mapping)
        ],
        local_candidates=local,
        state_root=state_root,
        now=now,
        resource_pressure=wake.get("resource_pressure"),
        budget_chars=budget_chars,
        max_facts=24,
    )
    result["selected"] = {
        source: selected_payloads(result, source)
        for source in (
            "goal", "mission", "experiment", "interest", "inquiry", "belief",
            "belief_change", "plan", "strategy", "experience", "resource",
            "capability", "capability_belief", "relationship", "commitment",
            "internal", "operational",
        )
    }
    return result


__all__ = [
    "InformationNeed",
    "build_decision_context",
    "build_ferro_decision_context",
    "candidate",
    "canonical_memory_candidate",
    "extract_commitments",
    "plan_information_needs",
    "selected_payloads",
]
