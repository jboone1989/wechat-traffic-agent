"""Acquire counter-evidence or restrict a draft to lower-claim methodology."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .content_loop import _put
from .evidence_floor import EvidenceFloorPlan, planned_risk_cues


@dataclass(frozen=True)
class CounterEvidenceRequest:
    folder: Path
    cycle: str
    now: object
    selection: object
    selected: dict


@dataclass(frozen=True)
class CounterEvidenceResult:
    plan: EvidenceFloorPlan
    counter_payload: dict | None
    scope_downgraded: bool
    writing_requirement: str


class CounterEvidenceService:
    def __init__(self, *, explorer, activity):
        self.explorer, self.activity = explorer, activity

    async def _discover(self, request, runtime, cues):
        search = getattr(self.explorer, 'search_counter_evidence', None)
        filter_items = getattr(self.explorer, 'filter_counter_evidence', None)
        self.activity(
            request.cycle, 'DECIDE', 'I’m checking for counter-evidence',
            'This topic can make performance claims, so I am looking for contrary or limiting evidence before writing.',
        )
        pool = (await search(
            subject=request.selection.title + ' ' + request.selection.reader_question,
            now=request.now,
        ) if search is not None else {'items': [], 'failures': [], 'query': None})
        filtered = (await filter_items(
            runtime=runtime, primary=request.selected, candidates=pool['items'],
        ) if filter_items is not None else {
            'items': [], 'matches': [], 'candidate_count': 0,
        })
        if filtered['items']:
            match = filtered['matches'][0]
            return EvidenceFloorPlan(
                topic=request.selection.topic, required=True, trigger_cues=cues,
                status='SATISFIED', counter_query=pool.get('query'),
                counter_evidence=filtered['items'][0],
                counter_match_quote=match['evidence_quote'],
                counter_match_reason=match['reason'],
            )
        missing = EvidenceFloorPlan(
            topic=request.selection.topic, required=True, trigger_cues=cues,
            status='MISSING', counter_query=pool.get('query'),
        )
        _put(request.folder / 'evidence_floor_attempt.json', missing.model_dump(mode='json'))
        self.activity(
            request.cycle, 'DECIDE', 'I could not verify the stronger claim, so I’m narrowing the article',
            'I will write the useful methodological part and remove unsupported performance, prediction, or causal claims.',
            status='completed',
        )
        return EvidenceFloorPlan(
            topic=request.selection.topic, required=False, trigger_cues=cues,
            status='NOT_REQUIRED', counter_query=pool.get('query'),
        )

    async def _plan(self, request, runtime):
        path = request.folder / 'evidence_floor_plan.json'
        if path.exists():
            return EvidenceFloorPlan.model_validate_json(path.read_text(encoding='utf-8'))
        cues = planned_risk_cues(
            topic=request.selection.topic, title=request.selection.title,
            reader_question=request.selection.reader_question,
            gap=request.selection.gap, primary_evidence=request.selected,
        )
        plan = await self._discover(request, runtime, cues) if cues else EvidenceFloorPlan(
            topic=request.selection.topic, required=False, trigger_cues=(),
            status='NOT_REQUIRED',
        )
        _put(path, plan.model_dump(mode='json'))
        return plan

    async def prepare(self, request: CounterEvidenceRequest, runtime):
        plan = await self._plan(request, runtime)
        counter = plan.counter_evidence.model_dump(mode='json') if plan.counter_evidence else None
        downgraded = (request.folder / 'evidence_floor_attempt.json').exists() and not plan.required
        requirement = (
            ' Mandatory Evidence Floor: include exactly one paragraph beginning "Counter-evidence:" that '
            'directly addresses the supplied counter_evidence. Do not claim that source is independent; '
            'source independence is deliberately not evaluated by W3.' if plan.required else
            ' Counter-evidence for the stronger angle was not acquired. Narrow the article: do not claim '
            'outperformance, profitability, predictive power, improved returns, reduced risk, or causal '
            'performance effects. Explain methodology, verification, uncertainty, or limitations instead.'
            if downgraded else ''
        )
        return CounterEvidenceResult(plan, counter, downgraded, requirement)
