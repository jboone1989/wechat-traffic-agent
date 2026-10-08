"""A bounded research inquiry that commits interest, learns, then settles outcomes.

Neural interest commitments are synchronous (the result changes the next
decision). The research service is independent of the content cycle runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .content_loop import _put
from .learning_research import investigate_learning, learning_receipt
from .interest_learning import learning_reward, topic_contexts


@dataclass(frozen=True)
class InquiryRequest:
    folder: Path
    cycle: str
    now: object
    selection: object
    selected: dict
    evidence: list
    focus: object
    memory: object
    mind_plan: dict
    exploration: dict


@dataclass(frozen=True)
class InquiryResult:
    selection: object
    selected: dict
    learning: dict
    early_result: dict | None


class LearningInquiryService:
    def __init__(self, *, state_root, explorer, mind, neural_runtime,
                 activity, has_public_trace):
        self.state_root = state_root
        self.explorer = explorer
        self.mind = mind
        self.neural_runtime = neural_runtime
        self.activity = activity
        self.has_public_trace = has_public_trace

    def _commit(self, request):
        folder, cycle, now = request.folder, request.cycle, request.now
        path = folder / 'interest_study_commitment.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        if (folder / 'learning_result.json').exists():
            receipt = {'status': 'PRIOR_RESULT_NO_PRECOMMIT'}
        elif self.neural_runtime is not None:
            features = topic_contexts(
                self.state_root, [request.selection.topic],
                self.mind.public_projection() if self.mind is not None else {},
            )[request.selection.topic]
            receipt = self.neural_runtime.commit_interest_study(
                cycle=cycle, topic=request.selection.topic,
                features=features, now=now,
            )
        else:
            receipt = {'status': 'NO_NEURAL_OWNER'}
        _put(path, receipt)
        return receipt

    def _settle(self, request, learning, commitment):
        path = request.folder / 'interest_learning.json'
        if path.exists():
            return
        feedback = learning_reward(learning)
        neural = (
            self.neural_runtime.settle_interest_study(
                cycle=request.cycle, topic=request.selection.topic,
                outcome_id='interest-learning:' + request.cycle,
                utility=feedback['utility'], now=request.now,
            )
            if self.neural_runtime is not None
            and commitment.get('status') in {'COMMITTED', 'ALREADY_COMMITTED'}
            else {'status': 'NO_STUDY_COMMITMENT'}
        )
        mind = (
            self.mind.observe_learning_interest(
                cycle=request.cycle, topic=request.selection.topic,
                reward=feedback, evidence_refs=feedback['source_evidence_ids'],
            )
            if self.mind is not None and hasattr(self.mind, 'observe_learning_interest')
            else {'status': 'NO_MIND'}
        )
        _put(path, {'feedback': feedback, 'neural': neural, 'mind': mind})

    def _early_result(self, request, learning):
        if learning['progressed'] or self.has_public_trace(learning):
            return None
        cognition = {
            'cognition_state': 'INVESTIGATING', 'worth_saying': False,
            'expression_kind': 'NONE',
            'what_changed': 'Research continued but did not yet add concrete understanding.',
            'what_matters': learning['next_question'],
            'belief_proposition': learning['related_proposition'],
            'rationale': learning['rationale'],
        }
        learning_receipt(request.folder, 'cognition.json', cognition)
        self.activity(request.cycle, 'LEARN',
                      'I continued the inquiry rather than repeat a known thought',
                      learning['next_question'], status='completed')
        return {
            'status': 'COGNITION_ONLY', 'cycle': request.cycle,
            'mind_plan': request.mind_plan, 'cognition': cognition,
            'learning': learning, 'next_research_query': learning['next_query'],
            'source_failures': [
                *request.exploration.get('source_failures', []),
                *learning['source_failures'],
            ],
        }

    async def execute(self, request: InquiryRequest, runtime) -> InquiryResult:
        commitment = self._commit(request)
        learning = await investigate_learning(
            runtime=runtime, explorer=self.explorer, folder=request.folder,
            state_root=self.state_root, topic=request.selection.topic,
            focus=request.focus, memory=request.memory,
            evidence=request.evidence, selected=request.selected, now=request.now,
        )
        self._settle(request, learning, commitment)
        selected = next(row for row in learning['sources']
                        if row['evidence_id'] == learning['evidence_id'])
        selection = request.selection.model_copy(update={
            'evidence_id': selected['evidence_id'],
            'reader_question': request.focus.question,
        })
        return InquiryResult(
            selection, selected, learning, self._early_result(request, learning),
        )
