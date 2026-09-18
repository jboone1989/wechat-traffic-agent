from datetime import datetime, timedelta, timezone

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.habitat_learning import (
    ACTION_KEEP_LOCAL,
    ACTION_MOVE_COLD,
    ACTION_OFFLOAD,
    ACTION_REPLICATE,
    HabitatLearningEngine,
)
from wechat_traffic_agent.habitat_placement import (
    AUTH_NONE,
    AUTH_SUBJECT_STATE,
    CONF_PRIVATE,
    CONF_PUBLIC,
    CapabilityWorker,
    HabitatPlacementController,
    PlacementContract,
    TRUST_EPHEMERAL,
)


NOW = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)


def _setup(tmp_path):
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    placement = HabitatPlacementController(state_root=tmp_path)
    placement.registry.configure(CapabilityWorker(
        worker_id="worker-public",
        trust_tier=TRUST_EPHEMERAL,
        capability_ids=("content.research",),
        network_agent_id="worker.public.1",
        network_id="network.cai",
        reliability=0.95,
        cost_class="FREE",
        lease_expires_at=(NOW + timedelta(minutes=10)).isoformat(),
        first_seen_at=(NOW - timedelta(hours=2)).isoformat(),
        last_seen_at=NOW.isoformat(),
    ))
    learning = HabitatLearningEngine(
        state_root=tmp_path,
        mind=mind,
        placement=placement,
    )
    placement.learning = learning
    return mind, placement, learning


def _contract(task_id, *, authority=AUTH_NONE, confidentiality=CONF_PUBLIC):
    return PlacementContract(
        task_id=task_id,
        capability_id="content.research",
        confidentiality=confidentiality,
        authority=authority,
        stateful=authority == AUTH_SUBJECT_STATE,
        idempotent=True,
        replayable=True,
        preferred_cost="FREE",
        required_reliability=0.5,
        created_at=NOW.isoformat(),
    )


def test_vb_prefers_core_at_normal_pressure_before_remote_evidence(tmp_path):
    mind, _placement, learning = _setup(tmp_path)

    decision = learning.decide(
        contract=_contract("research-normal"),
        now=NOW,
        resource_pressure="NORMAL",
    )

    assert decision["selected"]["action"] == ACTION_KEEP_LOCAL
    assert {row["action"] for row in decision["candidates"]} == {
        ACTION_KEEP_LOCAL, ACTION_OFFLOAD,
    }
    episode = mind.session.resource_awareness.episodes[-1]
    assert episode.decision == "EXECUTE"
    assert any(
        ref.startswith("bootstrap-prior:content.research")
        for ref in episode.evidence_refs
    )


def test_vb_can_choose_free_worker_under_critical_pressure(tmp_path):
    mind, _placement, learning = _setup(tmp_path)

    decision = learning.decide(
        contract=_contract("research-critical"),
        now=NOW,
        resource_pressure="CRITICAL",
    )

    assert decision["selected"]["action"] == ACTION_OFFLOAD
    assert decision["selected"]["worker_id"] == "worker-public"
    assert decision["selected"]["utility"] > 0
    episode = mind.session.resource_awareness.episodes[-1]
    assert episode.decision == "EXECUTE_CHEAPER"


def test_measured_remote_failure_changes_next_vb_choice(tmp_path):
    mind, _placement, learning = _setup(tmp_path)
    first = learning.decide(
        contract=_contract("research-failure-1"),
        now=NOW,
        resource_pressure="CRITICAL",
    )
    assert first["selected"]["action"] == ACTION_OFFLOAD

    outcome = learning.record_execution(
        task_id="research-failure-1",
        episode_id=first["episode_id"],
        capability_id="content.research",
        action=ACTION_OFFLOAD,
        worker_id="worker-public",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        success=False,
        fallback_used=True,
        latency_ms=2000.0,
        process_cpu_ms=25.0,
        rss_delta_mib=1.0,
        disk_free_delta_mib=0.0,
        worker_age_seconds=7200.0,
        error_type="TimeoutError",
        evidence_refs=("test:remote-timeout",),
    )

    assert outcome.success is False
    assert learning.organ_cost("content.research", ACTION_OFFLOAD).sample_count == 1
    assert len(mind.session.resource_awareness.outcomes) == 1
    assert len(mind.session.resource_awareness.reflections) == 1

    second = learning.decide(
        contract=_contract("research-after-failure"),
        now=NOW + timedelta(minutes=1),
        resource_pressure="CRITICAL",
    )
    assert second["selected"]["action"] == ACTION_KEEP_LOCAL


def test_subject_state_remains_pinned_by_hard_controller_boundary(tmp_path):
    mind, _placement, learning = _setup(tmp_path)

    decision = learning.decide(
        contract=_contract(
            "subject-state",
            authority=AUTH_SUBJECT_STATE,
            confidentiality=CONF_PRIVATE,
        ),
        now=NOW,
        resource_pressure="CRITICAL",
    )

    assert decision["selected"]["action"] == ACTION_KEEP_LOCAL
    offload = next(row for row in decision["candidates"] if row["action"] == ACTION_OFFLOAD)
    assert offload["eligible"] is False
    assert mind.session.resource_awareness.episodes[-1].decision == "EXECUTE"


def test_habitat_state_and_future_candidate_catalog_are_explicit(tmp_path):
    _mind, placement, learning = _setup(tmp_path)
    state = learning.observe_habitat(
        now=NOW,
        current_resource={
            "pressure": "WATCH",
            "disk_free_gib": 11.5,
            "disk_used_ratio": 0.82,
            "mem_available_gib": 2.0,
        },
        placement_review={"status": "READY"},
    )

    assert state.resource_pressure == "WATCH"
    assert state.live_workers == 1
    assert state.worker_ids == ("worker-public",)
    catalog = {row["action"]: row for row in learning.candidate_catalog()}
    assert catalog[ACTION_KEEP_LOCAL]["execution"] == "AVAILABLE"
    assert catalog[ACTION_OFFLOAD]["execution"] == "AVAILABLE"
    assert catalog[ACTION_MOVE_COLD]["execution"] == "PLANNED"
    assert catalog[ACTION_REPLICATE]["execution"] == "PLANNED"
