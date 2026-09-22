"""N33 prospective selection of Ferro developmental architecture.

The architecture search changes effective developmental gene values only during
pre-assigned treatment trials. Trial overlays are reversible: they never become
an inherited phenotype until real target-task outcomes have settled four trials
per variant. The selected phenotype is then re-applied on every runtime start.

This module does not use an LLM and does not treat Brain Lab reward as selection
evidence. Selection utility must come from the downstream real-world target task.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA = "ferro-developmental-architecture.v1"
MIN_PER_VARIANT = 4
MAX_TRIALS = 256
VARIANTS: dict[str, dict[str, float]] = {
    "ADAPTIVE_FAST": {
        "birth_threshold_scale": 0.90,
        "maturation_time_scale": 0.80,
        "synaptogenesis_scale": 1.08,
        "recurrent_affinity_scale": 1.05,
        "dormancy_resilience_scale": 0.95,
        "organ_threshold_scale": 0.92,
    },
    "CONSERVATIVE_STABLE": {
        "birth_threshold_scale": 1.10,
        "maturation_time_scale": 1.20,
        "synaptogenesis_scale": 0.94,
        "recurrent_affinity_scale": 0.96,
        "dormancy_resilience_scale": 1.12,
        "organ_threshold_scale": 1.10,
    },
}


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


class DevelopmentalArchitectureSearch:
    """Balanced crossover search over real developmental phenotype overlays."""

    def __init__(self, state_root: Path, *, substrate: Any | None = None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.path = self.state_root / "application" / "developmental_architecture.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "seq": 0,
            "selected_variant": None,
            "selected_at": None,
            "trials": [],
            "selection_history": [],
        }
        self.apply_selected()

    def _supports_overlay(self) -> bool:
        return bool(
            self.substrate is not None
            and callable(getattr(self.substrate, "_gene", None))
            and getattr(self.substrate, "genome", None) is not None
        )

    @staticmethod
    def _assignment(seq: int, world_id: str) -> str:
        block = (int(seq) - 1) // 2
        within = (int(seq) - 1) % 2
        first_adaptive = bool(
            sha256(f"ferro-developmental-architecture:{world_id}:{block}".encode()).digest()[0] & 1
        )
        adaptive = first_adaptive if within == 0 else not first_adaptive
        return "ADAPTIVE_FAST" if adaptive else "CONSERVATIVE_STABLE"

    def assign(
        self,
        *,
        world_id: str,
        source_trial_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.state.get("selected_variant"):
            return {
                "status": "ARCHITECTURE_ALREADY_SELECTED",
                "selected_variant": self.state.get("selected_variant"),
                "trial": None,
            }
        if not self._supports_overlay():
            return {"status": "SUBSTRATE_OVERLAY_UNAVAILABLE", "trial": None}

        seq = int(self.state.get("seq") or 0) + 1
        assignment = self._assignment(seq, str(world_id))
        trial = {
            "trial_id": f"architecture:{seq:06d}",
            "seq": seq,
            "world_id": str(world_id),
            "source_trial_id": str(source_trial_id),
            "assignment": assignment,
            "status": "ASSIGNED",
            "assigned_at": now.isoformat(),
            "target_outcome": None,
            "variant_multipliers": dict(VARIANTS[assignment]),
        }
        rows = list(self.state.get("trials") or ())
        rows.append(trial)
        self.state.update({
            "seq": seq,
            "updated_at": now.isoformat(),
            "trials": rows[-MAX_TRIALS:],
        })
        _write(self.path, self.state)
        return {"status": "ASSIGNED", "trial": dict(trial)}

    def _base_gene_reader(self):
        substrate = self.substrate
        if substrate is None:
            return None
        base = getattr(substrate, "_ferro_architecture_base_gene_reader", None)
        if callable(base):
            return base
        current = getattr(substrate, "_gene", None)
        if callable(current):
            setattr(substrate, "_ferro_architecture_base_gene_reader", current)
            return current
        return None

    def _install_overlay(self, variant: str | None) -> bool:
        if not self._supports_overlay():
            return False
        substrate = self.substrate
        base = self._base_gene_reader()
        if base is None:
            return False
        multipliers = dict(VARIANTS.get(str(variant), {}))
        if not multipliers:
            substrate._gene = base
            setattr(substrate, "_ferro_architecture_variant", None)
            return True

        def effective_gene(name: str) -> float:
            raw = float(base(name))
            return raw * float(multipliers.get(str(name), 1.0))

        substrate._gene = effective_gene
        setattr(substrate, "_ferro_architecture_variant", str(variant))
        return True

    @contextmanager
    def trial_overlay(self, trial_id: str) -> Iterator[dict[str, Any]]:
        row = next(
            (
                dict(value)
                for value in reversed(self.state.get("trials") or ())
                if str(value.get("trial_id") or "") == str(trial_id)
            ),
            None,
        )
        if row is None:
            raise KeyError(f"unknown architecture trial: {trial_id}")
        prior_reader = getattr(self.substrate, "_gene", None) if self.substrate is not None else None
        prior_variant = (
            getattr(self.substrate, "_ferro_architecture_variant", None)
            if self.substrate is not None
            else None
        )
        genome = getattr(self.substrate, "genome", None) if self.substrate is not None else None
        genome_snapshot = genome.snapshot() if genome is not None and hasattr(genome, "snapshot") else None
        installed = self._install_overlay(str(row["assignment"]))
        try:
            yield {
                "status": "OVERLAY_ACTIVE" if installed else "OVERLAY_UNAVAILABLE",
                "assignment": row["assignment"],
                "trial_id": row["trial_id"],
            }
        finally:
            if self.substrate is not None and callable(prior_reader):
                self.substrate._gene = prior_reader
                setattr(self.substrate, "_ferro_architecture_variant", prior_variant)
            # Trial architecture itself must not rewrite the inherited genome.
            # Structural/synaptic consequences on the production nervous system
            # remain real, but baseline genome selection is restored until a
            # phenotype wins prospective real-world selection.
            if genome is not None and isinstance(genome_snapshot, Mapping):
                state = getattr(genome, "state", None)
                if isinstance(state, dict):
                    state.clear()
                    state.update({
                        "schema_version": genome_snapshot.get("schema_version"),
                        "ancestral_anchor": genome_snapshot.get("ancestral_anchor"),
                        "genes": dict(genome_snapshot.get("genes") or {}),
                        "mutation_count": int(genome_snapshot.get("mutation_count") or 0),
                        "history": [dict(value) for value in genome_snapshot.get("history") or ()],
                    })
                    substrate_state = getattr(self.substrate, "_state", None)
                    if isinstance(substrate_state, dict):
                        substrate_state["developmental_genome"] = genome.snapshot()

    def _evaluation(
        self,
        now: datetime | None = None,
        *,
        allow_selection: bool = False,
    ) -> dict[str, Any]:
        settled = [
            dict(row)
            for row in self.state.get("trials") or ()
            if row.get("status") == "SETTLED"
        ]
        arms: dict[str, dict[str, Any]] = {}
        for variant in VARIANTS:
            subset = [row for row in settled if row.get("assignment") == variant]
            values = [
                float((row.get("target_outcome") or {}).get("utility"))
                for row in subset
                if (row.get("target_outcome") or {}).get("utility") is not None
            ]
            arms[variant] = {
                "count": len(values),
                "mean_target_utility": None if not values else round(sum(values) / len(values), 6),
            }
        ready = all(arms[name]["count"] >= MIN_PER_VARIANT for name in VARIANTS)
        selected = self.state.get("selected_variant")
        effect = None
        if all(arms[name]["mean_target_utility"] is not None for name in VARIANTS):
            effect = round(
                float(arms["ADAPTIVE_FAST"]["mean_target_utility"])
                - float(arms["CONSERVATIVE_STABLE"]["mean_target_utility"]),
                6,
            )
        if ready and not selected and allow_selection:
            # Real outcomes choose the phenotype. Exact ties deliberately prefer
            # the conservative architecture rather than manufacturing novelty.
            selected = (
                "ADAPTIVE_FAST"
                if float(effect or 0.0) > 0.0
                else "CONSERVATIVE_STABLE"
            )
            stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            self.state["selected_variant"] = selected
            self.state["selected_at"] = stamp.isoformat()
            history = list(self.state.get("selection_history") or ())
            history.append({
                "selected_variant": selected,
                "selected_at": stamp.isoformat(),
                "selected_at_trial_seq": int(self.state.get("seq") or 0),
                "effect_adaptive_minus_conservative": effect,
                "arms": arms,
                "causal_source": "REAL_TARGET_OUTCOMES",
            })
            self.state["selection_history"] = history[-32:]
            self.state["updated_at"] = stamp.isoformat()
            _write(self.path, self.state)
            self.apply_selected()
        return {
            "status": "SELECTED" if selected else "COLLECTING_EVIDENCE",
            "ready": ready,
            "selected_variant": selected,
            "arms": arms,
            "effect_adaptive_minus_conservative": effect,
            "minimum_per_variant": MIN_PER_VARIANT,
        }

    def settle(
        self,
        *,
        trial_id: str,
        utility: float | None,
        target_status: str,
        observed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = list(self.state.get("trials") or ())
        index = next(
            (
                idx
                for idx in range(len(rows) - 1, -1, -1)
                if str(rows[idx].get("trial_id") or "") == str(trial_id)
                and rows[idx].get("status") == "ASSIGNED"
            ),
            None,
        )
        if index is None:
            return None
        row = dict(rows[index])
        if utility is None:
            row["status"] = "INCONCLUSIVE"
            row["target_outcome"] = {
                "observed_at": observed_at.isoformat(),
                "status": str(target_status),
                "utility": None,
            }
        else:
            row["status"] = "SETTLED"
            row["target_outcome"] = {
                "observed_at": observed_at.isoformat(),
                "status": str(target_status),
                "utility": max(-1.0, min(1.0, float(utility))),
            }
        rows[index] = row
        self.state.update({
            "updated_at": observed_at.isoformat(),
            "trials": rows[-MAX_TRIALS:],
        })
        _write(self.path, self.state)
        return {**row, "evaluation": self._evaluation(observed_at, allow_selection=True)}

    def apply_selected(self) -> dict[str, Any]:
        selected = str(self.state.get("selected_variant") or "")
        if not selected:
            return {"status": "NO_SELECTED_ARCHITECTURE", "selected_variant": None}
        applied = self._install_overlay(selected)
        return {
            "status": "APPLIED" if applied else "SUBSTRATE_OVERLAY_UNAVAILABLE",
            "selected_variant": selected,
            "multipliers": dict(VARIANTS.get(selected, {})),
        }

    def projection(self) -> dict[str, Any]:
        evaluation = self._evaluation()
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "selected_variant": self.state.get("selected_variant"),
            "selected_at": self.state.get("selected_at"),
            "inherited_application": self.apply_selected(),
            "trial_count": len(self.state.get("trials") or ()),
            "trials": [dict(row) for row in (self.state.get("trials") or ())[-16:]],
            "selection_history": [
                dict(row) for row in (self.state.get("selection_history") or ())[-8:]
            ],
            "evaluation": evaluation,
            "variants": {name: dict(value) for name, value in VARIANTS.items()},
            "selection_evidence": "REAL_TARGET_OUTCOMES_ONLY",
            "llm_used_for_selection": False,
        }


__all__ = [
    "DevelopmentalArchitectureSearch",
    "MIN_PER_VARIANT",
    "SCHEMA",
    "VARIANTS",
]
