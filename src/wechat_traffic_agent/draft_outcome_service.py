"""Persist the inspected draft and produce a verifiable result payload.

This service does not decide, write, verify or publish the article. The
existing completion/outbox port is solely responsible for actual publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .content_loop import _put
from .draft_inbox import ContentDraftRecord
from .web_editorial import digest


@dataclass(frozen=True)
class DraftOutcomeRequest:
    folder: Path
    cycle: str
    inputs: dict
    artifact: Any
    fingerprint: str
    lesson_id: str
    lesson_payload: dict
    check: Any
    passes: bool
    process_skill: dict | None
    verification_attempts: int
    draft_repair_attempts: int
    experiment_payload: dict | None
    experiment_check: dict | None
    strategy_payload: dict
    action_prediction_payload: dict
    mind_strategy_context: dict | None
    strategy_sync: dict
    floor_plan: Any
    floor_check: Any
    technique_requirements: dict
    required_technique_indices: set[int]
    application_valid: set[int]
    support_valid: set[int]
    support_exempt: set[int]
    grounding_valid: set[int]
    perspective_expression: bool
    expression_kind: str
    minimum_words: int | None
    text: str
    traffic_payload: dict
    mind_plan: dict
    worldview_update: dict | None
    prediction_record: dict | None
    cognition_decision: Any
    exploration: dict
    evidence_pool: dict
    selected: dict
    market_companion: dict | None


class DraftOutcomeService:
    def __init__(self, *, inbox, runtime):
        self.inbox, self.runtime = inbox, runtime

    def persist(self, request: DraftOutcomeRequest):
        (folder, cycle, inputs, artifact, fingerprint, lesson_id, lesson_payload,
         check, passes, process_skill, verification_attempts,
         draft_repair_attempts, experiment_payload, experiment_check,
         strategy_payload, action_prediction_payload, mind_strategy_context,
         strategy_sync, floor_plan, floor_check, technique_requirements,
         required_technique_indices, application_valid, support_valid,
         support_exempt, grounding_valid, perspective_expression,
         expression_kind, minimum_words, text) = (
            request.folder, request.cycle, request.inputs, request.artifact,
            request.fingerprint, request.lesson_id, request.lesson_payload,
            request.check, request.passes, request.process_skill,
            request.verification_attempts, request.draft_repair_attempts,
            request.experiment_payload, request.experiment_check,
            request.strategy_payload, request.action_prediction_payload,
            request.mind_strategy_context, request.strategy_sync,
            request.floor_plan, request.floor_check, request.technique_requirements,
            request.required_technique_indices, request.application_valid,
            request.support_valid, request.support_exempt, request.grounding_valid,
            request.perspective_expression, request.expression_kind,
            request.minimum_words, request.text,
        )
        experiment_eligible = experiment_check is None or experiment_check['status'] == 'PASS'
        path = self.inbox.save(ContentDraftRecord(source_fingerprint=fingerprint,
                              generated_at=datetime.fromisoformat(inputs['now']), artifact=artifact))
        experience = {'cycle': cycle, 'lesson_id': lesson_id, 'lesson': lesson_payload,
                      'draft_sha256': digest(artifact.model_dump(mode='json')), 'source_fingerprint': fingerprint,
                      'self_check': check.model_dump(mode='json'), 'automated_checks_passed': passes,
                      'memory_process_variant': {
                          'procedure_id': str((process_skill or {}).get('procedure_id') or ''),
                          'variant_id': str((process_skill or {}).get('variant_id') or ''),
                          'policy_action': str((process_skill or {}).get('policy_action') or 'DIRECT'),
                          'policy_reason': str((process_skill or {}).get('policy_reason') or ''),
                          'verification_attempts': verification_attempts,
                          'draft_repair_attempts': draft_repair_attempts,
                      },
                      'prospective_experiment': experiment_payload,
                      'website_experiment_compliance': experiment_check,
                      'website_experiment_eligible': experiment_eligible,
                      'content_strategy': strategy_payload,
                      'action_prediction': action_prediction_payload,
                      'virtualbrain_strategy_context': mind_strategy_context,
                      'strategy_learning_sync': strategy_sync,
                      'traffic_context_ref': 'traffic_context.json',
                      'evidence_floor_plan': floor_plan.model_dump(mode='json'),
                      'evidence_floor_check': floor_check.model_dump(mode='json'),
                      'technique_requirements': technique_requirements,
                      'missing_technique_applications': sorted(
                          required_technique_indices - application_valid
                      ),
                      'deferred_technique_applications': technique_requirements['deferred_indices'],
                      'missing_section_support': sorted(set(range(len(artifact.sections))) - support_valid),
                      'support_exempt_section_indices': sorted(support_exempt),
                      'missing_section_grounding': sorted(set(range(len(artifact.sections))) - grounding_valid),
                      'perspective_expression': perspective_expression,
                      'expression_kind': expression_kind, 'minimum_words': minimum_words,
                      'word_count': len(text.split()),
                      'assessment_kind': 'model self-check with exact-quote validation; no audience outcome claim'}
        _put(folder / 'experience.json', experience)
        self.runtime.persist()
        return path, experience

    def result(self, request, path):
        (cycle, inputs, fingerprint, lesson_id, passes, experiment_payload,
         experiment_check, strategy_payload, action_prediction_payload,
         mind_strategy_context, strategy_sync, traffic_payload,
         floor_check, process_skill, verification_attempts, draft_repair_attempts,
         perspective_expression, mind_plan, worldview_update, prediction_record,
         cognition_decision, exploration, evidence_pool, selected, market_companion) = (
            request.cycle, request.inputs, request.fingerprint, request.lesson_id,
            request.passes, request.experiment_payload, request.experiment_check,
            request.strategy_payload, request.action_prediction_payload,
            request.mind_strategy_context, request.strategy_sync,
            request.traffic_payload, request.floor_check, request.process_skill,
            request.verification_attempts, request.draft_repair_attempts,
            request.perspective_expression, request.mind_plan, request.worldview_update,
            request.prediction_record, request.cognition_decision,
            request.exploration, request.evidence_pool, request.selected,
            request.market_companion,
        )
        experiment_eligible = experiment_check is None or experiment_check['status'] == 'PASS'
        result = {'status': 'DRAFT_CREATED' if passes else 'HELD_BY_CHECKS', 'cycle': cycle,
                  'source_fingerprint': fingerprint, 'draft_path': str(path), 'lesson_id': lesson_id,
                  'prospective_experiment': experiment_payload,
                  'website_experiment_compliance': experiment_check,
                  'website_experiment_eligible': experiment_eligible,
                  'content_strategy': strategy_payload,
                  'action_prediction': action_prediction_payload,
                  'virtualbrain_strategy_context': mind_strategy_context,
                  'strategy_learning_sync': strategy_sync,
                  'traffic_context': traffic_payload,
                  'evidence_floor': floor_check.model_dump(mode='json'),
                  'prior_experiences_used': len(inputs['prior_experience']), 'automated_checks_passed': passes,
                  'memory_process_variant': {
                      'procedure_id': str((process_skill or {}).get('procedure_id') or ''),
                      'variant_id': str((process_skill or {}).get('variant_id') or ''),
                      'policy_action': str((process_skill or {}).get('policy_action') or 'DIRECT'),
                      'policy_reason': str((process_skill or {}).get('policy_reason') or ''),
                      'verification_attempts': verification_attempts,
                      'draft_repair_attempts': draft_repair_attempts,
                  },
                  'perspective_expression': perspective_expression,
                  'mind_plan': mind_plan, 'worldview_update': worldview_update, 'prediction': prediction_record,
                  'cognition': (cognition_decision.model_dump(mode='json') if cognition_decision is not None else None),
                  'source_failures': exploration.get('source_failures', evidence_pool.get('failures', [])),
                  'exploration': {'candidate_count': exploration.get('candidate_count', 0),
                                  'match_count': len(exploration.get('matches', [])),
                                  'selected_evidence_id': selected['evidence_id'],
                                  'selected_source_kind': selected['source_kind'],
                                  'market_context_evidence_id': (market_companion.get('evidence_id') if market_companion else None),
                                  'evidence_refs': selected['evidence_refs']}}
        return result

    def complete(self, request: DraftOutcomeRequest):
        path, experience = self.persist(request)
        return self.result(request, path), experience
