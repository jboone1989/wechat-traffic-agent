"""Complete a verified draft without coupling editorial, learning and distribution.

The publication port persists its own immutable PublicationEvent and queues
downstream delivery.  Learning and process memory receive typed notifications.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .content_feedback_subscribers import MemoryLifeFeedback, MindProcessFeedback
from .content_lifecycle_events import (
    ContentCycleSettled, ContentEventRouter, DraftChecked,
)


class ContentCycleCompletion:
    def __init__(self, *, publisher, mind, memory_life, activity):
        self.publisher = publisher
        self.activity = activity
        self.events = ContentEventRouter()
        self.events.subscribe(DraftChecked, "mind", MindProcessFeedback(mind, activity))
        self.events.subscribe(ContentCycleSettled, "memory_life", MemoryLifeFeedback(memory_life))

    def _publish(self, result, *, artifact, experience, folder, cycle, passed):
        if not passed or self.publisher is None:
            return
        self.activity(cycle, 'CREATE', 'I’m publishing the verified article',
                      f"Publishing {artifact.title} to the public site.")
        result['publication'] = self.publisher.publish(
            artifact=artifact, experience=experience, cycle_root=folder,
        )
        result['status'] = 'PUBLISHED'
        self.activity(cycle, 'LEARN', 'Published — and I recorded the result',
                      f"{artifact.title} is public; the cycle is complete.",
                      status='completed')

    def complete(self, result, *, artifact, experience, folder: Path, cycle: str,
                 topic: str, passed: bool, evidence_id: str, evidence_refs,
                 runtime_calls: int, process_steps, failure_mode: str,
                 observed_at: datetime):
        refs = tuple(dict.fromkeys([
            f'cycle:{cycle}', 'evidence:' + evidence_id, *evidence_refs,
            'draft-sha256:' + experience['draft_sha256'],
        ]))
        model_calls = runtime_calls + len(list((folder / 'explorer_calls').glob('*.json')))
        receipts = self.events.emit(DraftChecked(
            cycle, topic, bool(passed), refs, experience['draft_sha256'],
            model_calls, folder,
        ))
        if receipts.get('mind') is not None:
            result['mind_experience'] = receipts['mind']
        self._publish(result, artifact=artifact, experience=experience,
                      folder=folder, cycle=cycle, passed=passed)
        if result.get('status') == 'PUBLISHED':
            from .square_website_syndication import SquareWebsiteSyndication
            result['square_syndication'] = SquareWebsiteSyndication(
                folder.parent.parent.parent.parent).enqueue(folder=folder, result=result)
        outcomes = self.events.emit(ContentCycleSettled(
            cycle, topic, bool(passed), str(result.get('status') or 'UNKNOWN'),
            refs, tuple(process_steps), failure_mode, model_calls,
            observed_at, self.publisher is not None,
        ))
        result['memory_life'] = outcomes['memory_life']
        return result
