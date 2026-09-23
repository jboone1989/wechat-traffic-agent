"""Bounded real neural telemetry for Ferro's public Neural Life view.

The telemetry layer never simulates decorative neural events.  It samples the
live persistent substrate, aggregates the whole ancestral membrane vector into
bounded visual bins, and exposes a small real subgraph around engineered
semantic electrodes.  Geometry is explicitly visualization-only; graph edges
and neural values come from the live substrate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return number


def _unwrap_base(substrate: Any) -> Any:
    current = substrate
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        base = getattr(current, "base", None)
        if base is None:
            break
        current = base
    return current


class NeuralTelemetrySampler:
    """Create a stable, bounded projection from the real live neural substrate."""

    SCHEMA_VERSION = "ferro-neural-telemetry.v1"
    DEFAULT_FIELD_BINS = 192
    DEFAULT_NODES = 720
    DEFAULT_EDGES = 1200
    COMPACT_NODES = 280
    COMPACT_EDGES = 420

    def __init__(self, wakeful: Any | None) -> None:
        self.wakeful = wakeful
        self._topology_key: tuple[Any, ...] | None = None
        self._nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []

    def _public_state(self) -> dict[str, Any]:
        if self.wakeful is None:
            return {}
        method = getattr(self.wakeful, "public_snapshot", None)
        if not callable(method):
            return {}
        try:
            value = method()
        except Exception:
            return {}
        return _mapping(value)

    def _substrate(self) -> Any | None:
        core = getattr(self.wakeful, "core", None)
        reflex = getattr(core, "reflex", None)
        return getattr(reflex, "substrate", None)

    @staticmethod
    def _projection(substrate: Any) -> dict[str, Any]:
        method = getattr(substrate, "projection", None)
        if not callable(method):
            return {}
        try:
            value = method()
        except Exception:
            return {}
        return _mapping(value)

    @staticmethod
    def _latest_burst(projection: Mapping[str, Any]) -> dict[str, Any]:
        bursts = projection.get("recent_bursts")
        if not isinstance(bursts, list):
            return {}
        for row in reversed(bursts):
            if isinstance(row, Mapping):
                return dict(row)
        return {}

    @staticmethod
    def _normalize_membrane(values: Any, np: Any) -> tuple[Any, float, float]:
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return arr, 0.0, 1.0
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return np.zeros_like(arr, dtype=float), 0.0, 1.0
        lo = float(np.percentile(finite, 5))
        hi = float(np.percentile(finite, 95))
        if not math.isfinite(lo):
            lo = float(finite.min())
        if not math.isfinite(hi):
            hi = float(finite.max())
        if hi - lo < 1e-9:
            hi = lo + 1.0
        normalized = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
        normalized = np.where(np.isfinite(normalized), normalized, 0.0)
        return normalized, lo, hi

    def _whole_field(self, base: Any, *, bin_count: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        np = getattr(base, "np", None)
        net = getattr(base, "net", None)
        values = getattr(net, "v", None)
        if np is None or values is None:
            return [], {"source": "unavailable"}
        try:
            raw = np.asarray(values, dtype=float)
            normalized, lo, hi = self._normalize_membrane(raw, np)
        except Exception:
            return [], {"source": "unavailable"}
        n = int(normalized.size)
        if n <= 0:
            return [], {"source": "unavailable"}
        count = max(1, min(int(bin_count), n))
        bounds = np.linspace(0, n, count + 1, dtype=int)
        rows: list[dict[str, Any]] = []
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for idx in range(count):
            start, end = int(bounds[idx]), int(bounds[idx + 1])
            if end <= start:
                continue
            segment = normalized[start:end]
            raw_segment = raw[start:end]
            mean_activity = float(segment.mean()) if segment.size else 0.0
            peak_activity = float(segment.max()) if segment.size else 0.0
            mean_mv = float(raw_segment.mean()) if raw_segment.size else 0.0
            radius = math.sqrt((idx + 0.5) / count) * 0.46
            angle = idx * golden
            rows.append({
                "bin": idx,
                "start_neuron": start,
                "end_neuron": end - 1,
                "count": end - start,
                "activity": round(mean_activity, 5),
                "peak": round(peak_activity, 5),
                "mean_membrane": round(mean_mv, 5),
                "x": round(0.5 + radius * math.cos(angle), 5),
                "y": round(0.5 + radius * math.sin(angle), 5),
            })
        return rows, {
            "source": "live_membrane_vector",
            "neuron_count": n,
            "normalization_p05": round(lo, 5),
            "normalization_p95": round(hi, 5),
            "all_ancestral_neurons_included": True,
            "geometry": "deterministic_index_shards_visualization_only",
        }

    @staticmethod
    def _ports(base: Any) -> dict[str, dict[str, list[int]]]:
        ports = getattr(base, "_ports", None)
        if not isinstance(ports, Mapping):
            return {}
        result: dict[str, dict[str, list[int]]] = {}
        for channel, raw in ports.items():
            if not isinstance(raw, Mapping):
                continue
            entry: dict[str, list[int]] = {}
            for side in ("input", "readout"):
                values = raw.get(side)
                if values is None:
                    continue
                try:
                    entry[side] = [int(x) for x in values]
                except Exception:
                    continue
            if entry:
                result[str(channel)] = entry
        return result

    def _build_topology(self, base: Any, projection: Mapping[str, Any]) -> None:
        connectome = getattr(base, "connectome", None)
        weights = getattr(connectome, "weights", None)
        if weights is None:
            self._nodes, self._edges = [], []
            return
        try:
            csc = weights.tocsc()
            n = int(getattr(connectome, "n", csc.shape[0]))
        except Exception:
            self._nodes, self._edges = [], []
            return

        ports = self._ports(base)
        key = (
            n,
            int(projection.get("connection_count") or 0),
            tuple((name, tuple(side.get("input", ())), tuple(side.get("readout", ())))
                  for name, side in sorted(ports.items())),
        )
        if key == self._topology_key and self._nodes:
            return

        role_by_id: dict[int, list[tuple[str, str]]] = {}
        seeds: list[tuple[int, str]] = []
        for channel, sides in sorted(ports.items()):
            for side, values in sides.items():
                for neuron_id in values:
                    if 0 <= neuron_id < n:
                        role_by_id.setdefault(neuron_id, []).append((channel, side))
                if values:
                    stride = max(1, len(values) // 12)
                    sampled = values[::stride][:12]
                    for neuron_id in sampled:
                        if 0 <= neuron_id < n:
                            seeds.append((neuron_id, channel))

        if not seeds:
            step = max(1, n // 64)
            seeds = [(idx, "connectome") for idx in range(0, n, step)][:64]

        node_group: dict[int, str] = {}
        selected: set[int] = set()
        queue: list[tuple[int, str]] = []
        for neuron_id, channel in seeds:
            if neuron_id in selected:
                continue
            selected.add(neuron_id)
            node_group[neuron_id] = channel
            queue.append((neuron_id, channel))
            if len(selected) >= self.DEFAULT_NODES:
                break

        edges: list[dict[str, Any]] = []
        qpos = 0
        while qpos < len(queue) and len(selected) < self.DEFAULT_NODES and len(edges) < self.DEFAULT_EDGES:
            pre, channel = queue[qpos]
            qpos += 1
            try:
                start, end = int(csc.indptr[pre]), int(csc.indptr[pre + 1])
                posts = csc.indices[start:end]
                data = csc.data[start:end]
            except Exception:
                continue
            ranked = sorted(
                ((abs(float(weight)), int(post), float(weight))
                 for post, weight in zip(posts, data)),
                key=lambda row: (-row[0], row[1]),
            )[:8]
            for magnitude, post, weight in ranked:
                if not (0 <= post < n):
                    continue
                if post not in selected and len(selected) < self.DEFAULT_NODES:
                    selected.add(post)
                    node_group[post] = channel
                    queue.append((post, channel))
                if post in selected and len(edges) < self.DEFAULT_EDGES:
                    edges.append({
                        "from": pre,
                        "to": post,
                        "weight": round(weight, 6),
                        "strength": round(min(1.0, magnitude), 5),
                    })

        # Ensure seed-to-seed real edges are included even when the graph walk hit
        # the node limit early.
        selected_lookup = set(selected)
        if len(edges) < self.DEFAULT_EDGES:
            seen_pairs = {(row["from"], row["to"]) for row in edges}
            for pre in sorted(selected):
                if len(edges) >= self.DEFAULT_EDGES:
                    break
                try:
                    start, end = int(csc.indptr[pre]), int(csc.indptr[pre + 1])
                except Exception:
                    continue
                for post, weight in zip(csc.indices[start:end], csc.data[start:end]):
                    post = int(post)
                    pair = (pre, post)
                    if post not in selected_lookup or pair in seen_pairs:
                        continue
                    value = float(weight)
                    edges.append({
                        "from": pre,
                        "to": post,
                        "weight": round(value, 6),
                        "strength": round(min(1.0, abs(value)), 5),
                    })
                    seen_pairs.add(pair)
                    if len(edges) >= self.DEFAULT_EDGES:
                        break

        channels = sorted(set(node_group.values()))
        channel_index = {name: idx for idx, name in enumerate(channels)}
        nodes: list[dict[str, Any]] = []
        for neuron_id in sorted(selected):
            group = node_group.get(neuron_id, "connectome")
            gi = channel_index.get(group, 0)
            hub_angle = (math.tau * gi / max(1, len(channels))) - math.pi / 2
            hub_x = 0.5 + 0.30 * math.cos(hub_angle)
            hub_y = 0.5 + 0.30 * math.sin(hub_angle)
            local_angle = ((neuron_id * 0.6180339887498949) % 1.0) * math.tau
            local_radius = 0.025 + ((neuron_id * 0.41421356237) % 1.0) * 0.095
            roles = role_by_id.get(neuron_id, [])
            nodes.append({
                "id": neuron_id,
                "group": group,
                "x": round(hub_x + local_radius * math.cos(local_angle), 5),
                "y": round(hub_y + local_radius * math.sin(local_angle), 5),
                "roles": [{"channel": ch, "side": side} for ch, side in roles],
            })

        self._topology_key = key
        self._nodes = nodes
        self._edges = edges

    def _live_nodes(self, base: Any, *, compact: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        limit = self.COMPACT_NODES if compact else self.DEFAULT_NODES
        edge_limit = self.COMPACT_EDGES if compact else self.DEFAULT_EDGES
        nodes = self._nodes[:limit]
        selected = {int(row["id"]) for row in nodes}
        edges = [row for row in self._edges if row["from"] in selected and row["to"] in selected][:edge_limit]

        np = getattr(base, "np", None)
        net = getattr(base, "net", None)
        values = getattr(net, "v", None)
        if np is None or values is None:
            return [dict(row, activity=0.0, membrane=None) for row in nodes], edges
        try:
            raw = np.asarray(values, dtype=float)
            normalized, _lo, _hi = self._normalize_membrane(raw, np)
        except Exception:
            return [dict(row, activity=0.0, membrane=None) for row in nodes], edges
        live: list[dict[str, Any]] = []
        for row in nodes:
            neuron_id = int(row["id"])
            if 0 <= neuron_id < int(raw.size):
                live.append(dict(
                    row,
                    activity=round(float(normalized[neuron_id]), 5),
                    membrane=round(float(raw[neuron_id]), 5),
                ))
            else:
                live.append(dict(row, activity=0.0, membrane=None))
        return live, edges

    @staticmethod
    def _synthetic_nodes(substrate: Any, projection: Mapping[str, Any], *, compact: bool) -> list[dict[str, Any]]:
        state = getattr(substrate, "_state", None)
        neurons = state.get("neurons") if isinstance(state, Mapping) else None
        if not isinstance(neurons, Mapping):
            return []
        event_seq = int(state.get("event_seq") or 0)
        limit = 80 if compact else 240
        rows: list[dict[str, Any]] = []
        alive = [
            row for row in neurons.values()
            if isinstance(row, Mapping) and str(row.get("state") or "") != "DEAD"
        ]
        alive.sort(key=lambda row: int(row.get("ordinal") or 0))
        for idx, row in enumerate(alive[:limit]):
            threshold = max(1e-9, _bounded_float(row.get("threshold"), 1.0))
            membrane = _bounded_float(row.get("membrane"), 0.0)
            fired = int(row.get("last_fired_event_seq") or -1) == event_seq
            angle = ((int(row.get("ordinal") or idx) * 0.754877666) % 1.0) * math.tau
            radius = 0.12 + 0.025 * ((idx % 7) / 6.0)
            rows.append({
                "id": str(row.get("neuron_id") or f"synthetic:{idx}"),
                "state": str(row.get("state") or "UNKNOWN"),
                "generation": str(row.get("generation") or ""),
                "activity": 1.0 if fired else round(max(0.0, min(1.0, membrane / threshold)), 5),
                "fired_latest_event": fired,
                "x": round(0.5 + radius * math.cos(angle), 5),
                "y": round(0.5 + radius * math.sin(angle), 5),
                "parent_channels": [str(x) for x in row.get("parent_channels") or ()],
            })
        return rows

    @staticmethod
    def _current_action(public: Mapping[str, Any]) -> dict[str, Any] | None:
        motor = public.get("neural_motor") if isinstance(public.get("neural_motor"), Mapping) else {}
        recent = motor.get("recent_decisions") if isinstance(motor.get("recent_decisions"), list) else []
        for row in reversed(recent):
            if not isinstance(row, Mapping):
                continue
            outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
            return {
                "task": str(row.get("task") or row.get("motor_id") or ""),
                "authority": str(row.get("authority") or row.get("decision_kind") or "NEURAL"),
                "status": str(outcome.get("status") or row.get("status") or "PENDING"),
                "activation": round(_bounded_float(row.get("activation")), 5),
                "reward": (
                    round(_bounded_float(outcome.get("reward")), 5)
                    if outcome.get("reward") is not None else None
                ),
            }
        life = public.get("neural_life") if isinstance(public.get("neural_life"), Mapping) else {}
        action = life.get("last_authority_action")
        return dict(action) if isinstance(action, Mapping) else None

    def snapshot(self, *, compact: bool = False) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        public = self._public_state()
        life = public.get("neural_life") if isinstance(public.get("neural_life"), Mapping) else {}
        substrate = self._substrate()
        if substrate is None:
            return {
                "schema_version": self.SCHEMA_VERSION,
                "status": "UNAVAILABLE",
                "observed_at": now.isoformat(),
                "reason": "live neural substrate is not attached",
                "drives": dict(life.get("drives") or {}),
                "intention": dict(life.get("intention") or {}),
                "current_action": self._current_action(public),
            }

        projection = self._projection(substrate)
        base = _unwrap_base(substrate)
        latest = self._latest_burst(projection)
        field_bins, field_meta = self._whole_field(
            base,
            bin_count=(96 if compact else self.DEFAULT_FIELD_BINS),
        )
        self._build_topology(base, projection)
        nodes, edges = self._live_nodes(base, compact=compact)
        synthetic = self._synthetic_nodes(substrate, projection, compact=compact)
        development = projection.get("developmental_neurogenesis")
        development = dict(development) if isinstance(development, Mapping) else {}
        total_neurons = int(
            development.get("total_neuron_count")
            or projection.get("neuron_count")
            or field_meta.get("neuron_count")
            or 0
        )
        total_connections = int(projection.get("connection_count") or 0) + int(
            projection.get("synthetic_connection_count") or 0
        )
        return {
            "schema_version": self.SCHEMA_VERSION,
            "status": "LIVE" if field_bins else "DEGRADED",
            "observed_at": now.isoformat(),
            "detail": "compact" if compact else "full",
            "brain": {
                "total_neurons": total_neurons,
                "ancestral_neurons": int(development.get("ancestral_neuron_count") or field_meta.get("neuron_count") or 0),
                "synthetic_neurons": int(development.get("synthetic_neuron_count") or 0),
                "total_connections": total_connections,
                "brain_time_ms": round(_bounded_float(projection.get("brain_time_ms")), 3),
                "field": field_bins,
                "field_meta": field_meta,
                "sample_nodes": nodes,
                "sample_edges": edges,
                "synthetic_sample": synthetic,
                "sample_is_complete_brain": False,
                "sample_semantics": "real subgraph around engineered semantic electrodes; geometry is visualization-only",
            },
            "burst": {
                "seq": int(latest.get("seq") or 0) if latest else None,
                "observed_at": latest.get("observed_at"),
                "spikes": int(latest.get("spikes") or 0),
                "active_neurons": int(latest.get("active_neurons") or 0),
                "synaptic_events": int(latest.get("synaptic_events") or 0),
                "readout_hz": dict(latest.get("readout_hz") or {}),
                "delta_readout_hz": dict(latest.get("delta_readout_hz") or {}),
                "pattern_digest": latest.get("response_spike_pattern_digest"),
            } if latest else None,
            "drives": dict(life.get("drives") or {}),
            "affect": dict(life.get("affect") or {}),
            "intention": dict(life.get("intention") or {}),
            "current_action": self._current_action(public),
            "resource_pressure": public.get("resource_pressure"),
            "boundary": (
                "whole-connectome field aggregates every ancestral neuron; detailed nodes are a bounded real subgraph. "
                "No per-edge spike path is invented when the substrate only exposes aggregate burst evidence."
            ),
        }


__all__ = ["NeuralTelemetrySampler"]
