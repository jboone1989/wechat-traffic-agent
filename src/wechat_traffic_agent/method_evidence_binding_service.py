"""Bind public method notes to the exact method/tool evidence that supports them.

Cognition may choose a METHOD_NOTE, but publication is withheld unless the
method itself was observed in the current cycle or verified by research.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .content_goal_contracts import EvidenceSelection
from .content_loop import _put


@dataclass(frozen=True)
class MethodBindingRequest:
    folder: Path
    cycle: str
    selection: EvidenceSelection
    selected: dict
    related: list
    cognition: object | None
    learning: dict | None
    mind_plan: dict
    world_payload: dict | None
    snapshot: dict
    recent_titles: list


@dataclass(frozen=True)
class MethodBindingResult:
    selection: EvidenceSelection
    selected: dict
    related: list
    early_result: dict | None
    authority: str | None


class MethodEvidenceBindingService:
    def __init__(self, *, activity, structured, research_supports,
                 title_key, repair_related, website_view):
        self.activity = activity
        self.structured = structured
        self.research_supports = research_supports
        self.title_key = title_key
        self.repair_related = repair_related
        self.website_view = website_view

    @staticmethod
    def _observed_methods(request):
        topics = {
            str(row.get("evidence_id")): str(row.get("topic") or "")
            for row in (request.mind_plan.get("world_observations") or [])
            if isinstance(row, dict) and row.get("learning_kind") in {"METHOD", "TOOL"}
            and row.get("evidence_id")
        }
        methods = {}
        for row in ((request.world_payload or {}).get("items") or []):
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("evidence_id") or "")
            if evidence_id in topics and row.get("learning_kind") in {"METHOD", "TOOL"}:
                methods[evidence_id] = {"evidence": row, "topic": topics[evidence_id]}
        return methods

    async def _choose_method(self, request, runtime, methods):
        path = request.folder / "expression_binding.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return EvidenceSelection.model_validate(payload["selection"])
        def validate(value):
            entry = methods.get(value.evidence_id)
            if entry is None:
                raise ValueError("expression evidence must be an observed METHOD/TOOL item from this cycle")
            if value.topic != entry["topic"]:
                raise ValueError("method expression topic must match the persisted world observation")
            if self.title_key(value.title) in {self.title_key(t) for t in request.recent_titles}:
                raise ValueError("method expression title duplicates existing content")
        selection = await self.structured(
            runtime,
            "BIND_METHOD_EXPRESSION: The cognition decided a METHOD_NOTE is worth saying, but the earlier Website selection is about a different source. "
            "Bind the public method note to the exact METHOD/TOOL evidence that supports the supplied cognition. Use an exact evidence_id and its exact persisted topic. "
            "The method/tool is DISCOVERED only: frame the reader question and gap around how to test it before adoption. Do not treat it as already learned, installed, or true. "
            "Choose related_ids only as optional navigation context. Return JSON only.\n"
            + json.dumps({
                "cognition": request.cognition.model_dump(mode="json"),
                "method_evidence": [
                    entry["evidence"] | {"persisted_topic": entry["topic"]}
                    for entry in methods.values()
                ],
                "website": self.website_view(request.snapshot),
                "recent_titles": request.recent_titles[-100:],
            }, ensure_ascii=False),
            EvidenceSelection, validate=validate,
        )
        selection = self.repair_related(selection, request.snapshot)
        _put(path, {
            "schema_version": "content-agent-expression-binding.v1",
            "reason": "METHOD_NOTE_REBOUND_TO_OBSERVED_METHOD_EVIDENCE",
            "original_evidence_id": request.selected.get("evidence_id"),
            "selection": selection.model_dump(mode="json"),
        })
        return selection

    async def bind(self, request: MethodBindingRequest, runtime) -> MethodBindingResult:
        selected, selection, related = request.selected, request.selection, request.related
        researched = self.research_supports(request.learning, selected)
        if request.cognition is not None and request.cognition.expression_kind == "METHOD_NOTE" and researched:
            _put(request.folder / "research_expression_binding.json", {
                "reason": "METHOD_NOTE_SUPPORTED_BY_VERIFIED_RESEARCH",
                "evidence_id": selected["evidence_id"], "learning_cycle": request.learning["cycle"],
                "facts": request.learning["facts"],
            })
        if (request.cognition is None or request.cognition.expression_kind != "METHOD_NOTE"
                or selected.get("learning_kind") in {"METHOD", "TOOL"} or researched):
            return MethodBindingResult(selection, selected, related, None, None)
        methods = self._observed_methods(request)
        if not methods:
            self.activity(request.cycle, "DECIDE",
                          "I found a method idea but cannot bind it to evidence",
                          "The method thought is not being published because no METHOD/TOOL evidence observed in this cycle can be proven to support it.",
                          status="stopped")
            return MethodBindingResult(selection, selected, related, {
                "status": "HELD_BY_EXPRESSION_EVIDENCE", "cycle": request.cycle,
                "reason": "METHOD_NOTE_WITHOUT_OBSERVED_METHOD_EVIDENCE",
                "cognition": request.cognition.model_dump(mode="json"),
                "selected_evidence_id": selected.get("evidence_id"),
            }, None)
        expression = await self._choose_method(request, runtime, methods)
        entry = methods.get(expression.evidence_id)
        if entry is None or expression.topic != entry["topic"]:
            raise ValueError("persisted method expression binding no longer matches observed evidence")
        selected = entry["evidence"]
        related = [row["artifact"] for row in request.snapshot["articles"]
                   if row["artifact"]["artifact_id"] in expression.related_ids]
        self.activity(request.cycle, "DECIDE",
                      "I rebound the method note to the evidence that caused it",
                      f"{expression.title} now cites {selected.get('title') or expression.evidence_id} instead of the unrelated Website fallback source.",
                      status="completed")
        return MethodBindingResult(expression, selected, related, None, "COGNITION_EVIDENCE_REBIND")
