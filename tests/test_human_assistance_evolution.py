from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from wechat_traffic_agent.human_assistance import HumanAssistanceService


NOW = datetime(2026, 9, 18, 2, 15, tzinfo=timezone.utc)


class NullNotifier:
    configured = False

    def send_text(self, text: str):
        raise AssertionError("notifier should not be used")


@dataclass
class FakeFact:
    fact_id: str


class FakeEvolutionRuntime:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.persist_count = 0
        self.fail = fail

    def observe_evolution_fact(self, **kwargs):
        if self.fail:
            raise RuntimeError("evolution unavailable")
        self.calls.append(dict(kwargs))
        return FakeFact(f"evf:{len(self.calls)}")

    def persist(self):
        self.persist_count += 1
def _request(service, *, provider: str, env_name: str, summary: str):
    return service.request(
        kind="MODEL_RESOURCE_ACCESS",
        urgency="NORMAL",
        summary=summary,
        requested_action=f"Configure {env_name} through the protected operator path.",
        now=NOW,
        details={
            "provider_label": provider,
            "credential_env": env_name,
            "signup_url": "https://example.invalid/signup",
        },
    )


def test_model_credential_request_records_generic_human_dependency(tmp_path):
    runtime = FakeEvolutionRuntime()
    service = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )

    row = _request(
        service,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Cerebras trial is blocked on a protected credential.",
    )

    assert row["evolution_observation_status"] == "RECORDED"
    assert row["evolution_fact_id"] == "evf:1"
    assert runtime.persist_count == 1
    [fact] = runtime.calls
    assert fact["kind"] == "HUMAN_DEPENDENCY"
    assert fact["subject"] == "protected-credential-handoff"
    assert fact["recurrence_key"] == (
        "HUMAN_DEPENDENCY:protected-credential-handoff"
    )
    assert fact["evidence_refs"] == [row["request_id"]]
    assert fact["metadata"] == {
        "request_kind": "MODEL_RESOURCE_ACCESS",
        "credential_env": "CEREBRAS_API_KEY",
        "provider_label": "Cerebras",
    }


def test_different_providers_share_one_structural_recurrence_key(tmp_path):
    runtime = FakeEvolutionRuntime()
    service = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )

    _request(
        service,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Cerebras needs a credential.",
    )
    _request(
        service,
        provider="NVIDIA NIM",
        env_name="NVIDIA_API_KEY",
        summary="NVIDIA needs a credential.",
    )

    assert len(runtime.calls) == 2
    assert {
        item["recurrence_key"] for item in runtime.calls
    } == {"HUMAN_DEPENDENCY:protected-credential-handoff"}
    assert {
        item["subject"] for item in runtime.calls
    } == {"protected-credential-handoff"}


def test_repeated_same_request_is_not_recorded_twice(tmp_path):
    runtime = FakeEvolutionRuntime()
    service = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )
    first = _request(
        service,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Cerebras needs a credential.",
    )
    second = _request(
        service,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Cerebras needs a credential.",
    )

    assert first["request_id"] == second["request_id"]
    assert len(runtime.calls) == 1
    assert runtime.persist_count == 1


def test_backfill_promotes_legacy_request_into_evolution_fact(tmp_path):
    legacy = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
    )
    row = _request(
        legacy,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Existing Cerebras request.",
    )
    assert row["evolution_observation_status"] == "UNAVAILABLE"

    runtime = FakeEvolutionRuntime()
    upgraded = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )
    result = upgraded.backfill_evolution_observations(now=NOW)

    assert result == {"status": "OK", "recorded": 1, "failed": 0}
    assert len(runtime.calls) == 1
    saved = json.loads(
        next(
            (tmp_path / "application" / "human_assistance").glob(
                "human-request-*.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert saved["evolution_observation_status"] == "RECORDED"


def test_evolution_failure_never_blocks_human_assistance(tmp_path):
    runtime = FakeEvolutionRuntime(fail=True)
    service = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )

    row = _request(
        service,
        provider="Cerebras",
        env_name="CEREBRAS_API_KEY",
        summary="Credential is still needed.",
    )

    assert row["request_id"].startswith("human-request:")
    assert row["evolution_observation_status"] == "FAILED"
    assert row["evolution_error_type"] == "RuntimeError"
    saved = list(
        (tmp_path / "application" / "human_assistance").glob(
            "human-request-*.json"
        )
    )
    assert len(saved) == 1


def test_evolution_metadata_never_copies_unrelated_secret_fields(tmp_path):
    runtime = FakeEvolutionRuntime()
    service = HumanAssistanceService(
        state_root=tmp_path,
        notifier=NullNotifier(),
        evolution_runtime=runtime,
    )
    service.request(
        kind="MODEL_RESOURCE_ACCESS",
        summary="Credential is needed.",
        requested_action="Use the protected operator path.",
        now=NOW,
        details={
            "provider_label": "Example",
            "credential_env": "EXAMPLE_API_KEY",
            "api_key": "do-not-learn-this-value",
        },
    )

    [fact] = runtime.calls
    encoded = json.dumps(fact, sort_keys=True)
    assert "do-not-learn-this-value" not in encoded
    assert set(fact["metadata"]) == {
        "request_kind",
        "credential_env",
        "provider_label",
    }
