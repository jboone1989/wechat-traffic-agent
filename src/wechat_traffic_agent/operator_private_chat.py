from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


def _utc_iso(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_model_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"reply": str(raw or "").strip(), "goal_candidate": None, "memory_candidates": []}
    return value if isinstance(value, dict) else {"reply": str(raw or "").strip(), "goal_candidate": None, "memory_candidates": []}
class OperatorConversationStore:
    """Durable private owner↔Ferro conversation and goal-draft store."""

    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root) / "application" / "operator_chat"
        self.events_path = self.root / "events.jsonl"
        self.proposals_root = self.root / "goal_proposals"
        self.root.mkdir(parents=True, exist_ok=True)
        self.proposals_root.mkdir(parents=True, exist_ok=True)

    def history(self, *, limit: int = 80) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            return []
        return rows[-max(1, min(int(limit), 2000)) :]

    def append(self, role: str, text: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        role = str(role or "").strip().lower()
        body = str(text or "").strip()
        if role not in {"operator", "ferro", "system"}:
            raise ValueError("invalid conversation role")
        if not body:
            raise ValueError("conversation text must not be empty")
        row = {
            "message_id": "operator-chat:" + uuid4().hex,
            "role": role,
            "text": body,
            "created_at": _utc_iso(),
            "metadata": dict(metadata or {}),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return row
    def proposals(self, *, limit: int = 40) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.proposals_root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[: max(1, min(int(limit), 100))]

    def create_proposal(self, raw: Mapping[str, Any], *, source_message_id: str = "") -> dict[str, Any]:
        kind = str(raw.get("kind") or "GENERAL").strip().upper()
        if kind not in {"GENERAL", "EXTERNAL_MISSION"}:
            kind = "GENERAL"
        description = str(raw.get("description") or raw.get("title") or "").strip()
        if not description:
            raise ValueError("goal proposal requires a description")
        for existing in self.proposals(limit=100):
            if (
                str(existing.get("status") or "") == "DRAFT"
                and str(existing.get("description") or "").strip().casefold() == description.casefold()
            ):
                return existing
        proposal_id = "goal-proposal:" + uuid4().hex
        row = {
            "proposal_id": proposal_id,
            "status": "DRAFT",
            "kind": kind,
            "description": description[:2000],
            "reason": str(raw.get("reason") or "developed through private owner conversation").strip()[:2000],
            "metric": str(raw.get("metric") or "").strip()[:200],
            "baseline": raw.get("baseline"),
            "target": raw.get("target"),
            "deadline": str(raw.get("deadline") or "").strip(),
            "direction": str(raw.get("direction") or "AT_LEAST").strip().upper(),
            "constraints": [str(item)[:500] for item in (raw.get("constraints") or []) if str(item).strip()][:20],
            "source_message_id": str(source_message_id or ""),
            "created_at": _utc_iso(),
        }
        self._write_proposal(row)
        return row

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        path = self._proposal_path(proposal_id)
        if not path.exists():
            raise FileNotFoundError(proposal_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid goal proposal")
        return value
    def activate(self, proposal_id: str, runtime: Any) -> dict[str, Any]:
        row = self.get_proposal(proposal_id)
        if str(row.get("status") or "") == "ACTIVATED":
            return row
        if str(row.get("status") or "") != "DRAFT":
            raise ValueError("only DRAFT proposals can be activated")
        kind = str(row.get("kind") or "GENERAL")
        if kind == "EXTERNAL_MISSION":
            from agent_runtime.goals import MissionDirection
            metric = str(row.get("metric") or "").strip()
            deadline_raw = str(row.get("deadline") or "").strip()
            if not metric or row.get("baseline") is None or row.get("target") is None or not deadline_raw:
                raise ValueError("external mission draft requires metric, baseline, target, and deadline")
            deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError("external mission deadline must include timezone")
            direction = MissionDirection(str(row.get("direction") or "AT_LEAST"))
            mission = runtime.submit_external_mission(
                str(row["description"]),
                metric=metric,
                baseline=float(row["baseline"]),
                target=float(row["target"]),
                deadline=deadline.astimezone(timezone.utc),
                direction=direction,
                constraints=list(row.get("constraints") or []),
                evidence_refs=[proposal_id],
                issued_by="owner-private-conversation",
            )
            row["mission_id"] = mission.mission_id
            row["goal_id"] = mission.goal_id
        else:
            goal = runtime.submit_goal(
                str(row["description"]),
                reason=str(row.get("reason") or "owner private conversation"),
                auto_complete=False,
            )
            row["goal_id"] = goal.goal_id
        runtime.persist()
        row["status"] = "ACTIVATED"
        row["activated_at"] = _utc_iso()
        self._write_proposal(row)
        self.append(
            "system",
            "Goal activated: " + str(row["description"]),
            metadata={"proposal_id": proposal_id, "goal_id": row.get("goal_id"), "mission_id": row.get("mission_id")},
        )
        return row

    def _proposal_path(self, proposal_id: str) -> Path:
        safe = str(proposal_id or "").replace(":", "-").replace("/", "-")
        if not safe or safe in {".", ".."}:
            raise ValueError("invalid proposal id")
        return self.proposals_root / (safe + ".json")

    def _write_proposal(self, row: Mapping[str, Any]) -> None:
        path = self._proposal_path(str(row.get("proposal_id") or ""))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)


__all__ = ["OperatorConversationStore", "parse_model_json"]
