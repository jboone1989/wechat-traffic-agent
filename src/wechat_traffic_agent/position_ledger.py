"""Committed falsifiable positions: continuity and accountability for published claims."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

LEDGER_SCHEMA = 1
ABANDON_AFTER = timedelta(days=5)
RESOLUTION_WINDOW = timedelta(days=7)
_TERMINAL = {"CONFIRMED", "REFUTED", "ABANDONED"}


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class PositionLedger:
    """OPEN -> CONFIRMED | REFUTED | ABANDONED; terminal states are final."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "positions.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = row.get("positions") if isinstance(row, dict) else None
        if not isinstance(rows, list):
            return []
        clean = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("position_id"), str) or not isinstance(item.get("status"), str):
                continue
            if item["status"] not in {"OPEN"} | _TERMINAL:
                continue
            clean.append(item)
        return clean

    def _save(self, rows: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"schema": LEDGER_SCHEMA, "positions": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _valid(self, row: Mapping[str, Any]) -> bool:
        for key in ("position_id", "topic", "proposition", "status"):
            if not str(row.get(key) or "").strip():
                return False
        if row.get("status") == "OPEN" and not str(row.get("falsifier") or "").strip():
            return False
        return _parse_ts(row.get("created_at")) is not None

    def get(self, position_id: str) -> dict[str, Any] | None:
        for row in self._load():
            if row.get("position_id") == position_id:
                return row
        return None

    def register(self, *, topic: str, proposition: str, falsifier: str,
                 evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                 now: datetime) -> dict[str, Any]:
        topic, proposition, falsifier = topic.strip(), proposition.strip(), falsifier.strip()
        if not topic or not proposition or not falsifier:
            raise ValueError("position requires topic, proposition and falsifier")
        rows = [row for row in self._load() if self._valid(row)]
        digest = re.sub(r"[^a-z0-9]", "", f"{topic}:{proposition}".lower())[:24]
        row = {
            "position_id": "pos-" + digest + "-" + now.strftime("%Y%m%d%H%M%S"),
            "topic": topic, "proposition": proposition, "falsifier": falsifier,
            "status": "OPEN", "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "evidence_refs": [str(ref) for ref in evidence_refs],
            "post_ids": [str(draft_id)], "resolution": None,
        }
        rows.append(row)
        self._save(rows)
        return dict(row)

    def transition(self, position_id: str, *, verdict: str, resolution: str,
                   evidence_refs: tuple[str, ...] | list[str], draft_id: str,
                   now: datetime) -> dict[str, Any]:
        if verdict not in {"CONFIRMED", "REFUTED", "ABANDONED"}:
            raise ValueError("verdict must be CONFIRMED, REFUTED or ABANDONED")
        rows = self._load()
        for row in rows:
            if row.get("position_id") != position_id:
                continue
            if row.get("status") != "OPEN":
                raise ValueError(f"position {position_id} is {row.get('status')}, not OPEN")
            if verdict == "REFUTED" and not str(resolution or "").strip():
                raise ValueError("REFUTED requires a resolution explaining what was wrong")
            row["status"] = verdict
            row["resolution"] = str(resolution or "").strip() or None
            row["updated_at"] = now.isoformat()
            row["evidence_refs"] = list(dict.fromkeys(
                [*row.get("evidence_refs", []), *(str(ref) for ref in evidence_refs)]))
            if str(draft_id):
                row["post_ids"] = list(dict.fromkeys([*row.get("post_ids", []), str(draft_id)]))
            self._save(rows)
            return dict(row)
        raise ValueError(f"position {position_id} not found")

    def _sweep(self, rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        changed = False
        for row in rows:
            if row.get("status") != "OPEN":
                continue
            created = _parse_ts(row.get("created_at"))
            if created and now - created > ABANDON_AFTER:
                row["status"] = "ABANDONED"
                row["resolution"] = "no verification within 5 days"
                row["updated_at"] = now.isoformat()
                changed = True
        if changed:
            self._save(rows)
        return rows

    def open_positions(self, *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
        rows = [row for row in self._sweep(self._load(), now) if row.get("status") == "OPEN"]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return [dict(row) for row in rows[:limit]]

    def recent_resolutions(self, *, now: datetime, limit: int = 5) -> list[dict[str, Any]]:
        rows = []
        for row in self._load():
            if row.get("status") not in {"CONFIRMED", "REFUTED"}:
                continue
            updated = _parse_ts(row.get("updated_at"))
            if updated and now - updated <= RESOLUTION_WINDOW:
                rows.append((str(row.get("updated_at")), dict(row)))
        rows.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in rows[:limit]]
