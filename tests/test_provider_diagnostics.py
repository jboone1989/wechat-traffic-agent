from datetime import datetime, timezone

from agent_runtime.resource_discovery import ModelTrial
from wechat_traffic_agent.provider_diagnostics import diagnose_provider_trials

NOW = datetime(2026, 9, 18, 2, 30, tzinfo=timezone.utc)


def _row(model, i, *, status, code="", quality=None):
    return ModelTrial(
        trial_id=f"trial:{model}:{i}",
        opportunity_id="lead:o",
        task_class="simple_public_text",
        status=status,
        quality_score=quality,
        latency_ms=10.0,
        observed_cost=0.0 if status == "COMPLETED" else None,
        evidence_refs=(f"receipt:{model}:{i}",),
        provider_lead_id="lead:o",
        provider_label="OpenRouter",
        model_id=model,
        started_at=NOW.isoformat(),
        completed_at=NOW.isoformat(),
        error_code=code,
    )


def test_success_counterexample_localizes_failure_and_teaches_future_runs(tmp_path):
    rows = [
        *[_row("old-model", i, status="FAILED", code="HTTP_410") for i in range(3)],
        *[_row("working-model", i, status="COMPLETED", quality=1.0) for i in range(3)],
    ]
    first = diagnose_provider_trials(
        state_root=tmp_path,
        provider_label="OpenRouter",
        credential_env="OPENROUTER_API_KEY",
        lead_id="lead:o",
        rows=rows,
        now=NOW,
    )
    second = diagnose_provider_trials(
        state_root=tmp_path,
        provider_label="OpenRouter",
        credential_env="OPENROUTER_API_KEY",
        lead_id="lead:o",
        rows=rows,
        now=NOW,
    )

    assert first["availability_status"] == "REACHABLE"
    assert first["failure_scope"] == "model"
    assert first["provider_unavailable_hypothesis"] == "REFUTED"
    assert first["specific_findings"]["incompatible_models"] == ["old-model"]
    assert first["lesson_id"]
    assert first["lesson_id"] in second["recalled_lesson_ids"]
    assert (tmp_path / "application" / "diagnostics" / "lessons").exists()


def test_rate_limit_stays_transient_and_does_not_become_provider_death(tmp_path):
    rows = [_row("free-a", i, status="FAILED", code="HTTP_429") for i in range(3)]
    result = diagnose_provider_trials(
        state_root=tmp_path,
        provider_label="OpenRouter",
        credential_env="OPENROUTER_API_KEY",
        lead_id="lead:o",
        rows=rows,
        now=NOW,
    )

    assert result["classification"] == "TEMPORARY_RATE_LIMIT"
    assert result["availability_status"] == "UNVERIFIED"
    assert result["failure_scope"] == "model"
    assert result["provider_unavailable_hypothesis"] == "UNTESTED"
    assert "retry" in result["next_probe"].lower()


def test_callable_low_quality_model_is_suitability_not_availability(tmp_path):
    rows = [_row("weak-model", i, status="COMPLETED", quality=0.2) for i in range(3)]
    result = diagnose_provider_trials(
        state_root=tmp_path,
        provider_label="OpenRouter",
        credential_env="OPENROUTER_API_KEY",
        lead_id="lead:o",
        rows=rows,
        now=NOW,
    )

    assert result["classification"] == "MODEL_TASK_MISMATCH"
    assert result["availability_status"] == "REACHABLE"
    assert result["provider_unavailable_hypothesis"] == "REFUTED"
    assert result["specific_findings"]["low_quality_models"] == ["weak-model"]


def test_consistent_401_403_is_authentication_diagnosis_not_outage(tmp_path):
    rows = [
        _row("model-a", 0, status="FAILED", code="HTTP_401"),
        _row("model-a", 1, status="FAILED", code="HTTP_403"),
    ]
    result = diagnose_provider_trials(
        state_root=tmp_path,
        provider_label="Example",
        credential_env="EXAMPLE_API_KEY",
        lead_id="lead:o",
        rows=rows,
        now=NOW,
    )

    assert result["classification"] == "AUTHENTICATION_REJECTED"
    assert result["availability_status"] == "REACHABLE_BUT_AUTH_REJECTED"
    assert result["failure_scope"] == "provider"
