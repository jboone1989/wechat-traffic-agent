from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)


class _Explorer:
    def __init__(self, evidence_id: str = "rss:test:1"):
        self.evidence_id = evidence_id

    async def collect(self, *, now, active):
        assert active is True
        return {
            "items": [{
                "evidence_id": self.evidence_id,
                "source": "rss",
                "title": "Fresh public evidence",
                "summary": "Observed from the public world.",
                "observed_at": now.isoformat(),
            }],
            "failures": [{
                "source": "agent-network:trading",
                "error_type": "RuntimeError",
            }],
            "observed_at": now.isoformat(),
        }


class _MissingTradingDraft:
    async def run_once(self, *, now):
        raise RuntimeError(
            "Agent Network returned no Trading public research provider"
        )


class _NoTradingProviderResult:
    def to_payload(self):
        return {
            "status": "NO_RESEARCH_PROVIDER",
            "topic_count": 0,
            "artifact_id": None,
        }


class _NoTradingProviderDraft:
    async def run_once(self, *, now):
        return _NoTradingProviderResult()


class _WakefulRecorder:
    def __init__(self):
        self.network_evidence = []

    def emit_network_evidence(self, *, evidence_ref, observed_at):
        self.network_evidence.append((evidence_ref, observed_at))


def _runner(tmp_path):
    runner = WakefulContentTaskRunner(
        state_root=tmp_path,
        continuous_loop=SimpleNamespace(explorer=_Explorer()),
        draft_worker=_MissingTradingDraft(),
        wakeful=SimpleNamespace(),
    )
    runner._runtime_tick = lambda now: (
        SimpleNamespace(generation_ready=True),
        {"application": {"status": "OK"}},
    )
    return runner


def test_observe_world_does_not_invent_network_evidence_when_provider_absent(tmp_path):
    runner = _runner(tmp_path)
    recorder = _WakefulRecorder()
    runner.wakeful = recorder
    runner.draft_worker = _NoTradingProviderDraft()

    result = runner._observe(NOW)

    assert result["status"] == "OK"
    assert result["world_evidence"]["novel_evidence_count"] == 1
    assert result["world_utility"] > 0.05
    assert result["draft_worker"]["status"] == "NO_RESEARCH_PROVIDER"
    assert result["draft_worker"]["required_for_observation"] is False
    assert recorder.network_evidence == []


def test_observe_world_uses_public_explorer_when_trading_provider_missing(tmp_path):
    runner = _runner(tmp_path)

    result = runner._observe(NOW)

    assert result["status"] == "OK"
    assert result["world_evidence"]["status"] == "OBSERVED"
    assert result["world_evidence"]["item_count"] == 1
    assert result["world_evidence"]["novel_evidence_count"] == 1
    assert result["world_utility"] > 0.05
    assert result["world_utility_evidence"] == ["rss:test:1"]
    assert result["draft_worker"]["status"] == "OPTIONAL_SOURCE_UNAVAILABLE"
    assert result["draft_worker"]["required_for_observation"] is False


def test_observe_world_repeated_evidence_does_not_repeat_world_reward(tmp_path):
    runner = _runner(tmp_path)

    first = runner._observe(NOW)
    second = runner._observe(NOW.replace(minute=1))

    assert first["world_utility"] > 0.05
    assert second["status"] == "OK"
    assert second["world_evidence"]["novel_evidence_count"] == 0
    assert second["world_utility"] == 0.0
    assert second["world_utility_evidence"] == []
