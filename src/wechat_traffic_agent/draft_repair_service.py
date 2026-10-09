"""Single bounded writing-technique repair, with the original verifier as authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import re

from .content_artifact import ContentKind
from .content_loop import _put
from .content_service import ContentAgent
from .trading_draft_worker import default_trading_research_audience
from .website_experiment import experiment_compliance
from .web_editorial import digest


@dataclass(frozen=True)
class DraftRepairRequest:
    generation: object
    verification_request: object
    inputs: dict
    fingerprint: str
    selection: object
    lesson_payload: dict
    required_indices: set[int]
    experiment: object | None


@dataclass(frozen=True)
class DraftRepairResult:
    artifact: object
    verification: object
    experiment_check: dict | None
    text: str
    perspective_expression: bool
    repair_attempts: int


class DraftRepairService:
    def __init__(self, *, activity, verifier):
        self.activity, self.verifier = activity, verifier

    @staticmethod
    def _repairable(request, verification):
        needed = request.required_indices - verification.application_valid
        body = request.generation.text
        return (
            not verification.passed and bool(needed)
            and all([verification.check.grounded, verification.check.useful])
            and verification.grounding_valid == set(range(len(request.generation.artifact.sections)))
            and (request.verification_request.minimum_words is None
                 or len(body.split()) >= request.verification_request.minimum_words)
            and verification.floor_check.status == 'PASS'
            and request.generation.perspective_expression
        )

    @staticmethod
    def _repair_brief(request, indices):
        brief = dict(request.generation.brief)
        brief['draft_repair'] = {
            'reason': 'MISSING_LEARNED_TECHNIQUE',
            'missing_technique_indices': indices,
            'missing_techniques': [
                request.lesson_payload['techniques'][index] for index in indices
            ],
            'original_article': request.generation.artifact.model_dump(mode='json'),
        }
        brief['writing_requirements'] = (
            str(brief['writing_requirements'])
            + ' ONE BOUNDED DRAFT REPAIR: preserve the same factual scope, '
            'sources, uncertainty, and safety boundaries. Do not add new factual '
            'claims merely to satisfy style. Visibly apply every missing REQUIRED '
            'learned technique listed in draft_repair, using its instruction and check. '
            'Never force a technique deferred by technique_requirements.'
        )
        return brief

    async def repair(self, request: DraftRepairRequest, runtime, verified) -> DraftRepairResult:
        original = request.generation
        if not self._repairable(request, verified):
            return DraftRepairResult(
                original.artifact, verified, original.experiment_check,
                original.text, original.perspective_expression, 0,
            )
        indices = sorted(request.required_indices - verified.application_valid)
        folder, cycle = request.verification_request.folder, request.verification_request.cycle
        self.activity(
            cycle, 'CREATE', 'The draft missed a writing technique, so I’m repairing it once',
            'Keeping the same evidence and claim boundaries; the repaired draft must pass every original check.',
        )
        artifact = await ContentAgent(runtime).compose(
            original.topics, default_trading_research_audience(),
            artifact_id='continuous-' + request.fingerprint[:20],
            now=datetime.fromisoformat(request.inputs['now']),
            editorial_brief=json.dumps(self._repair_brief(request, indices), ensure_ascii=False),
        )
        artifact = artifact.model_copy(update={
            'kind': (ContentKind.NOTE if request.verification_request.is_short_note else artifact.kind),
            'tags': (request.selection.topic,),
            'disclosure': (
                'Written by Content Agent using public source material. '
                'Automated source and writing checks are fallible; '
                'this is not investment advice.'
            ),
        })
        compliance = experiment_compliance(artifact, request.experiment) if request.experiment is not None else None
        if compliance is not None:
            _put(folder / 'website_experiment_compliance.json', compliance)
        body = self.verifier._body(artifact)
        perspective = (
            re.search(r"\b(?:I|I'm|I’m|my|My)\b", body) is not None
            if request.verification_request.perspective_required else True
        )
        revised = replace(
            request.verification_request, artifact=artifact,
            perspective_expression=perspective, retry_verification_once=False,
        )
        reviewed = await self.verifier.verify(revised, runtime, strict_repair=True)
        _put(folder / 'draft_repair.json', {
            'schema_version': 'content-draft-repair.v1',
            'reason': 'MISSING_LEARNED_TECHNIQUE',
            'missing_technique_indices_before': indices,
            'original_draft_sha256': digest(original.artifact.model_dump(mode='json')),
            'repaired_draft_sha256': digest(artifact.model_dump(mode='json')),
            'recheck_passed': bool(reviewed.passed), 'gate_weakened': False,
        })
        return DraftRepairResult(artifact, reviewed, compliance, body, perspective, 1)
