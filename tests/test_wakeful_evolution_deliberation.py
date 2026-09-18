from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import EvolutionFactKind
from agent_runtime.runtime import AgentRuntime

from wechat_traffic_agent.wakeful_tasks import WakefulContentTaskRunner


NOW = datetime(2026, 9, 18, 3, 15, tzinfo=timezone.utc)


class DailyReflection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def maybe_generate(self, now):
        self.calls += 1
        if self.fail:
            raise RuntimeError("daily reflection failed")
        return {"status": "GENERATED", "observed_at": now.isoformat()}


def _proposal_json(label: str = "credential broker") -> str:
    return json.dumps(
        {
            "statement": (
                f"A bounded {label} may reduce recurring operator dependency "
                "without exposing protected values"
            ),
            "proposed_change": (
                f"Prototype {label} in shadow mode with write-only secret input, "
                "configured/not-configured visibility, audit evidence, and rollback."
            ),
            "expected_fitness": {
                "autonomy": 0.35,
                "security": 0.20,
                "operator_friction": 0.40,
            },
            "confidence": 0.6,
        }
    )


def _runtime(tmp_path, backend=None):
    return AgentRuntime.bootstrap(
        tmp_path / "runtime",
        clock=FrozenClock(NOW),
        agent_backend=backend,
    )


def _add_need(rt, *, subject: str, severity: float, evidence: str):
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject=subject,
        summary=f"Human assistance is still required for {subject}",
        severity=severity,
        evidence_refs=[evidence],
        need_hint=f"Reduce recurring human dependency around {subject}",
    )
    return next(
        need for need in rt.evolution_needs
        if subject in need.statement
    )


def _runner(rt, daily=None):
    return WakefulContentTaskRunner(
        app=SimpleNamespace(domain_runtime=rt),
        daily_reflection=daily or DailyReflection(),
    )


def test_reflect_deliberates_highest_pressure_need_and_persists_hypothesis(tmp_path):
    backend = FixtureAgentBackend(_proposal_json())
    rt = _runtime(tmp_path, backend)
    low = _add_need(
        rt,
        subject="low-pressure-handoff",
        severity=0.76,
        evidence="human-request:low",
    )
    high = _add_need(
        rt,
        subject="protected-credential-handoff",
        severity=0.95,
        evidence="human-request:high",
    )
    assert high.pressure > low.pressure

    row = _runner(rt).run("REFLECT", now=NOW)

    assert row["status"] == "OK"
    evolution = row["evolution_deliberation"]
    assert evolution["status"] == "PROPOSED"
    assert evolution["need_id"] == high.need_id
    assert evolution["pressure"] == high.pressure
    assert evolution["confidence"] == 0.6
    assert evolution["fitness_dimensions"] == [
        "autonomy",
        "operator_friction",
        "security",
    ]
    assert "proposed_change" not in evolution
    assert len(backend.requests) == 1

    restored = AgentRuntime.restore(
        tmp_path / "runtime",
        clock=FrozenClock(NOW),
    )
    assert len(restored.evolution_hypotheses) == 1
    assert restored.evolution_hypotheses[0].need_id == high.need_id


def test_reflect_does_not_spend_tokens_again_for_existing_proposal(tmp_path):
    backend = FixtureAgentBackend(_proposal_json())
    rt = _runtime(tmp_path, backend)
    need = _add_need(
        rt,
        subject="protected-credential-handoff",
        severity=0.9,
        evidence="human-request:1",
    )
    runner = _runner(rt)

    first = runner.run("REFLECT", now=NOW)
    second = runner.run("REFLECT", now=NOW)

    assert first["evolution_deliberation"]["need_id"] == need.need_id
    assert second["evolution_deliberation"] == {
        "status": "ALREADY_PROPOSED",
        "open_need_count": 1,
    }
    assert len(backend.requests) == 1


def test_reflect_defers_cleanly_without_advisory_backend(tmp_path):
    rt = _runtime(tmp_path)
    need = _add_need(
        rt,
        subject="protected-credential-handoff",
        severity=0.9,
        evidence="human-request:1",
    )

    row = _runner(rt).run("REFLECT", now=NOW)

    assert row["status"] == "OK"
    assert row["evolution_deliberation"] == {
        "status": "BACKEND_UNAVAILABLE",
        "need_id": need.need_id,
        "pressure": need.pressure,
    }


def test_daily_reflection_failure_does_not_block_evolution_deliberation(tmp_path):
    backend = FixtureAgentBackend(_proposal_json())
    rt = _runtime(tmp_path, backend)
    _add_need(
        rt,
        subject="protected-credential-handoff",
        severity=0.9,
        evidence="human-request:1",
    )
    daily = DailyReflection(fail=True)

    row = _runner(rt, daily).run("REFLECT", now=NOW)

    assert row["status"] == "DEGRADED"
    assert row["daily_reflection"]["error_type"] == "RuntimeError"
    assert row["evolution_deliberation"]["status"] == "PROPOSED"
    assert len(backend.requests) == 1


def test_windfall_creates_high_value_hypothesis_even_without_open_need(tmp_path):
    backend = FixtureAgentBackend(_proposal_json("capability replacement audit"))
    rt = _runtime(tmp_path, backend)
    runner = WakefulContentTaskRunner(
        app=SimpleNamespace(domain_runtime=rt),
        daily_reflection=DailyReflection(),
        state_root=tmp_path,
    )
    runner._record_windfall_pending(
        {
            "model": "moonshotai/kimi-k3",
            "provider_label": "NVIDIA NIM",
            "observed_at": NOW.isoformat(),
            "capability_tier": "FRONTIER",
            "capability_score": 0.99,
            "trial_ids": ["trial:kimi:1", "trial:kimi:2", "trial:kimi:3"],
        },
        NOW,
    )

    first = runner.run("REFLECT", now=NOW)
    second = runner.run("REFLECT", now=NOW)

    evolution = first["evolution_deliberation"]
    assert evolution["status"] == "WINDFALL_PROPOSED"
    assert evolution["model"] == "moonshotai/kimi-k3"
    assert evolution["windfall_used"] is True
    assert evolution["token_budget"] == 1800
    assert len(backend.requests) == 1
    assert backend.requests[0].routing_task_class == "self_upgrade"
    assert backend.requests[0].minimum_model_quality == 0.82
    assert second["evolution_deliberation"] == {"status": "NO_OPEN_NEEDS"}
    assert len(backend.requests) == 1
    assert not (
        tmp_path
        / "application"
        / "llm_resource_pool"
        / "windfall_feast_pending.json"
    ).exists()
    receipts = list(
        (
            tmp_path
            / "application"
            / "llm_resource_pool"
            / "windfall_feast_receipts"
        ).glob("*.json")
    )
    assert len(receipts) == 1
