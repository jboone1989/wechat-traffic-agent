from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


class MemorySaliencePolicy:
    """Maps verified event + current Neural Life state to memory strength only.

    It never changes factual content, authority, confidence, or truth status.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.neural_path = self.state_root / "application" / "neural_life.json"

    def neural_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.neural_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def score(
        self,
        event: Mapping[str, Any],
        *,
        neural_life: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        state = dict(self.neural_state() if neural_life is None else neural_life)
        affect = state.get("affect") if isinstance(state.get("affect"), Mapping) else {}
        body = state.get("body") if isinstance(state.get("body"), Mapping) else {}
        drives = state.get("drives") if isinstance(state.get("drives"), Mapping) else {}

        significance = _clamp(float(event.get("significance") or 0.0))
        prediction_error = _clamp(float(event.get("prediction_error") or 0.0))
        identity = _clamp(float(event.get("identity_relevance") or 0.0))
        emotional = _clamp(float(event.get("emotional_intensity") or 0.0))
        arousal = _clamp(float(affect.get("arousal") or 0.0))
        curiosity = _clamp(float(affect.get("curiosity") or 0.0))
        frustration = _clamp(float(affect.get("frustration") or 0.0))
        resource_hunger = _clamp(float(affect.get("resource_hunger") or 0.0))
        resource_pressure = max(
            _clamp(float(body.get("cpu_pressure") or 0.0)),
            _clamp(float(body.get("memory_pressure") or 0.0)),
            _clamp(float(body.get("disk_pressure") or 0.0)),
            _clamp(float(drives.get("survival") or 0.0)),
            resource_hunger,
        )
        neural_activation = max(arousal, curiosity * 0.7, frustration, resource_pressure)
        salience = _clamp(
            0.22 * significance
            + 0.25 * prediction_error
            + 0.18 * identity
            + 0.15 * emotional
            + 0.20 * neural_activation
        )
        # High salience: consolidate sooner, decay slower, recall more readily.
        consolidation_priority = _clamp(0.35 + 0.65 * salience)
        decay_rate = max(0.20, min(1.25, 1.15 - 0.90 * salience))
        recall_boost = max(0.85, min(1.50, 0.90 + 0.60 * salience))
        return {
            "salience": salience,
            "consolidation_priority": consolidation_priority,
            "decay_rate": decay_rate,
            "recall_boost": recall_boost,
            "neural_activation": neural_activation,
        }


__all__ = ["MemorySaliencePolicy"]
