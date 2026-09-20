from __future__ import annotations

import json
from pathlib import Path

from wechat_traffic_agent.cortex_continuity import CortexContinuityMonitor


def _append_audit(root: Path, *, request_id: str, model: str, resource: str) -> None:
    path = root / "application" / "llm_audit" / "requests.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "request_id": request_id,
        "scope": "content-agent",
        "provider": {
            "class": "OpenAICompatibleProvider",
            "provider_name": resource.split(":", 1)[0],
            "model": model,
        },
        "routing": {"selected_resource_id": resource},
        "status": "OK",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _graph(*, event_seq: int = 10, births: int = 1, mutations: int = 2):
    return {
        "substrate": {
            "source_fingerprint": "sha256:ancestral-malecns",
            "neuron_count": 166_700 + births,
            "developmental_neurogenesis": {
                "synthetic_neuron_count": births,
                "birth_seq": births,
                "event_seq": event_seq,
                "species_stage": "FERRO_F0_DEVELOPING",
                "developmental_genome": {
                    "fingerprint": f"sha256:genome-{mutations}",
                    "mutation_count": mutations,
                },
            },
        }
    }


def _motor(*, seq: int = 5):
    return {
        "decision_seq": seq,
        "habits": [
            {"motor_id": "motor_003", "meaning": "NEURAL_DREAM"},
        ],
        "recent_decisions": [],
    }


def test_actual_audit_provider_change_preserves_organism_identity(tmp_path: Path):
    monitor = CortexContinuityMonitor(tmp_path)
    organism_id = monitor.projection()["organism_id"]

    _append_audit(
        tmp_path,
        request_id="req:1",
        model="deepseek/deepseek-v4-flash-0731:free",
        resource="openrouter:free-deepseek",
    )
    first = monitor.observe(
        neural_graph=_graph(event_seq=10, births=1, mutations=2),
        motor=_motor(seq=5),
        personality={"traits": {"curiosity": 0.7}},
    )
    assert first["swap_count"] == 0
    assert first["current_cortex"]["model"].startswith("deepseek/")

    _append_audit(
        tmp_path,
        request_id="req:2",
        model="openai/gpt-oss-20b",
        resource="nvidia:gpt-oss",
    )
    second = monitor.observe(
        neural_graph=_graph(event_seq=12, births=1, mutations=3),
        motor=_motor(seq=7),
        personality={"traits": {"curiosity": 0.72}},
    )
    assert second["organism_id"] == organism_id
    assert second["swap_count"] == 1
    swap = second["recent_swaps"][-1]
    assert swap["continuity"]["status"] == "PRESERVED"
    assert all(swap["continuity"]["checks"].values())
    assert swap["from"]["model"].startswith("deepseek/")
    assert swap["to"]["model"] == "openai/gpt-oss-20b"


def test_lineage_reset_is_detected_across_cortex_swap(tmp_path: Path):
    monitor = CortexContinuityMonitor(tmp_path)
    _append_audit(tmp_path, request_id="req:1", model="model-a", resource="provider-a:model-a")
    monitor.observe(
        neural_graph=_graph(event_seq=20, births=2, mutations=4),
        motor=_motor(seq=10),
    )

    _append_audit(tmp_path, request_id="req:2", model="model-b", resource="provider-b:model-b")
    projection = monitor.observe(
        neural_graph=_graph(event_seq=1, births=0, mutations=0),
        motor={"decision_seq": 1, "habits": [], "recent_decisions": []},
    )
    swap = projection["recent_swaps"][-1]
    assert swap["continuity"]["status"] == "DISCONTINUITY_DETECTED"
    assert swap["continuity"]["checks"]["development_event_seq_monotonic"] is False
    assert swap["continuity"]["checks"]["learned_habits_not_reset"] is False


def test_restart_preserves_organism_id_and_does_not_duplicate_same_request(tmp_path: Path):
    monitor = CortexContinuityMonitor(tmp_path)
    _append_audit(tmp_path, request_id="req:1", model="model-a", resource="provider-a:model-a")
    projection = monitor.observe(neural_graph=_graph(), motor=_motor())
    organism_id = projection["organism_id"]

    restored = CortexContinuityMonitor(tmp_path)
    same = restored.observe(neural_graph=_graph(), motor=_motor())
    assert same["organism_id"] == organism_id
    assert same["swap_count"] == 0
    assert len(same["recent_swaps"]) == 0


def test_routing_resource_change_counts_as_real_cortex_change(tmp_path: Path):
    monitor = CortexContinuityMonitor(tmp_path)
    _append_audit(tmp_path, request_id="req:1", model="same-model", resource="openrouter:route-a")
    monitor.observe(neural_graph=_graph(), motor=_motor())
    _append_audit(tmp_path, request_id="req:2", model="same-model", resource="nvidia:route-b")
    projection = monitor.observe(
        neural_graph=_graph(event_seq=11, mutations=3),
        motor=_motor(seq=6),
    )
    assert projection["swap_count"] == 1
    assert projection["recent_swaps"][-1]["continuity"]["status"] == "PRESERVED"
