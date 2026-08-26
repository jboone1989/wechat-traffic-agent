"""Immutable experiment records and per-day slot allocation."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .models import AudienceProfile, ContentObjective, TietuDraft


class Experiment(BaseModel):
    """One immutable content experiment. Fields never change after creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    created_at: datetime
    audience: AudienceProfile
    objective: ContentObjective
    candidate_id: str | None = None
    pattern_id: str | None = None
    strategy: dict[str, Any]
    draft: TietuDraft
    source_refs: tuple[dict[str, Any], ...]
    runtime_run_id: str


class ExperimentLedger:
    """Allocates the next free experiment slot under output/<date>/exp-NN."""

    _SLOT = re.compile(r"^exp-(\d+)$")

    def allocate(self, output_root: Path, today: date) -> tuple[str, Path]:
        """Return the next free slot for today; creates the day directory if absent."""
        day_dir = output_root / today.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        numbers = [
            int(match.group(1))
            for name in day_dir.iterdir()
            if name.is_dir() and (match := self._SLOT.match(name.name))
        ]
        slot = max(numbers, default=0) + 1
        experiment_id = f"{today.isoformat()}-exp-{slot:02d}"
        return experiment_id, day_dir / f"exp-{slot:02d}"
