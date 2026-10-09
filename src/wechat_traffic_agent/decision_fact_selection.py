"""In-memory decision fact selection independent of memory retrieval and LLM calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence
from .context_selection_policy import policy_ref as context_selection_policy_ref


@dataclass(frozen=True)
class SelectionRules:
    text: Callable
    dependency_tokens: Callable
    explicitly_stale: Callable


class DecisionFactSelector:
    def __init__(self, *, candidates, by_id, needs, scored_by_need, policy,
                 current_time, owner_goal_ids, owner_mission_ids, budget_chars,
                 max_facts, rules: SelectionRules):
        self.candidates, self.by_id, self.needs = candidates, by_id, needs
        self.scored_by_need, self.policy, self.current_time = scored_by_need, policy, current_time
        self.owner_goal_ids, self.owner_mission_ids = owner_goal_ids, owner_mission_ids
        self.budget_chars, self.max_facts = budget_chars, max_facts
        self.rules = rules
        self.selected, self.selected_chars, self.trace, self.uncovered = {}, 0, {}, set()

    def _initialize(self):
        needs, scored_by_need, policy = self.needs, self.scored_by_need, self.policy
        selected: dict[str, dict[str, Any]] = {}
        selected_chars = 0
        trace: dict[str, dict[str, Any]] = {}

        # V2 selection is marginal-utility set cover rather than fixed passes. A
        # candidate that answers several information needs is preferred, and once a
        # need has strong evidence it stops consuming more context merely because
        # more lexically-similar rows exist.
        ordered_needs = sorted(needs, key=lambda item: item.priority, reverse=True)
        need_by_id = {need.need_id: need for need in ordered_needs}
        score_lookup: dict[str, dict[str, tuple[float, list[str]]]] = {
            need.need_id: {
                cid: (float(score), list(reasons))
                for score, cid, reasons in (scored_by_need.get(need.need_id) or [])
            }
            for need in ordered_needs
        }
        top_score = {
            need.need_id: float((scored_by_need.get(need.need_id) or [(0.0, "", [])])[0][0])
            for need in ordered_needs
        }
        coverage_floor = {
            need.need_id: max(
                float(policy["coverage_floor_min"]),
                float(policy["coverage_ratio"]) * top_score[need.need_id],
            )
            for need in ordered_needs
            if top_score[need.need_id] > 0.0
        }
        uncovered = {
            need.need_id
            for need in ordered_needs
            if scored_by_need.get(need.need_id)
        }

        self.selected, self.selected_chars, self.trace, self.uncovered = (
            selected, selected_chars, trace, uncovered,
        )
        self.ordered_needs, self.need_by_id, self.score_lookup = (
            ordered_needs, need_by_id, score_lookup,
        )
        self.top_score, self.coverage_floor = top_score, coverage_floor

    def _anchors(self):
        candidates, by_id, policy, current_time = (
            self.candidates, self.by_id, self.policy, self.current_time,
        )
        owner_goal_ids, owner_mission_ids = self.owner_goal_ids, self.owner_mission_ids
        budget_chars, max_facts = self.budget_chars, self.max_facts
        selected, selected_chars, trace, uncovered = (
            self.selected, self.selected_chars, self.trace, self.uncovered,
        )
        needs, scored_by_need = self.needs, self.scored_by_need
        ordered_needs, need_by_id, score_lookup, top_score, coverage_floor = (
            self.ordered_needs, self.need_by_id, self.score_lookup,
            self.top_score, self.coverage_floor,
        )
        # Owner goal/mission identity is not optional retrieval evidence; it is the
        # decision anchor itself. Preserve at most one current goal and one current
        # mission so aggressive reranking cannot erase what the decision is about.
        mandatory_anchor_ids: list[str] = []
        for source, id_key, allowed_ids in (
            ("goal", "goal_id", owner_goal_ids),
            ("mission", "mission_id", owner_mission_ids),
        ):
            if not allowed_ids:
                continue
            for row in candidates:
                if str(row.get("source") or "") != source or not bool(row.get("current")):
                    continue
                payload = row.get("payload")
                value = (
                    str(payload.get(id_key) or "")
                    if isinstance(payload, Mapping)
                    else ""
                )
                if value and value in allowed_ids:
                    mandatory_anchor_ids.append(str(row.get("context_id") or ""))
                    break

        for row in candidates:
            if str(row.get("source") or "") != "plan" or not bool(row.get("current")):
                continue
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            plan_goal = (
                payload.get("goal")
                if isinstance(payload.get("goal"), Mapping)
                else {}
            )
            plan_goal_id = str(
                payload.get("owner_goal_id")
                or plan_goal.get("goal_id")
                or ""
            )
            plan_mission_id = str(payload.get("external_mission_id") or "")
            if (
                (plan_goal_id and plan_goal_id in owner_goal_ids)
                or (plan_mission_id and plan_mission_id in owner_mission_ids)
            ):
                mandatory_anchor_ids.append(str(row.get("context_id") or ""))
                break

        for cid in mandatory_anchor_ids:
            if not cid or cid in selected:
                continue
            row = by_id[cid]
            row_chars = len(self.rules.text(row.get("payload"))) + len(str(row.get("text") or ""))
            if selected_chars + row_chars > budget_chars:
                continue
            selected[cid] = row
            selected_chars += row_chars
            entry = trace.setdefault(
                cid,
                {"score": 0.0, "need_ids": [], "reasons": []},
            )
            objective_match = score_lookup.get("objective", {}).get(cid)
            if objective_match is not None:
                entry["score"] = max(float(entry["score"]), round(objective_match[0], 4))
                entry["need_ids"].append("objective")
                for reason in objective_match[1]:
                    if reason not in entry["reasons"]:
                        entry["reasons"].append(reason)
            entry["reasons"].append("mandatory_owner_scope_anchor")
        if mandatory_anchor_ids:
            uncovered.discard("objective")

        self.selected_chars, self.uncovered = selected_chars, uncovered

    def _cover(self):
        candidates, by_id, policy, current_time = (
            self.candidates, self.by_id, self.policy, self.current_time,
        )
        owner_goal_ids, owner_mission_ids = self.owner_goal_ids, self.owner_mission_ids
        budget_chars, max_facts = self.budget_chars, self.max_facts
        selected, selected_chars, trace, uncovered = (
            self.selected, self.selected_chars, self.trace, self.uncovered,
        )
        needs, scored_by_need = self.needs, self.scored_by_need
        ordered_needs, need_by_id, score_lookup, top_score, coverage_floor = (
            self.ordered_needs, self.need_by_id, self.score_lookup,
            self.top_score, self.coverage_floor,
        )
        while uncovered and len(selected) < max_facts:
            best: tuple[float, float, str, list[str]] | None = None
            for row in candidates:
                cid = str(row.get("context_id") or "")
                if not cid or cid in selected:
                    continue
                matched: list[str] = []
                marginal = 0.0
                strongest = 0.0
                for need_id in uncovered:
                    match = score_lookup.get(need_id, {}).get(cid)
                    if match is None:
                        continue
                    score = float(match[0])
                    reasons = list(match[1])
                    if score < coverage_floor.get(need_id, 0.78):
                        continue
                    # A need is only considered satisfied by the right kind of
                    # evidence. This prevents a goal/mission row from accidentally
                    # satisfying "past outcomes" merely because it repeats the same
                    # project words.
                    typed_evidence = (
                        "source_matches_information_need" in reasons
                        or any(reason.startswith("memory_router_rank:") for reason in reasons)
                        or "current_need_prior" in reasons
                    )
                    if not typed_evidence:
                        continue
                    peak = max(0.001, top_score.get(need_id, score))
                    normalized = min(1.0, score / peak)
                    need = need_by_id[need_id]
                    marginal += need.priority * (0.7 + 0.3 * normalized)
                    strongest = max(strongest, score)
                    matched.append(need_id)
                if not matched:
                    continue
                row_chars = len(self.rules.text(row.get("payload"))) + len(str(row.get("text") or ""))
                if selected and selected_chars + row_chars > budget_chars:
                    continue
                # Multi-need coverage is valuable; large rows pay a small tax so a
                # compact fact wins when decision utility is otherwise equivalent.
                utility = (
                    marginal
                    + float(policy["multi_need_bonus"]) * max(0, len(matched) - 1)
                    - float(policy["size_tax"]) * min(1.0, row_chars / max(1, budget_chars))
                )
                item = (utility, strongest, cid, matched)
                if best is None or item[:3] > best[:3]:
                    best = item

            if best is None:
                break

            _, _, cid, matched = best
            row = by_id[cid]
            row_chars = len(self.rules.text(row.get("payload"))) + len(str(row.get("text") or ""))
            if selected_chars + row_chars > budget_chars:
                break
            selected[cid] = row
            selected_chars += row_chars
            entry = trace.setdefault(
                cid,
                {"score": 0.0, "need_ids": [], "reasons": []},
            )
            for need_id in matched:
                score, reasons = score_lookup[need_id][cid]
                entry["score"] = max(float(entry["score"]), round(score, 4))
                if need_id not in entry["need_ids"]:
                    entry["need_ids"].append(need_id)
                for reason in reasons:
                    if reason not in entry["reasons"]:
                        entry["reasons"].append(reason)
                uncovered.discard(need_id)
            entry["reasons"].append(
                "marginal_need_coverage:" + ",".join(sorted(matched))
            )

        self.selected_chars, self.uncovered = selected_chars, uncovered

    def _expand(self):
        candidates, by_id, policy, current_time = (
            self.candidates, self.by_id, self.policy, self.current_time,
        )
        owner_goal_ids, owner_mission_ids = self.owner_goal_ids, self.owner_mission_ids
        budget_chars, max_facts = self.budget_chars, self.max_facts
        selected, selected_chars, trace, uncovered = (
            self.selected, self.selected_chars, self.trace, self.uncovered,
        )
        needs, scored_by_need = self.needs, self.scored_by_need
        ordered_needs, need_by_id, score_lookup, top_score, coverage_floor = (
            self.ordered_needs, self.need_by_id, self.score_lookup,
            self.top_score, self.coverage_floor,
        )
        # Dependency expansion is intentionally narrow. Owner goal/mission IDs are
        # too broad to justify pulling every related record after the information
        # needs were already satisfied. Expansion therefore requires a more specific
        # shared causal/evidence key, and is capped to a few facts.
        dependency_keys = set()
        for row in selected.values():
            dependency_keys.update(self.rules.dependency_tokens(row))
        generic_dependency_keys = owner_goal_ids | owner_mission_ids
        specific_dependency_keys = dependency_keys - generic_dependency_keys
        if dependency_keys and len(selected) < max_facts:
            expansion: list[tuple[float, str, list[str]]] = []
            generic_evidence_sources = {
                "experience", "memory", "commitment", "relationship", "experiment",
                "belief_change", "capability_belief",
            }
            for row in candidates:
                cid = str(row.get("context_id") or "")
                if cid in selected:
                    continue
                if bool(row.get("_temporal_shadowed")) or self.rules.explicitly_stale(
                    row, now=current_time
                ):
                    continue
                row_tokens = self.rules.dependency_tokens(row)
                specific_shared = specific_dependency_keys & row_tokens
                generic_shared = generic_dependency_keys & row_tokens
                if not specific_shared and not generic_shared:
                    continue
                source = str(row.get("source") or "")
                # A bare owner goal/mission link is allowed to recover only evidence
                # rows, never another arbitrary state row. The expansion cap below
                # keeps this from becoming a goal-wide dump.
                if not specific_shared and source not in generic_evidence_sources:
                    continue
                shared = specific_shared or generic_shared
                score = (
                    (1.15 if specific_shared else 0.82)
                    + 0.55 * min(3, len(shared))
                    + 0.35 * float(row.get("importance") or 0.0)
                    + 0.2 * float(row.get("confidence") or 0.0)
                )
                expansion.append(
                    (score, cid, ["dependency_expansion:" + ",".join(sorted(shared)[:4])])
                )
            expansion.sort(key=lambda item: (item[0], item[1]), reverse=True)
            expansion_limit = min(2, max(1, len(ordered_needs) // 3))
            added = 0
            for score, cid, reasons in expansion:
                if len(selected) >= max_facts or added >= expansion_limit:
                    break
                row = by_id[cid]
                row_chars = len(self.rules.text(row.get("payload"))) + len(str(row.get("text") or ""))
                if selected_chars + row_chars > budget_chars:
                    continue
                selected[cid] = row
                selected_chars += row_chars
                added += 1
                trace[cid] = {
                    "score": round(score, 4),
                    "need_ids": ["dependencies"],
                    "reasons": reasons,
                }

        self.selected_chars, self.uncovered = selected_chars, uncovered

    def _rejected_rows(self, selected_ids):
        candidates, needs, scored_by_need, policy = (
            self.candidates, self.needs, self.scored_by_need, self.policy,
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
                    if best_score > float(policy["relevance_floor"])
                    else "below_decision_relevance_floor"
                ),
                "signals": best_reasons[:8],
            })
        rejected.sort(
            key=lambda item: (float(item.get("best_score") or 0.0), str(item.get("context_id") or "")),
            reverse=True,
        )
        return rejected

    def _result(self, *, state_root, memory_recall_count, memory_errors):
        candidates, by_id, policy, current_time = (
            self.candidates, self.by_id, self.policy, self.current_time,
        )
        owner_goal_ids, owner_mission_ids = self.owner_goal_ids, self.owner_mission_ids
        budget_chars, max_facts = self.budget_chars, self.max_facts
        selected, selected_chars, trace, uncovered = (
            self.selected, self.selected_chars, self.trace, self.uncovered,
        )
        needs, scored_by_need = self.needs, self.scored_by_need
        ordered_needs, need_by_id, score_lookup, top_score, coverage_floor = (
            self.ordered_needs, self.need_by_id, self.score_lookup,
            self.top_score, self.coverage_floor,
        )
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
        rejected = self._rejected_rows(selected_ids)
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
            "selection_policy": dict(policy),
            "selection_policy_ref": context_selection_policy_ref(policy),
            "need_coverage": {
                "covered": sorted(
                    {
                        need_id
                        for meta in trace.values()
                        for need_id in list(meta.get("need_ids") or [])
                        if need_id != "dependencies"
                    }
                ),
                "uncovered": sorted(uncovered),
            },
            "memory_router": {
                "enabled": state_root is not None,
                "recalled_rows_across_needs": memory_recall_count,
                "errors": memory_errors[:12],
            },
        }

    def select(self, *, state_root, memory_recall_count, memory_errors):
        self._initialize()
        self._anchors()
        self._cover()
        self._expand()
        return self._result(
            state_root=state_root, memory_recall_count=memory_recall_count,
            memory_errors=memory_errors,
        )
