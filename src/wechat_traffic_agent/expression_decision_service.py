"""Decide whether a completed inquiry deserves expression without forcing a post."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json

from .content_loop import _put
from .content_goal_contracts import CognitionDecision
from .learning_research import learning_receipt
from .x_social import XHistoryLedger, _expression_model_context


@dataclass(frozen=True)
class ExpressionRequest:
    folder: Path
    cycle: str
    selected: dict
    market_companion: dict | None
    mind_plan: dict
    inputs: dict
    learning: dict | None
    belief_decision: Any
    worldview_update: dict | None
    prediction: dict | None
    exploration: dict
    evidence_pool: dict
    focus_topic: str | None


@dataclass(frozen=True)
class ExpressionResult:
    decision: CognitionDecision | None
    early_result: dict | None


class ExpressionDecisionService:
    def __init__(self, *, state_root, mind, activity, structured,
                 normalize, has_public_trace, market_instruction, replace_receipt):
        self.state_root, self.mind, self.activity = state_root, mind, activity
        self.structured, self.normalize = structured, normalize
        self.has_public_trace, self.market_instruction = has_public_trace, market_instruction
        self.replace_receipt = replace_receipt
        self.expression_model_context = _expression_model_context

    def _context(self, request):
        path = request.folder / (
            'learning_cognition_context.json' if request.learning else 'cognition_context.json'
        )
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        recent = [
            {key: row.get(key) for key in ('recorded_at', 'text', 'expression_kind', 'x_url')}
            for row in XHistoryLedger(self.state_root).recent(limit=100)
            if row.get('event_type') == 'EXPRESSION'
        ][-20:]
        context = {
            'recent_public_expressions': recent, 'source_evidence': request.selected,
            'worldview_decision': request.belief_decision.model_dump(mode='json'),
            'worldview_update': request.worldview_update,
            'prediction': request.prediction, 'mind_goal': request.mind_plan.get('goal'),
            'current_mind': self.mind.public_projection(),
            'recent_titles': request.inputs['previous_titles'][-20:],
            'market_context_evidence': request.market_companion, 'learning': request.learning,
        }
        _put(path, context)
        return context

    def _cached(self, request):
        path = request.folder / (
            'learning_cognition.json' if request.learning else 'cognition.json'
        )
        if not path.exists():
            return path, None, False
        decision = CognitionDecision.model_validate_json(path.read_text(encoding='utf-8'))
        reconsider = bool(
            request.learning is not None and not request.learning['progressed']
            and self.has_public_trace(request.learning)
            and not decision.worth_saying and decision.expression_kind == 'NONE'
            and decision.what_changed == 'Research continued but did not yet add concrete understanding.'
        )
        return path, None if reconsider else decision, reconsider

    async def _choose(self, request, runtime, context):
        selected, learning_result, market_companion = (
            request.selected, request.learning, request.market_companion,
        )
        folder, cycle, inputs = request.folder, request.cycle, request.inputs
        worldview_update = request.worldview_update
        def validate_cognition(value):
            if value.worth_saying != (value.expression_kind != 'NONE'):
                raise ValueError('worth_saying must match expression_kind')
            changed = isinstance(worldview_update, dict) and worldview_update.get('status') in {'FORMED', 'REVISED'}
            if value.cognition_state == 'BELIEF_CHANGED' and not changed:
                return
        mission_instruction = (
            ' Bootstrap focus: this one cycle was deliberately pointed at a topic to establish initial output. '
            'Choose LONGFORM, RESEARCH_NOTE, METHOD_NOTE, THOUGHT, or NONE according to the actual information gain and evidence. '
            'Do not force a length or format merely because the cycle was bootstrapped.'
            if request.focus_topic else ''
        )
        market_instruction = '' if learning_result is not None else self.market_instruction(selected)
        if market_instruction and market_companion is not None:
            market_instruction += (
                ' Use the supplied market_context_evidence together with the primary source to form the current bounded market view. '
                'Do not make source-taxonomy or media-critique the main point unless the evidence is too weak to support any market interpretation. '
                'In what_matters, prefer the agent’s current interpretation plus the next observation that would confirm, weaken, or reverse it.'
            )
        cognition_decision = await self.structured(
            runtime,
            'DECIDE_EXPRESSION: Decide whether this completed cognition cycle contains a genuinely useful idea worth expressing now. '
            'The persistent agent thinks continuously and fulfills active daily publication commitments by investigating substantive questions. Do not relabel repetition as learning to fill the count. NONE is correct only when there is no meaningful information gain or useful public trace. '
            'LONGFORM is appropriate for a material belief formation/revision or substantial research result. RESEARCH_NOTE is appropriate when the evidence is real and the investigation itself is useful, but the durable worldview correctly remains unresolved. '
            'METHOD_NOTE is appropriate when a METHOD or TOOL source reveals a concrete tactic, workflow, open-source library, or research technique worth testing; present it as a candidate to verify, never as already adopted. '
            'THOUGHT is appropriate for a concise, evidence-grounded reflection or question from actual learning. '
            'Ferro researches its own interests and shares along the way. A belief formation or revision creates no publication obligation. '
            'Use learning.reasoning_steps to share an evidence-backed explanation: the observations, what follows from them, and limits. '
            'A useful intermediate calculation, distinction, counterexample or reason for rejecting an interpretation can be worth sharing before the question is answered. '
            'Do not discard this reasoning just because no final conclusion or belief change exists. A search diary or a bare promise to investigate is insufficient. '
            'Use the concrete learning_delta and observations, not another generic reminder that evidence is needed. '
            'Learning includes a new mechanism, implementation detail, comparison, counterexample or narrowed uncertainty, even if the broad inquiry remains open. '
            'The same evidence may support a concise post and a later illustrated explanation when the latter adds supported context or a useful framework. A new belief change or fresh source is not required for a useful new expression. Choose depth by evidence quality; padding or repeating a prior point is not useful. Compare recent_public_expressions before deciding there is additional public value. Do not use traffic demand as evidence that a thought is true. Do not invent a belief change. Return JSON only.'
            + mission_instruction + market_instruction + '\n'
            + json.dumps(self.expression_model_context(context), ensure_ascii=False),
            CognitionDecision, validate=validate_cognition,
        )
        return self.normalize(cognition_decision, worldview_update)

    async def decide(self, request: ExpressionRequest, runtime, *, enabled: bool):
        if not enabled:
            return ExpressionResult(None, None)
        path, decision, reconsider = self._cached(request)
        if decision is None:
            self.activity(
                request.cycle, 'DECIDE',
                'I’m deciding whether I actually have something worth saying',
                'A completed cognition cycle does not create a publication obligation.',
            )
            decision = await self._choose(request, runtime, self._context(request))
            if reconsider:
                self.replace_receipt(path, decision.model_dump(mode='json'))
            else:
                _put(path, decision.model_dump(mode='json'))
        if request.learning is not None:
            learning_receipt(request.folder, 'cognition.json', decision.model_dump(mode='json'))
        if decision.worth_saying:
            return ExpressionResult(decision, None)
        self.activity(request.cycle, 'DECIDE',
                      'I completed the thought without forcing a publication',
                      decision.rationale, status='completed')
        exploration = request.exploration
        selected = request.selected
        return ExpressionResult(decision, {
            'status': 'COGNITION_ONLY', 'cycle': request.cycle,
            'mind_plan': request.mind_plan, 'worldview_update': request.worldview_update,
            'prediction': request.prediction, 'cognition': decision.model_dump(mode='json'),
            'source_failures': exploration.get('source_failures',
                                              request.evidence_pool.get('failures', [])),
            'exploration': {
                'candidate_count': exploration.get('candidate_count', 0),
                'match_count': len(exploration.get('matches', [])),
                'selected_evidence_id': selected['evidence_id'],
                'selected_source_kind': selected['source_kind'],
                'evidence_refs': selected['evidence_refs'],
            },
        })
