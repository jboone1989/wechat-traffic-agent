"""T4 regression: a deadline and a subsequent event must not lose each other's evidence.

This runs actual Ferro IndependentTaskScheduler and Agent Runtime FilePlanStore
against temporary files, not a stubbed queue or a production state folder.
"""
from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


def test_new_event_refs_survive_existing_earlier_daily_deadline(tmp_path):
    scheduler = IndependentTaskScheduler(tmp_path)
    deadline = NOW
    later = NOW + timedelta(seconds=10)
    scheduler.schedule(
        "CONTENT_COGNITION", deadline,
        reason="daily-posting-goal",
        relevant_refs=("daily-goal:original",),
        preemptible=False,
    )

    scheduler.schedule(
        "CONTENT_COGNITION", later,
        reason="material new event",
        relevant_refs=("eventgraph:conflict:42",),
    )

    wake = scheduler.pending("CONTENT_COGNITION")
    assert len(wake) == 1
    assert wake[0].at == deadline
    assert wake[0].preemptible is False
    assert wake[0].relevant_refs == ("daily-goal:original", "eventgraph:conflict:42")
    claimed = scheduler.claim_due("CONTENT_COGNITION", NOW)
    assert claimed is not None
    assert claimed.relevant_refs == wake[0].relevant_refs


def test_earlier_event_preempts_deadline_without_losing_goal_context(tmp_path):
    scheduler = IndependentTaskScheduler(tmp_path)
    scheduler.schedule(
        "CONTENT_COGNITION", NOW + timedelta(hours=4),
        reason="daily-posting-goal", relevant_refs=("daily-goal:original",),
        preemptible=False,
    )
    scheduler.schedule(
        "CONTENT_COGNITION", NOW,
        reason="material new event", relevant_refs=("eventgraph:conflict:42",),
    )
    (wake,) = scheduler.pending("CONTENT_COGNITION")
    assert wake.at == NOW
    assert wake.preemptible is False
    assert wake.relevant_refs == ("daily-goal:original", "eventgraph:conflict:42")


def test_other_tasks_keep_separate_wake_id_and_refs(tmp_path):
    scheduler = IndependentTaskScheduler(tmp_path)
    scheduler.schedule("RESOURCE_REVIEW", NOW, relevant_refs=("pressure:ram",))
    scheduler.schedule("CONTENT_COGNITION", NOW, relevant_refs=("article:one",))
    assert scheduler.pending("RESOURCE_REVIEW")[0].relevant_refs == ("pressure:ram",)
    assert scheduler.pending("CONTENT_COGNITION")[0].relevant_refs == ("article:one",)
