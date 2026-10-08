"""Compaction must preserve events still usable for causal value and interest learning."""
from __future__ import annotations

from virtualbrain.research.autobiography import LifeEvent
from wechat_traffic_agent.content_mind import ContentMind


def test_compaction_retains_autobiographical_event_links(tmp_path):
    root = tmp_path / "state"
    mind = ContentMind(root=root, subject_id="content-agent")
    for index in range(280):
        mind.session.autobiography.record(
            LifeEvent(
                event_id=f"event-{index}",
                tick=index,
                summary=f"Evidence-bearing experience {index}",
                evidence_refs=(f"receipt:{index}",),
            )
        )
    mind._persist()
    assert len(mind.session.autobiography.events) == 280
    restored = ContentMind(root=root, subject_id="content-agent")
    assert len(restored.session.autobiography.events) == 280
    assert restored.session.autobiography.event_by_id("event-0") is not None
