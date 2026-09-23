"""Bounded live circuit/neuron inspection for Ferro Neural Life V2.

This module is read-only. It exposes real substrate nodes and synapses while
keeping visualization geometry explicitly separate from biological/anatomical
claims.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


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


class NeuralCircuitInspector:
    SCHEMA = "ferro-neural-circuit.v1"
    NEURON_SCHEMA = "ferro-neuron-inspector.v1"
    NODES = 260
    EDGES = 720
    COMPACT_NODES = 120
    COMPACT_EDGES = 260

    def __init__(self, wakeful: Any | None) -> None:
        self.wakeful = wakeful

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
            return _mapping(method())
        except Exception:
            return {}

    @staticmethod
    def _ports(base: Any) -> dict[str, dict[str, list[int]]]:
        ports = getattr(base, "_ports", None)
        if not isinstance(ports, Mapping):
            return {}
        result: dict[str, dict[str, list[int]]] = {}
        for channel, raw in ports.items():
            if not isinstance(raw, Mapping):
                continue
            sides: dict[str, list[int]] = {}
            for side in ("input", "readout"):
                values = raw.get(side)
                if values is None:
                    continue
                try:
                    sides[side] = [int(value) for value in values]
                except Exception:
                    continue
            if sides:
                result[str(channel)] = sides
        return result

    @staticmethod
    def _normalize(values: Any, np: Any) -> Any:
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return arr
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return np.zeros_like(arr, dtype=float)
        lo = float(np.percentile(finite, 5))
        hi = float(np.percentile(finite, 95))
        if hi - lo < 1e-9:
            hi = lo + 1.0
        result = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
        return np.where(np.isfinite(result), result, 0.0)

    @classmethod
    def _roles(cls, base: Any) -> dict[int, list[dict[str, str]]]:
        result: dict[int, list[dict[str, str]]] = {}
        for channel, sides in cls._ports(base).items():
            for side, neuron_ids in sides.items():
                for neuron_id in neuron_ids:
                    result.setdefault(int(neuron_id), []).append({
                        "channel": channel,
                        "side": side,
                    })
        return result

    def catalog(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        substrate = self._substrate()
        if substrate is None:
            return {"schema_version": self.SCHEMA, "status": "UNAVAILABLE", "observed_at": now, "circuits": []}
        base = _unwrap_base(substrate)
        circuits: list[dict[str, Any]] = []
        for channel, sides in sorted(self._ports(base).items()):
            circuits.append({
                "selector": f"channel:{channel}",
                "kind": "ancestral_channel",
                "label": channel,
                "member_hint": len(set(sides.get("input", ()) + sides.get("readout", ()))),
            })
        projection = self._projection(substrate)
        development = projection.get("developmental_neurogenesis")
        if isinstance(development, Mapping):
            for row in development.get("circuits") or ():
                if not isinstance(row, Mapping):
                    continue
                circuit_id = str(row.get("circuit_id") or "")
                if not circuit_id:
                    continue
                circuits.append({
                    "selector": f"circuit:{circuit_id}",
                    "kind": "ferro_born_circuit",
                    "label": circuit_id,
                    "state": str(row.get("state") or "UNKNOWN"),
                    "member_hint": int(row.get("member_count") or 0),
                    "dominant_parent_channels": [str(x) for x in row.get("dominant_parent_channels") or ()],
                })
        return {
            "schema_version": self.SCHEMA,
            "status": "LIVE",
            "observed_at": now,
            "circuits": circuits,
        }

    def _channel_graph(self, base: Any, channel: str, *, compact: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ports = self._ports(base)
        port = ports.get(channel)
        if not port:
            return [], []
        connectome = getattr(base, "connectome", None)
        weights = getattr(connectome, "weights", None)
        np = getattr(base, "np", None)
        net = getattr(base, "net", None)
        values = getattr(net, "v", None)
        if weights is None or np is None or values is None:
            return [], []
        try:
            csc = weights.tocsc()
            raw = np.asarray(values, dtype=float)
            normalized = self._normalize(raw, np)
            neuron_count = int(getattr(connectome, "n", csc.shape[0]))
        except Exception:
            return [], []

        node_limit = self.COMPACT_NODES if compact else self.NODES
        edge_limit = self.COMPACT_EDGES if compact else self.EDGES
        seeds = list(dict.fromkeys(
            int(value)
            for side in ("input", "readout")
            for value in port.get(side, ())
            if 0 <= int(value) < neuron_count
        ))
        selected = set(seeds[:node_limit])
        depth = {neuron_id: 0 for neuron_id in selected}
        queue = list(selected)
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[int, int]] = set()
        qpos = 0
        while qpos < len(queue) and len(selected) < node_limit and len(edges) < edge_limit:
            source = queue[qpos]
            qpos += 1
            try:
                start, end = int(csc.indptr[source]), int(csc.indptr[source + 1])
                ranked = sorted(
                    (
                        (abs(float(weight)), int(target), float(weight))
                        for target, weight in zip(csc.indices[start:end], csc.data[start:end])
                        if 0 <= int(target) < neuron_count
                    ),
                    key=lambda row: (-row[0], row[1]),
                )[:10]
            except Exception:
                continue
            for magnitude, target, weight in ranked:
                if target not in selected and len(selected) < node_limit:
                    selected.add(target)
                    depth[target] = min(3, depth.get(source, 0) + 1)
                    queue.append(target)
                if target in selected and (source, target) not in seen_edges and len(edges) < edge_limit:
                    edges.append({
                        "from": source,
                        "to": target,
                        "weight": round(weight, 6),
                        "strength": round(min(1.0, magnitude), 5),
                        "kind": "ancestral_synapse",
                    })
                    seen_edges.add((source, target))

        for source in sorted(selected):
            if len(edges) >= edge_limit:
                break
            try:
                start, end = int(csc.indptr[source]), int(csc.indptr[source + 1])
            except Exception:
                continue
            for target, weight in zip(csc.indices[start:end], csc.data[start:end]):
                target = int(target)
                if target not in selected or (source, target) in seen_edges:
                    continue
                value = float(weight)
                edges.append({
                    "from": source,
                    "to": target,
                    "weight": round(value, 6),
                    "strength": round(min(1.0, abs(value)), 5),
                    "kind": "ancestral_synapse",
                })
                seen_edges.add((source, target))
                if len(edges) >= edge_limit:
                    break

        by_layer: dict[int, list[int]] = {}
        for neuron_id in selected:
            by_layer.setdefault(depth.get(neuron_id, 3), []).append(neuron_id)
        layout: dict[int, tuple[float, float]] = {}
        for layer, members in sorted(by_layer.items()):
            members.sort()
            radius = 0.08 + 0.115 * layer
            for idx, neuron_id in enumerate(members):
                angle = math.tau * idx / max(1, len(members)) + layer * 0.43
                jitter = (((neuron_id * 0.6180339887) % 1.0) - 0.5) * 0.025
                layout[neuron_id] = (
                    0.5 + (radius + jitter) * math.cos(angle),
                    0.5 + (radius + jitter) * math.sin(angle),
                )
        roles = self._roles(base)
        input_set = set(int(x) for x in port.get("input", ()))
        readout_set = set(int(x) for x in port.get("readout", ()))
        nodes = []
        for neuron_id in sorted(selected, key=lambda value: (depth.get(value, 3), value)):
            x, y = layout[neuron_id]
            nodes.append({
                "id": neuron_id,
                "kind": "ancestral",
                "group": channel,
                "depth": depth.get(neuron_id, 3),
                "activity": round(float(normalized[neuron_id]), 5),
                "membrane": round(float(raw[neuron_id]), 5),
                "x": round(x, 5),
                "y": round(y, 5),
                "is_input": neuron_id in input_set,
                "is_readout": neuron_id in readout_set,
                "roles": roles.get(neuron_id, []),
            })
        return nodes, edges

    def _synthetic_graph(self, substrate: Any, circuit_id: str, *, compact: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        state = getattr(substrate, "_state", None)
        if not isinstance(state, Mapping):
            return [], [], {}
        circuits = state.get("circuits")
        neurons = state.get("neurons")
        if not isinstance(circuits, Mapping) or not isinstance(neurons, Mapping):
            return [], [], {}
        raw_circuit = circuits.get(circuit_id)
        if not isinstance(raw_circuit, Mapping):
            return [], [], {}
        node_limit = self.COMPACT_NODES if compact else self.NODES
        edge_limit = self.COMPACT_EDGES if compact else self.EDGES
        members = [
            str(value)
            for value in raw_circuit.get("members") or ()
            if str(value) in neurons
        ][:node_limit]
        member_set = set(members)
        event_seq = int(state.get("event_seq") or 0)
        nodes = []
        count = max(1, len(members))
        for idx, neuron_id in enumerate(members):
            row = neurons[neuron_id]
            if not isinstance(row, Mapping):
                continue
            threshold = max(1e-9, _number(row.get("threshold"), 1.0))
            membrane = _number(row.get("membrane"), 0.0)
            fired = int(row.get("last_fired_event_seq") or -1) == event_seq
            radius = 0.10 + 0.24 * math.sqrt((idx + 1) / count)
            angle = idx * math.pi * (3.0 - math.sqrt(5.0))
            nodes.append({
                "id": neuron_id,
                "kind": "ferro_born",
                "state": str(row.get("state") or "UNKNOWN"),
                "generation": str(row.get("generation") or ""),
                "activity": 1.0 if fired else round(max(0.0, min(1.0, membrane / threshold)), 5),
                "membrane": round(membrane, 6),
                "threshold": round(threshold, 6),
                "fired_latest_event": fired,
                "parent_channels": [str(x) for x in row.get("parent_channels") or ()],
                "x": round(0.5 + radius * math.cos(angle), 5),
                "y": round(0.5 + radius * math.sin(angle), 5),
            })
        edges = []
        for target in members:
            row = neurons.get(target)
            recurrent = row.get("recurrent_inputs") if isinstance(row, Mapping) else None
            if not isinstance(recurrent, Mapping):
                continue
            for source, weight in recurrent.items():
                source = str(source)
                if source not in member_set:
                    continue
                value = _number(weight)
                edges.append({
                    "from": source,
                    "to": target,
                    "weight": round(value, 6),
                    "strength": round(min(1.0, abs(value)), 5),
                    "kind": "synthetic_recurrent",
                })
                if len(edges) >= edge_limit:
                    break
            if len(edges) >= edge_limit:
                break
        metrics = raw_circuit.get("metrics") if isinstance(raw_circuit.get("metrics"), Mapping) else {}
        meta = {
            "circuit_id": circuit_id,
            "state": str(raw_circuit.get("state") or "UNKNOWN"),
            "member_count": int(metrics.get("member_count") or len(raw_circuit.get("members") or ())),
            "mature_member_count": int(metrics.get("mature_member_count") or 0),
            "stability_events": int(raw_circuit.get("stability_events") or 0),
            "active_events": int(raw_circuit.get("active_events") or 0),
            "internal_edge_ratio": round(_number(metrics.get("internal_edge_ratio")), 6),
            "dominant_parent_channels": [str(x) for x in metrics.get("dominant_parent_channels") or ()],
        }
        return nodes, edges, meta

    def circuit(self, selector: str, *, compact: bool = False) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        selector = str(selector or "").strip()
        substrate = self._substrate()
        if substrate is None:
            return {"schema_version": self.SCHEMA, "status": "UNAVAILABLE", "observed_at": now, "selector": selector}
        base = _unwrap_base(substrate)
        meta: dict[str, Any] = {}
        if selector.startswith("channel:"):
            name = selector.split(":", 1)[1]
            nodes, edges = self._channel_graph(base, name, compact=compact)
            kind, title, meta = "ancestral_channel", name, {"channel": name}
        elif selector.startswith("circuit:"):
            name = selector.split(":", 1)[1]
            nodes, edges, meta = self._synthetic_graph(substrate, name, compact=compact)
            kind, title = "ferro_born_circuit", name
        else:
            return {"schema_version": self.SCHEMA, "status": "INVALID_SELECTOR", "observed_at": now, "selector": selector}
        return {
            "schema_version": self.SCHEMA,
            "status": "LIVE" if nodes else "EMPTY",
            "observed_at": now,
            "selector": selector,
            "kind": kind,
            "title": title,
            "detail": "compact" if compact else "full",
            "nodes": nodes,
            "edges": edges,
            "meta": meta,
            "boundary": "Every node and edge is read from the live substrate; coordinates are visualization geometry, not anatomy.",
        }

    @staticmethod
    def _strongest(mapping: Mapping[Any, Any], *, label: str) -> list[dict[str, Any]]:
        return [
            {label: str(key), "weight": round(_number(value), 6)}
            for key, value in sorted(
                mapping.items(),
                key=lambda item: (-abs(_number(item[1])), str(item[0])),
            )[:24]
        ]

    def neuron(self, neuron_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        substrate = self._substrate()
        raw_id = str(neuron_id or "").strip()
        if substrate is None:
            return {"schema_version": self.NEURON_SCHEMA, "status": "UNAVAILABLE", "observed_at": now, "id": raw_id}
        state = getattr(substrate, "_state", None)
        neurons = state.get("neurons") if isinstance(state, Mapping) else None
        if isinstance(neurons, Mapping) and raw_id in neurons and isinstance(neurons[raw_id], Mapping):
            row = neurons[raw_id]
            recurrent = row.get("recurrent_inputs") if isinstance(row.get("recurrent_inputs"), Mapping) else {}
            input_weights = row.get("input_weights") if isinstance(row.get("input_weights"), Mapping) else {}
            output_weights = row.get("output_weights") if isinstance(row.get("output_weights"), Mapping) else {}
            threshold = max(1e-9, _number(row.get("threshold"), 1.0))
            membrane = _number(row.get("membrane"), 0.0)
            return {
                "schema_version": self.NEURON_SCHEMA,
                "status": "LIVE",
                "observed_at": now,
                "id": raw_id,
                "kind": "ferro_born",
                "state": str(row.get("state") or "UNKNOWN"),
                "generation": str(row.get("generation") or ""),
                "membrane": round(membrane, 6),
                "threshold": round(threshold, 6),
                "activity": round(max(0.0, min(1.0, membrane / threshold)), 6),
                "parent_channels": [str(x) for x in row.get("parent_channels") or ()],
                "recurrent_inputs": self._strongest(recurrent, label="source"),
                "input_weights": self._strongest(input_weights, label="source"),
                "output_weights": self._strongest(output_weights, label="target"),
            }

        base = _unwrap_base(substrate)
        try:
            numeric_id = int(raw_id)
        except (TypeError, ValueError):
            return {"schema_version": self.NEURON_SCHEMA, "status": "NOT_FOUND", "observed_at": now, "id": raw_id}
        connectome = getattr(base, "connectome", None)
        weights = getattr(connectome, "weights", None)
        np = getattr(base, "np", None)
        net = getattr(base, "net", None)
        values = getattr(net, "v", None)
        if weights is None or np is None or values is None:
            return {"schema_version": self.NEURON_SCHEMA, "status": "NOT_FOUND", "observed_at": now, "id": raw_id}
        try:
            csc = weights.tocsc()
            raw = np.asarray(values, dtype=float)
            if numeric_id < 0 or numeric_id >= int(raw.size):
                raise IndexError
            normalized = self._normalize(raw, np)
            start, end = int(csc.indptr[numeric_id]), int(csc.indptr[numeric_id + 1])
            outgoing = sorted(
                (
                    {"target": int(target), "weight": round(float(weight), 6)}
                    for target, weight in zip(csc.indices[start:end], csc.data[start:end])
                ),
                key=lambda row: (-abs(row["weight"]), row["target"]),
            )[:24]
        except Exception:
            return {"schema_version": self.NEURON_SCHEMA, "status": "NOT_FOUND", "observed_at": now, "id": raw_id}
        return {
            "schema_version": self.NEURON_SCHEMA,
            "status": "LIVE",
            "observed_at": now,
            "id": numeric_id,
            "kind": "ancestral",
            "membrane": round(float(raw[numeric_id]), 6),
            "activity": round(float(normalized[numeric_id]), 6),
            "roles": self._roles(base).get(numeric_id, []),
            "outgoing_synapses": outgoing,
            "incoming_scope": "visible circuit only; full incoming scan is omitted from the public hot path",
        }


__all__ = ["NeuralCircuitInspector"]
