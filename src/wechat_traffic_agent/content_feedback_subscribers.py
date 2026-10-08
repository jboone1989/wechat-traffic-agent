"""Independent learning and process-memory listeners for content lifecycle events."""
from __future__ import annotations

from .content_loop import _put
from .content_lifecycle_events import DraftChecked, ContentCycleSettled


class MindProcessFeedback:
    def __init__(self, mind, activity):
        self.mind = mind
        self.activity = activity

    def __call__(self, event: DraftChecked):
        if self.mind is None:
            return None
        self.activity(
            event.cycle, 'LEARN', 'I’m recording what happened in this cycle',
            'Updating bounded process experience from this cycle; no audience-effect claim is made here.',
        )
        receipt = self.mind.observe_process_experience(
            cycle=event.cycle, topic=event.topic, passed=event.passed,
            model_calls=event.model_calls, evidence_refs=list(event.evidence_refs),
        )
        _put(event.folder / 'mind_experience.json', receipt)
        self.activity(
            event.cycle, 'LEARN', 'I saved what I learned from the process',
            f"Mind tick advanced with {'a passing' if event.passed else 'a held'} process result.",
            status='completed',
        )
        return receipt


class MemoryLifeFeedback:
    def __init__(self, memory_life):
        self.memory_life = memory_life

    def __call__(self, event: ContentCycleSettled):
        try:
            return self.memory_life.observe_process_outcome(
                process_id='content-cycle:' + event.cycle,
                topic=event.topic,
                action='content-create-verify-publish',
                success=event.passed,
                outcome=event.status,
                evidence_refs=list(event.evidence_refs),
                capabilities=[
                    'content.generate', 'content.verify',
                    *(['content.publish'] if event.publication_enabled else []),
                ],
                steps=list(event.process_steps),
                verification=[
                    'automated_checks_passed=true', 'evidence floor PASS',
                    'persist cycle evidence refs',
                ],
                failure_mode='' if event.passed else (event.failure_mode or 'content_checks_failed'),
                recovery_step='' if event.passed else (
                    'retry once with the frozen artifact when the failure is checker-only; '
                    'otherwise repair missing evidence or failed checks before publishing'
                ),
                expected_cost={'model_calls': event.model_calls},
                observed_at=event.observed_at.isoformat(),
            )
        except Exception as exc:
            return {'status': 'ERROR', 'error_type': type(exc).__name__}
