"""Grounded draft generation with explicit inputs and independently tested ports.

The writer owns brief compilation, bounded topic signals and the first
lower-claim rewrite; verification and publication are separate services.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import json
import re

from .content_loop import _put
from .content_service import ContentAgent
from .content_artifact import ContentKind
from .evidence_floor import artifact_risk_cues
from .models import TopicSignal
from .content_strategy import strategy_instruction
from .trading_draft_worker import default_trading_research_audience
from .website_experiment import experiment_compliance


@dataclass(frozen=True)
class DraftGenerationPolicies:
    compact_worldview_learning: Callable
    generation_strategy_receipt: Callable
    compact_floor: Callable
    compact_evidence: Callable
    compact_related: Callable
    market_instruction: Callable
    title_key: Callable


@dataclass(frozen=True)
class DraftGenerationRequest:
    folder: Path
    cycle: str
    inputs: dict
    fingerprint: str
    selection: Any
    selected: dict
    snapshot: dict
    related: list
    market_companion: dict | None
    counter_payload: dict | None
    floor_plan: Any
    floor_requirement: str
    scope_downgraded: bool
    expression_kind: str
    is_short_note: bool
    minimum_words: int | None
    market_pulse: bool
    social_style_context: dict
    lesson_payload: dict
    technique_requirements: dict
    experiment: Any
    experiment_payload: dict | None
    experiment_requirement: str | None
    cognition_decision: Any
    belief_decision: Any
    worldview_update: dict | None
    prediction_record: dict | None
    learning_result: dict | None
    strategy: Any
    strategy_payload: dict
    mind_strategy_context: dict | None
    existing: list


@dataclass(frozen=True)
class DraftGenerationResult:
    artifact: Any
    brief: dict
    topics: list
    experiment_check: dict | None
    text: str
    perspective_expression: bool


class DraftGenerationService:
    def __init__(self, *, base_url: str, activity: Callable, policies: DraftGenerationPolicies):
        self.base_url, self.activity, self.policies = base_url, activity, policies

    def _brief(self, request):
        (selection, selected, related, market_companion, counter_payload,
         floor_plan, floor_requirement, social_style_context, experiment_payload,
         cognition_decision, belief_decision, worldview_update, prediction_record,
         learning_result, content_strategy, mind_strategy_context, lesson_payload,
         technique_requirements, expression_kind, is_short_note, minimum_words,
         market_pulse, experiment_requirement) = (
            request.selection, request.selected, request.related,
            request.market_companion, request.counter_payload, request.floor_plan,
            request.floor_requirement, request.social_style_context,
            request.experiment_payload, request.cognition_decision,
            request.belief_decision, request.worldview_update, request.prediction_record,
            request.learning_result, request.strategy, request.mind_strategy_context,
            request.lesson_payload, request.technique_requirements,
            request.expression_kind, request.is_short_note, request.minimum_words,
            request.market_pulse, request.experiment_requirement,
        )
        expression_context = ({
            'learning': self.policies.compact_worldview_learning(learning_result),
            'agent_cognition': cognition_decision.model_dump(mode='json'),
            'worldview_decision': belief_decision.model_dump(mode='json'),
            'worldview_update': worldview_update,
            'dated_prediction': prediction_record,
            'voice_contract': {
                'identity': 'persistent Content Agent',
                'facts': 'Attribute observed facts to supplied sources; do not narrate sourced facts as personal memory.',
                'interpretation': 'Use first person for the agent’s own interpretation, judgment, uncertainty, and belief change.',
                'epistemic_honesty': 'Say what would weaken or change the view when material; never imply certainty that the stored belief does not have.',
                'style': 'Write like an expert expressing a considered view, not an academic paper, generic explainer, or institutional press release.',
            },
        } if cognition_decision is not None else None)
        generation_strategy = self.policies.generation_strategy_receipt(
            content_strategy,
            mind_strategy_context,
        )
        brief = {'selection': selection.model_dump(mode='json'), 'learned_techniques': lesson_payload,
                 'technique_requirements': technique_requirements,
                 'social_style_context': social_style_context,
                 'prospective_experiment': experiment_payload, 'content_strategy': generation_strategy,
                 'evidence_floor': self.policies.compact_floor(floor_plan),
                 'agent_expression_context': expression_context,
                 'source_material': {
                     'evidence': self.policies.compact_evidence(selected),
                     'market_context_evidence': self.policies.compact_evidence(
                         market_companion
                     ),
                     'counter_evidence': self.policies.compact_evidence(
                         counter_payload
                     ),
                     'existing_articles': self.policies.compact_related(
                         related
                     ),
                 },
                 'writing_requirements': (
                     (f'Write a concise {expression_kind.lower().replace("_", " ")}. Do not pad it to reach a length target; stop when the thought is complete. ' if is_short_note else f'Write at least {minimum_words} words. ')
                     + ('For a research note, clearly state what is known, what remains unresolved, and what evidence would resolve it. ' if expression_kind == 'RESEARCH_NOTE' else '')
                     + ('For a method note, describe the discovered method/tool as a learning candidate, why it may matter, and how this agent would test it before adoption. Do not claim it has already been learned or installed. ' if expression_kind == 'METHOD_NOTE' else '')
                     + ('For a thought, render a post-like note rather than an article: use one compact title, at most two short sections, and no more than three short paragraphs total. Put the immediate observation and the agent’s bounded view first; add what to watch or what would change the view only when useful. No formal introduction or conclusion. ' if expression_kind == 'THOUGHT' else '')
                     + ('Use social_style_context only for structure and pacing; never copy source wording, persona, claims, or certainty. ' if social_style_context.get('observations') else '')
                     + (self.policies.market_instruction(selected) + ' ' if market_pulse else '')
                     + 'Share the concrete learning discovery and question being pursued. Preserve source-supported mechanisms, names, comparisons or implementation details; do not reduce them to a generic reminder that evidence is needed. '
                     + 'Explain one mechanism, separate reported facts and interpretation. '
                     + 'No personal investment advice, fabricated numbers, private details, or unstated predictive claims. '
                     'Apply each technique listed in technique_requirements.required_indices visibly. '
                     'Do not force a deferred technique when it conflicts with the prospective experiment hard constraint. '
                     'Attribute factual claims to the selected source. No long verbatim source copying. '
                     'When agent_expression_context is present, make that cognition the reason the piece exists: express the agent’s own view in first person while keeping source facts attributed. '
                     + 'Do not hide a belief change behind neutral encyclopedia prose, and do not invent stronger conviction than the stored worldview update supports. If agent_expression_context contains a dated_prediction, it may be stated only as the agent’s explicit forecast with its stored confidence/horizon/falsifier, never as an observed fact. ')
                     + strategy_instruction(content_strategy)
                     + floor_requirement + ((' ' + experiment_requirement) if experiment_requirement else '')}
        return brief, generation_strategy

    def _topics(self, request):
        selection, selected, market_companion, counter_payload = (
            request.selection, request.selected, request.market_companion,
            request.counter_payload,
        )
        snapshot, inputs = request.snapshot, request.inputs
        observed_at = datetime.fromisoformat(selected.get('observed_at') or inputs['now'])
        topics = [TopicSignal(
            title=selection.title,
            summary=selected['summary'][:500],
            source=selected['source'][:80],
            source_url=selected.get('url'),
            source_revision=selected['source_revision'],
            observed_at=observed_at,
            evidence_refs=tuple(selected['evidence_refs']),
        )]
        if market_companion is not None:
            companion_observed = datetime.fromisoformat(market_companion.get('observed_at') or inputs['now'])
            topics.append(TopicSignal(
                title=market_companion['title'][:300], summary=market_companion['summary'][:500],
                source=market_companion['source'][:80], source_url=market_companion.get('url'),
                source_revision=market_companion['source_revision'], observed_at=companion_observed,
                evidence_refs=tuple(market_companion['evidence_refs']),
            ))
        if counter_payload is not None:
            counter_observed = datetime.fromisoformat(counter_payload.get('observed_at') or inputs['now'])
            topics.append(TopicSignal(
                title='Counter-evidence: ' + counter_payload['title'][:200],
                summary=counter_payload['summary'][:500], source=counter_payload['source'][:80],
                source_url=counter_payload.get('url'), source_revision=counter_payload['source_revision'],
                observed_at=counter_observed, evidence_refs=tuple(counter_payload['evidence_refs']),
            ))
        for row in snapshot['articles']:
            if row['artifact']['artifact_id'] in selection.related_ids:
                topics.append(TopicSignal(title=selection.title, summary=row['artifact']['summary'],
                    source='content-website', source_url=self.base_url.rstrip('/') + row['route'],
                    source_revision=row['content_sha256'],
                    evidence_refs=('content-artifact:' + row['artifact']['artifact_id'] + ':sha256:' + row['content_sha256'],)))
        return topics

    async def _compose(self, request, runtime, brief, topics, generation_strategy):
        folder, cycle, inputs = request.folder, request.cycle, request.inputs
        (selection, strategy_payload, mind_strategy_context, fingerprint, is_short_note,
         scope_downgraded, experiment, cognition_decision, existing) = (
            request.selection, request.strategy_payload, request.mind_strategy_context,
            request.fingerprint, request.is_short_note, request.scope_downgraded,
            request.experiment, request.cognition_decision, request.existing,
        )
        self.activity(cycle, 'CREATE', 'I’m writing the draft',
                       f"Composing {selection.title} from the selected evidence and related site context.")
        editorial_brief = json.dumps(brief, ensure_ascii=False)
        editorial_brief_bytes = len(editorial_brief.encode('utf-8'))
        generation_context_receipt = {
            'schema_version': 'content-generation-context-receipt.v1',
            'editorial_brief_bytes': editorial_brief_bytes,
            'editorial_brief_budget_bytes': 65536,
            'content_strategy_full_bytes': len(
                json.dumps(strategy_payload, ensure_ascii=False).encode('utf-8')
            ),
            'mind_strategy_context_full_bytes': len(
                json.dumps(mind_strategy_context, ensure_ascii=False).encode('utf-8')
            ) if mind_strategy_context is not None else 0,
            'strategy_projection_bytes': len(
                json.dumps(generation_strategy, ensure_ascii=False).encode('utf-8')
            ) if generation_strategy is not None else 0,
            'omitted_from_model_context': [
                'content_strategy.mind_strategy_evidence_refs',
                'content_strategy.prediction_evidence_refs',
                'virtualbrain_strategy_context.memory_prior',
                'virtualbrain_strategy_context.evidence_refs',
            ],
            'status': 'PASS' if editorial_brief_bytes <= 65536 else 'BUDGET_EXCEEDED',
        }
        _put(folder / 'generation_context_receipt.json', generation_context_receipt)
        if editorial_brief_bytes > 65536:
            raise ValueError(
                'generation editorial brief exceeds context budget: '
                f'{editorial_brief_bytes} bytes > 65536'
            )
        artifact = await ContentAgent(runtime).compose(topics, default_trading_research_audience(),
              artifact_id='continuous-' + fingerprint[:20], now=datetime.fromisoformat(inputs['now']),
              editorial_brief=editorial_brief)
        self.activity(cycle, 'CREATE', 'The draft is ready',
                       f"Drafted {artifact.title}. Running source, safety, and usefulness checks next.", status='completed')
        artifact = artifact.model_copy(update={
             'kind': ContentKind.NOTE if is_short_note else artifact.kind, 'tags': (selection.topic,),
             'disclosure': 'Written by Content Agent using public source material. Automated source and writing checks are fallible; this is not investment advice.'})
        if scope_downgraded and artifact_risk_cues(artifact, topic=selection.topic):
            self.activity(cycle, 'CREATE', 'The first draft still makes a stronger claim than the evidence allows',
                           'I am rewriting it once as a methodology and limitations article instead of discarding the whole production slot.')
            safer_brief = dict(brief)
            safer_brief['writing_requirements'] = str(brief['writing_requirements']) + (
                ' REWRITE REQUIREMENT: the previous attempt still triggered a high-risk claim cue without counter-evidence. '
                'Produce a lower-claim article about methodology, verification, uncertainty, or limitations. '
                'Do not claim or imply outperformance, profitability, alpha, prediction, forecast accuracy, reduced risk, '
                'improved returns, a win rate, a Sharpe ratio, or a buy/sell recommendation.')
            artifact = await ContentAgent(runtime).compose(
                topics, default_trading_research_audience(), artifact_id='continuous-' + fingerprint[:20],
                now=datetime.fromisoformat(inputs['now']), editorial_brief=json.dumps(safer_brief, ensure_ascii=False))
            artifact = artifact.model_copy(update={'kind': ContentKind.NOTE if is_short_note else artifact.kind, 'tags': (selection.topic,),
                 'disclosure': 'Written by Content Agent using public source material. Automated source and writing checks are fallible; this is not investment advice.'})
            self.activity(cycle, 'CREATE', 'I rewrote the draft to fit the evidence I actually have',
                           f"Reframed as {artifact.title}. Final checks will still fail closed if risky claims remain.", status='completed')
        experiment_check = experiment_compliance(artifact, experiment) if experiment is not None else None
        if experiment_check is not None:
            _put(folder / 'website_experiment_compliance.json', experiment_check)
        text = '\n'.join([artifact.title, artifact.summary] + [value for section in artifact.sections for value in (section.heading, *section.paragraphs)])
        perspective_expression = (
            cognition_decision is None
            or re.search(r"\b(?:I|I'm|I’m|my|My)\b", text) is not None
        )
        if self.policies.title_key(artifact.title) in {self.policies.title_key(t) for t in existing}:
            raise ValueError('generated title duplicates existing content')
        return DraftGenerationResult(artifact, brief, topics, experiment_check, text, perspective_expression)

    async def create(self, request: DraftGenerationRequest, runtime):
        brief, generation_strategy = self._brief(request)
        topics = self._topics(request)
        return await self._compose(request, runtime, brief, topics, generation_strategy)
