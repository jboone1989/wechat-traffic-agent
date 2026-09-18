from datetime import datetime, timedelta, timezone
import json

from wechat_traffic_agent.nvidia_catalog import (
    NvidiaCapabilityCatalog,
    classify_nvidia_model,
)


NOW = datetime(2026, 9, 18, 5, 0, tzinfo=timezone.utc)


def test_catalog_classifies_full_capability_families():
    assert classify_nvidia_model(
        "nvidia/nemotron-3-ultra-550b-a55b"
    )["category"] == "text_reasoning"
    assert classify_nvidia_model(
        "moonshotai/kimi-k3"
    )["category"] == "vision_multimodal"
    assert classify_nvidia_model(
        "nvidia/nemotron-3-embed-1b"
    )["category"] == "retrieval_embedding"
    assert classify_nvidia_model(
        "nvidia/llama-nemotron-rerank-vl-1b-v2"
    )["category"] == "retrieval_rerank"
    assert classify_nvidia_model(
        "nvidia/nemotron-parse-2.0"
    )["category"] == "document_extraction"
    assert classify_nvidia_model(
        "nvidia/riva-translate-4b-instruct-v2"
    )["category"] == "speech_audio"
    assert classify_nvidia_model(
        "nvidia/nemotron-3.5-content-safety"
    )["category"] == "safety_moderation"
    assert classify_nvidia_model(
        "nvidia/cosmos-reason2-8b"
    )["category"] == "physical_media"


def test_catalog_observation_persists_secret_free_full_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "never-persist-me")
    catalog = NvidiaCapabilityCatalog(state_root=tmp_path)
    payload = {
        "data": [
            {"id": "nvidia/nemotron-3-ultra-550b-a55b"},
            {"id": "z-ai/glm-5.3"},
            {"id": "moonshotai/kimi-k3"},
            {"id": "nvidia/nemotron-3-embed-1b"},
            {"id": "nvidia/nemotron-parse-2.0"},
            {"id": "nvidia/riva-translate-4b-instruct-v2"},
            {"id": "nvidia/nemotron-3.5-content-safety"},
        ]
    }
    monkeypatch.setattr(catalog, "_fetch", lambda _key: payload)

    result = catalog.observe(now=NOW)

    assert result["status"] == "OBSERVED"
    assert result["model_count"] == 7
    assert result["category_counts"]["text_reasoning"] == 2
    assert result["category_counts"]["vision_multimodal"] == 1
    assert result["category_counts"]["retrieval_embedding"] == 1
    assert result["category_counts"]["document_extraction"] == 1
    assert result["category_counts"]["speech_audio"] == 1
    assert result["category_counts"]["safety_moderation"] == 1

    raw = catalog.path.read_text()
    assert "never-persist-me" not in raw
    saved = json.loads(raw)
    assert len(saved["models"]) == 7
    assert {
        row["category"] for row in saved["models"]
    } >= {
        "text_reasoning",
        "vision_multimodal",
        "retrieval_embedding",
        "document_extraction",
        "speech_audio",
        "safety_moderation",
    }


def test_catalog_observation_is_hourly_and_records_model_churn(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    catalog = NvidiaCapabilityCatalog(state_root=tmp_path)
    calls = []
    responses = iter([
        {"data": [{"id": "z-ai/glm-5.3"}]},
        {"data": [{"id": "z-ai/glm-5.3-flash"}]},
    ])

    def fake(_key):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr(catalog, "_fetch", fake)

    first = catalog.observe(now=NOW)
    not_due = catalog.observe(now=NOW + timedelta(minutes=10))
    second = catalog.observe(now=NOW + timedelta(hours=2))

    assert first["new_models"] == ["z-ai/glm-5.3"]
    assert not_due["status"] == "NOT_DUE"
    assert len(calls) == 2
    assert second["new_models"] == ["z-ai/glm-5.3-flash"]
    assert second["removed_models"] == ["z-ai/glm-5.3"]


def test_catalog_missing_credential_does_not_create_fake_inventory(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    catalog = NvidiaCapabilityCatalog(state_root=tmp_path)

    result = catalog.observe(now=NOW)

    assert result == {
        "status": "CREDENTIAL_MISSING",
        "credential_env": "NVIDIA_API_KEY",
    }
    assert not catalog.path.exists()


def test_catalog_does_not_treat_reward_diffusion_or_clip_as_general_chat():
    assert classify_nvidia_model(
        "nvidia/nemotron-4-340b-reward"
    )["category"] == "reward_model"
    assert classify_nvidia_model(
        "google/diffusiongemma-26b-a4b-it"
    )["category"] == "physical_media"
    clip = classify_nvidia_model("nvidia/nvclip")
    assert clip["category"] == "retrieval_embedding"
    assert clip["endpoint_family"] == "embeddings"
