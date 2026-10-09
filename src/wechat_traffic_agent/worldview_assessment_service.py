"""Durable worldview updates and falsifiable predictions as one cognition port.

Only verified belief formation/revision may generate a prediction. Stored
decision receipts remain authoritative across restarts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import json

from .content_goal_contracts import WorldviewDecision
from .content_loop import _put
from .learning_research import learning_receipt
from .prospective_predictions import PredictionProposal


@dataclass(frozen=True)
class WorldviewRequest:
    folder: Path
    cycle: str
    now: object
    selection: object
    selected: dict
    mind_plan: dict
    learning: dict | None


@dataclass(frozen=True)
class WorldviewResult:
    decision: WorldviewDecision | None
    update: dict | None
    prediction: dict | None


class WorldviewAssessmentService:
    def __init__(self, *, mind, predictions, activity, structured,
                 decision_context, propose_prediction):
        self.mind = mind
        self.predictions = predictions
        self.activity = activity
        self.structured = structured
        self.decision_context = decision_context
        self.propose_prediction = propose_prediction

    async def _decide(self, request, runtime):
        folder, cycle, selection, selected = (
            request.folder, request.cycle, request.selection, request.selected,
        )
        learning = request.learning
        path = folder / ('learning_worldview_decision.json' if learning else 'worldview_decision.json')
        if path.exists():
            decision = WorldviewDecision.model_validate_json(path.read_text(encoding='utf-8'))
        else:
            current = self.mind.public_projection().get('beliefs', [])
            self.activity(
                cycle, 'DECIDE', 'I’m deciding what this evidence changes in my worldview',
                'I may form a bounded working view, revise an existing one, or defer if the evidence is not enough.',
            )
            prompt = (
                'FORM_WORLDVIEW: Decide whether this selected evidence justifies a durable working belief for the persistent agent. '
                'This is cognition, not article writing. DEFER is valid and preferred when the evidence is insufficient. '
                'A proposition must be a concise, testable world claim that can be revisited by future evidence; do not make it source-specific. '
                'If the evidence revises an existing proposition, copy that proposition EXACTLY rather than paraphrasing it. '
                'Compare semantic meaning, not wording. Repetition of a known idea is not a new belief or a revision. '
                'When learning.related_proposition is supplied, use that exact canonical proposition. '
                'A concrete discovery may be retained and shared without committing a general world claim. '
                'stance ranges from -1 (evidence argues strongly against the proposition) to +1 (strongly supports it). '
                'reliability is how fit this evidence is for updating that proposition, not a claim of truth. support is the evidence support used inside the inquiry. '
                'Separate observed facts from interpretation and do not invent facts beyond source_evidence. Return JSON only.\n'
                + json.dumps(self.decision_context(
                    topic=selection.topic, reader_question=selection.reader_question,
                    source_evidence=selected, current_beliefs=current,
                    mind_goal=request.mind_plan.get('goal'), learning=learning,
                ), ensure_ascii=False)
            )
            decision = await self.structured(runtime, prompt, WorldviewDecision)
        if learning is not None and learning['related_proposition'] is not None:
            decision = decision.model_copy(update={'proposition': learning['related_proposition']})
            _put(folder / 'worldview_semantic_binding.json', {
                'canonical_proposition': learning['related_proposition'],
                'learning_cycle': cycle,
            })
        if not path.exists():
            _put(path, decision.model_dump(mode='json'))
        return decision

    def _record(self, request, decision, record_belief):
        folder, cycle, selection = request.folder, request.cycle, request.selection
        path = folder / ('learning_worldview_update.json' if request.learning else 'worldview_update.json')
        goal = request.mind_plan.get('goal')
        goal = goal if isinstance(goal, dict) else {}
        inquiry_refs = list(goal.get('inquiry_refs') or ())
        origin = 'experience' if str(goal.get('origin') or '') == 'EXTERNAL_MISSION' else 'self_question'
        if path.exists():
            update = json.loads(path.read_text(encoding='utf-8'))
        else:
            update = record_belief(
                cycle=cycle, topic=selection.topic, question=selection.reader_question,
                source_evidence=request.selected, proposition=decision.proposition,
                stance=decision.stance, reliability=decision.reliability,
                support=decision.support,
                inquiry_id=str(inquiry_refs[0]) if inquiry_refs else None,
                inquiry_origin=origin, commit=decision.action == 'COMMIT',
            )
            _put(path, update)
        if request.learning is not None:
            learning_receipt(folder, 'worldview_decision.json', decision.model_dump(mode='json'))
            learning_receipt(folder, 'worldview_update.json', update)
        self.activity(
            cycle, 'LEARN',
            'I updated a long-term belief' if decision.action == 'COMMIT' else 'I kept the question open',
            decision.rationale, status='completed',
        )
        return update

    async def _predict(self, request, runtime, update):
        if not (isinstance(update, dict) and update.get('status') in {'FORMED', 'REVISED'}):
            return None
        folder, cycle, selected = request.folder, request.cycle, request.selected
        proposal_path = folder / 'prediction_proposal.json'
        prediction_path = folder / 'prediction.json'
        try:
            if proposal_path.exists():
                proposal = PredictionProposal.model_validate_json(
                    proposal_path.read_text(encoding='utf-8'),
                )
            else:
                proposal = await self.propose_prediction(
                    runtime=runtime, cycle=cycle, topic=request.selection.topic,
                    proposition=str(update['proposition']),
                    proposition_stance=float(update['stance']),
                    worldview_confidence=float(update['confidence']),
                    source_evidence=selected, now=request.now,
                )
                _put(proposal_path, proposal.model_dump(mode='json'))
            if proposal.action != 'RECORD':
                return None
            if prediction_path.exists():
                recorded = json.loads(prediction_path.read_text(encoding='utf-8'))
            else:
                prediction = self.predictions.record(
                    cycle=cycle, topic=request.selection.topic,
                    proposition=str(update['proposition']),
                    proposition_stance=float(update['stance']),
                    statement=str(proposal.statement), confidence=float(proposal.confidence),
                    created_at=request.now,
                    due_at=request.now + timedelta(hours=int(proposal.horizon_hours)),
                    verification_query=str(proposal.verification_query),
                    falsifier=str(proposal.falsifier),
                    source_evidence_refs=tuple(dict.fromkeys(
                        str(ref).strip() for ref in [
                            update.get('evidence_id'), selected['evidence_id'],
                            *selected.get('evidence_refs', ()),
                        ] if str(ref or '').strip()
                    )),
                )
                recorded = prediction.model_dump(mode='json')
                _put(prediction_path, recorded)
            self.activity(
                cycle, 'LEARN', 'I made a dated prediction that future evidence can judge',
                str(recorded.get('statement') or ''), status='completed',
            )
            return recorded
        except Exception as exc:
            _put(folder / 'prediction_proposal_error.json', {
                'status': 'PROPOSAL_ERROR', 'error_type': type(exc).__name__,
            })
            return None

    async def assess(self, request: WorldviewRequest, runtime, record_belief) -> WorldviewResult:
        if record_belief is None:
            return WorldviewResult(None, None, None)
        decision = await self._decide(request, runtime)
        update = self._record(request, decision, record_belief)
        prediction = await self._predict(request, runtime, update)
        return WorldviewResult(decision, update, prediction)
