from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from wechat_traffic_agent.neural_telemetry import NeuralTelemetrySampler


class _Weights:
    def __init__(self, n: int) -> None:
        self.shape = (n, n)
        indptr = [0]
        indices = []
        data = []
        for pre in range(n):
            indices.extend([(pre + 1) % n, (pre + 3) % n])
            data.extend([0.24, -0.11 if pre % 2 else 0.17])
            indptr.append(len(indices))
        self.indptr = np.asarray(indptr, dtype=np.int64)
        self.indices = np.asarray(indices, dtype=np.int64)
        self.data = np.asarray(data, dtype=float)

    def tocsc(self):
        return self


class _Base:
    def __init__(self, n: int = 48) -> None:
        self.np = np
        self.connectome = SimpleNamespace(n=n, weights=_Weights(n))
        self.net = SimpleNamespace(v=np.linspace(-66.0, -48.0, n))
        self._ports = {
            "curiosity": {
                "input": np.asarray([0, 2, 4, 6, 8, 10], dtype=np.int64),
                "readout": np.asarray([12, 14, 16, 18], dtype=np.int64),
            },
            "resource_pressure": {
                "input": np.asarray([20, 22, 24, 26], dtype=np.int64),
                "readout": np.asarray([28, 30, 32, 34], dtype=np.int64),
            },
        }


class _Substrate:
    def __init__(self) -> None:
        self.base = _Base()
        self._state = {
            "event_seq": 9,
            "neurons": {
                "ferro:1": {
                    "neuron_id": "ferro:1",
                    "ordinal": 1,
                    "generation": "FERRO_F0",
                    "state": "MATURE",
                    "membrane": 0.62,
                    "threshold": 0.8,
                    "last_fired_event_seq": 9,
                    "parent_channels": ["curiosity"],
                },
                "ferro:2": {
                    "neuron_id": "ferro:2",
                    "ordinal": 2,
                    "generation": "FERRO_F0",
                    "state": "EXPLORATORY",
                    "membrane": 0.25,
                    "threshold": 1.0,
                    "last_fired_event_seq": 8,
                    "parent_channels": ["resource_pressure"],
                    "recurrent_inputs": {"ferro:1": 0.37},
                },
            },
        }

    def projection(self):
        n = self.base.connectome.n
        return {
            "schema_version": "test-connectome",
            "neuron_count": n + 2,
            "connection_count": n * 2,
            "synthetic_connection_count": 6,
            "brain_time_ms": 412.5,
            "recent_bursts": [{
                "seq": 9,
                "observed_at": "2026-09-24T00:00:00+00:00",
                "spikes": 321,
                "active_neurons": 27,
                "synaptic_events": 804,
                "readout_hz": {"curiosity": 14.2},
                "delta_readout_hz": {"curiosity": 5.1},
                "response_spike_pattern_digest": "sha256:test",
            }],
            "developmental_neurogenesis": {
                "ancestral_neuron_count": n,
                "synthetic_neuron_count": 2,
                "total_neuron_count": n + 2,
            },
        }


class _Wakeful:
    def __init__(self) -> None:
        substrate = _Substrate()
        self.core = SimpleNamespace(reflex=SimpleNamespace(substrate=substrate))

    def public_snapshot(self):
        return {
            "state": "WAITING",
            "resource_pressure": "NORMAL",
            "neural_life": {
                "drives": {"survival": 0.2, "reproduction": 0.3, "evolution": 0.71},
                "affect": {"curiosity": 0.76, "confidence": 0.64},
                "intention": {"name": "EVOLVE", "strength": 0.79},
            },
            "neural_motor": {
                "recent_decisions": [{
                    "task": "REFLECT",
                    "authority": "NEURAL_DRIVE",
                    "status": "SETTLED",
                    "activation": 0.73,
                    "outcome": {"status": "SETTLED", "reward": 0.22},
                }],
            },
        }


def test_neural_telemetry_aggregates_whole_brain_and_real_subgraph():
    sampler = NeuralTelemetrySampler(_Wakeful())

    payload = sampler.snapshot()

    assert payload["schema_version"] == "ferro-neural-telemetry.v1"
    assert payload["status"] == "LIVE"
    assert payload["brain"]["total_neurons"] == 50
    assert payload["brain"]["ancestral_neurons"] == 48
    assert payload["brain"]["synthetic_neurons"] == 2
    assert sum(row["count"] for row in payload["brain"]["field"]) == 48
    assert payload["brain"]["field_meta"]["all_ancestral_neurons_included"] is True
    assert payload["brain"]["sample_is_complete_brain"] is False
    assert payload["brain"]["sample_nodes"]
    assert payload["brain"]["sample_edges"]
    assert all({"from", "to", "weight"} <= set(edge) for edge in payload["brain"]["sample_edges"])
    assert payload["brain"]["synthetic_sample"][0]["fired_latest_event"] is True
    synthetic = {row["id"]: row for row in payload["brain"]["synthetic_sample"]}
    assert synthetic["ferro:1"]["layout_basis"] == "parent_channels"
    assert synthetic["ferro:2"]["layout_basis"] == "parent_channels+recurrent_inputs"
    assert synthetic["ferro:2"]["recurrent_input_count"] == 1
    assert payload["brain"]["synthetic_edges"] == [{
        "from": "ferro:1",
        "to": "ferro:2",
        "weight": 0.37,
    }]

    anchors = {}
    for group in ("curiosity", "resource_pressure"):
        grouped = [row for row in payload["brain"]["sample_nodes"] if row["group"] == group]
        assert grouped
        anchors[group] = (
            sum(row["x"] for row in grouped) / len(grouped),
            sum(row["y"] for row in grouped) / len(grouped),
        )
    curiosity_distance = (
        (synthetic["ferro:1"]["x"] - anchors["curiosity"][0]) ** 2
        + (synthetic["ferro:1"]["y"] - anchors["curiosity"][1]) ** 2
    ) ** 0.5
    assert curiosity_distance < 0.09
    assert payload["burst"]["seq"] == 9
    assert payload["burst"]["spikes"] == 321
    assert payload["current_action"]["task"] == "REFLECT"
    assert "No per-edge spike path is invented" in payload["boundary"]


def test_neural_telemetry_compact_mode_bounds_frontend_payload():
    sampler = NeuralTelemetrySampler(_Wakeful())

    payload = sampler.snapshot(compact=True)

    assert payload["detail"] == "compact"
    assert len(payload["brain"]["field"]) <= 96
    assert len(payload["brain"]["sample_nodes"]) <= sampler.COMPACT_NODES
    assert len(payload["brain"]["sample_edges"]) <= sampler.COMPACT_EDGES


def test_neural_telemetry_reports_unavailable_without_live_substrate():
    sampler = NeuralTelemetrySampler(None)

    payload = sampler.snapshot()

    assert payload["status"] == "UNAVAILABLE"
    assert payload["reason"] == "live neural substrate is not attached"
