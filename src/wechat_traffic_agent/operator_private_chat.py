from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4


def _utc_iso(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def _normalize_intent_text(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _normalize_metric(value: object) -> str:
    text = _normalize_intent_text(value)
    compact = re.sub(r"[\\s._-]+", "", text)
    if compact in {"xfollowers", "followers"}:
        return "x.followers"
    if "粉丝" in text and ("x" in text or "twitter" in text):
        return "x.followers"
    return text


OWNER_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reply", "goal_candidate", "memory_candidates", "tool_requests"],
    "properties": {
        "reply": {"type": "string"},
        "tool_requests": {
            "type": "array",
            "items": {"type": "object"},
        },
        "goal_candidate": {
            "type": ["object", "null"],
        },
        "memory_candidates": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
}


def parse_owner_envelope(raw: str) -> dict[str, Any] | None:
    """Accept only a complete root JSON owner envelope, never embedded examples/reasoning."""
    text = str(raw or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("reply"), str):
        return None
    if value.get("goal_candidate") is not None and not isinstance(value.get("goal_candidate"), dict):
        return None
    if not isinstance(value.get("memory_candidates"), list):
        return None
    if "tool_requests" not in value:
        value["tool_requests"] = []
    if not isinstance(value.get("tool_requests"), list):
        return None
    return value


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
        return {"reply": str(raw or "").strip(), "goal_candidate": None, "memory_candidates": [], "tool_requests": []}
    return value if isinstance(value, dict) else {"reply": str(raw or "").strip(), "goal_candidate": None, "memory_candidates": [], "tool_requests": []}


class ReplyJSONStreamDecoder:
    """Expose reply only when it is the first field of a root JSON object."""

    _reply_prefix = re.compile(r'^\s*\{\s*"reply"\s*:\s*"')

    def __init__(self) -> None:
        self._buffer = ""
        self._position = 0
        self._started = False
        self._finished = False
        self._rejected = False
        self._escaped = False
        self._unicode_digits: str | None = None
        self._pending_high_surrogate: int | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def rejected(self) -> bool:
        return self._rejected

    def feed(self, chunk: str) -> str:
        if self._finished or self._rejected or not chunk:
            return ""
        self._buffer += str(chunk)
        if not self._started:
            stripped = self._buffer.lstrip()
            if stripped and stripped[0] != "{":
                self._rejected = True
                return ""
            match = self._reply_prefix.search(self._buffer)
            if match is None:
                if len(self._buffer) > 256:
                    self._rejected = True
                return ""
            self._started = True
            self._position = match.end()

        output: list[str] = []
        while self._position < len(self._buffer) and not self._finished:
            char = self._buffer[self._position]
            self._position += 1
            if self._unicode_digits is not None:
                if char.lower() not in "0123456789abcdef":
                    self._unicode_digits = None
                    output.append("\ufffd")
                    continue
                self._unicode_digits += char
                if len(self._unicode_digits) == 4:
                    codepoint = int(self._unicode_digits, 16)
                    self._unicode_digits = None
                    self._emit_codepoint(codepoint, output)
                continue
            if self._escaped:
                self._escaped = False
                if char == "u":
                    self._unicode_digits = ""
                    continue
                output.append({
                    '"': '"', "\\": "\\", "/": "/",
                    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                }.get(char, char))
                continue
            if char == "\\":
                self._escaped = True
                continue
            if char == '"':
                if self._pending_high_surrogate is not None:
                    output.append("\ufffd")
                    self._pending_high_surrogate = None
                self._finished = True
                break
            output.append(char)
        return "".join(output)

    def _emit_codepoint(self, codepoint: int, output: list[str]) -> None:
        if 0xD800 <= codepoint <= 0xDBFF:
            if self._pending_high_surrogate is not None:
                output.append("\ufffd")
            self._pending_high_surrogate = codepoint
            return
        if 0xDC00 <= codepoint <= 0xDFFF and self._pending_high_surrogate is not None:
            high = self._pending_high_surrogate
            self._pending_high_surrogate = None
            combined = 0x10000 + ((high - 0xD800) << 10) + (codepoint - 0xDC00)
            output.append(chr(combined))
            return
        if self._pending_high_surrogate is not None:
            output.append("\ufffd")
            self._pending_high_surrogate = None
        output.append(chr(codepoint))


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

    def relevant_archive(
        self, query: str, *, recent_limit: int = 80, limit: int = 12
    ) -> list[dict[str, Any]]:
        """Retrieve old owner-chat excerpts without inflating the hot context window."""
        rows = self.history(limit=2000)
        recent_limit = max(1, min(int(recent_limit), 2000))
        archive = rows[:-recent_limit] if len(rows) > recent_limit else []
        if not archive:
            return []
        raw_query = str(query or "").strip()
        if not raw_query:
            return []

        quoted = [
            value.casefold().strip()
            for value in re.findall(r'[\"“‘]([^\"”’]{2,80})[\"”’]', raw_query)
            if value.strip()
        ]
        latin = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9_.:-]{3,}", raw_query)
            if token.casefold() not in {"the", "and", "that", "this", "with", "from", "your"}
        }
        han_terms: set[str] = set()
        for chunk in re.findall(r"[\u3400-\u9fff]{2,}", raw_query):
            for size in (2, 3, 4):
                if len(chunk) < size:
                    continue
                han_terms.update(chunk[i : i + size] for i in range(len(chunk) - size + 1))

        scored: list[tuple[float, int]] = []
        for index, row in enumerate(archive):
            text = str(row.get("text") or "").casefold()
            if not text:
                continue
            score = 0.0
            score += 12.0 * sum(1 for term in quoted if term and term in text)
            score += 2.0 * sum(1 for term in latin if term in text)
            score += sum(min(len(term), 4) / 4 for term in han_terms if term in text)
            if score > 0:
                if str(row.get("role") or "") == "operator":
                    score += 0.25
                scored.append((score, index))
        if not scored:
            return []

        selected: set[int] = set()
        seed_limit = max(1, min(4, int(limit)))
        for _, index in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:seed_limit]:
            for neighbor in (index - 1, index, index + 1):
                if 0 <= neighbor < len(archive):
                    selected.add(neighbor)
        ordered = [archive[index] for index in sorted(selected)]
        return ordered[-max(1, min(int(limit), 40)) :]

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
            "semantic_status": str(raw.get("semantic_status") or "INTERPRETED").strip().upper(),
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
    def _message_text(self, message_id: str) -> str:
        wanted = str(message_id or "").strip()
        if not wanted:
            return ""
        for row in reversed(self.history(limit=2000)):
            if str(row.get("message_id") or "") == wanted:
                return str(row.get("text") or "")
        return ""

    @staticmethod
    def _cancel_runtime_goal(runtime: Any, goal_id: str) -> bool:
        wanted = str(goal_id or "").strip()
        if not wanted:
            return False
        rows = getattr(runtime, "goals", None)
        if not isinstance(rows, list):
            rows = getattr(runtime, "active_goals", None)
        if not isinstance(rows, list):
            return False
        for goal in rows:
            if str(getattr(goal, "goal_id", "") or "") != wanted:
                continue
            if _enum_text(getattr(goal, "status", "")) not in {"ACTIVE", "BLOCKED"}:
                return False
            status = getattr(goal, "status", None)
            if hasattr(getattr(status, "__class__", object), "CANCELLED"):
                goal.status = status.__class__.CANCELLED
            else:
                goal.status = "CANCELLED"
            return True
        return False

    @staticmethod
    def _compatible_external_mission(runtime: Any, row: Mapping[str, Any]) -> Any | None:
        metric = _normalize_metric(row.get("metric"))
        target = row.get("target")
        if not metric or target is None:
            return None
        try:
            target_value = float(target)
        except (TypeError, ValueError):
            return None
        for mission in reversed(list(getattr(runtime, "external_missions", ()) or ())):
            if _enum_text(getattr(mission, "status", "")) != "ACTIVE":
                continue
            if _normalize_metric(getattr(mission, "metric", "")) != metric:
                continue
            try:
                if abs(float(getattr(mission, "target")) - target_value) > 1e-9:
                    continue
            except (TypeError, ValueError, AttributeError):
                continue
            return mission
        return None

    def activate(
        self,
        proposal_id: str,
        runtime: Any,
        *,
        inherited_authorization_from: str = "",
    ) -> dict[str, Any]:
        row = self.get_proposal(proposal_id)
        if str(row.get("status") or "") == "ACTIVATED":
            return row
        if str(row.get("status") or "") != "DRAFT":
            raise ValueError("only DRAFT proposals can be activated")
        kind = str(row.get("kind") or "GENERAL")
        if kind == "EXTERNAL_MISSION":
            from agent_runtime.goals import MissionDirection
            metric = str(row.get("metric") or "").strip()
            if not metric or row.get("baseline") is None or row.get("target") is None:
                raise ValueError("external mission draft requires metric, baseline, and target")
            direction = MissionDirection(str(row.get("direction") or "AT_LEAST"))
            mission = self._compatible_external_mission(runtime, row)
            if mission is None:
                deadline_raw = str(row.get("deadline") or "").strip()
                if not deadline_raw:
                    raise ValueError("new external mission draft requires a deadline")
                deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    raise ValueError("new external mission deadline must include timezone")
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
                row["mission_binding"] = "CREATED"
            else:
                row["mission_binding"] = "EXISTING_COMPATIBLE_MISSION"
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
        row["semantic_status"] = "CANONICAL"
        if inherited_authorization_from:
            row["inherited_activation_from"] = str(inherited_authorization_from)
        self._write_proposal(row)
        self.append(
            "system",
            "Goal activated: " + str(row["description"]),
            metadata={"proposal_id": proposal_id, "goal_id": row.get("goal_id"), "mission_id": row.get("mission_id")},
        )
        return row

    def reconcile_semantic_upgrade(self, proposal_id: str, runtime: Any) -> dict[str, Any]:
        """Supersede an activated outage-capture goal with its later interpretation."""
        row = self.get_proposal(proposal_id)
        if str(row.get("status") or "") != "DRAFT":
            return row
        current_text = _normalize_intent_text(
            self._message_text(str(row.get("source_message_id") or ""))
        )
        if not current_text:
            return row
        prior = None
        for candidate in self.proposals(limit=100):
            if candidate.get("proposal_id") == proposal_id:
                continue
            if str(candidate.get("status") or "") != "ACTIVATED":
                continue
            if str(candidate.get("kind") or "") != "GENERAL":
                continue
            deferred = (
                str(candidate.get("semantic_status") or "") == "PENDING_RECONCILIATION"
                or "interpretation is deferred" in str(candidate.get("reason") or "").casefold()
            )
            if not deferred:
                continue
            prior_text = _normalize_intent_text(
                self._message_text(str(candidate.get("source_message_id") or ""))
            )
            if prior_text and prior_text == current_text:
                prior = candidate
                break
        if prior is None:
            return row

        activated = self.activate(
            proposal_id,
            runtime,
            inherited_authorization_from=str(prior.get("proposal_id") or ""),
        )
        old_goal_id = str(prior.get("goal_id") or "")
        cancelled = self._cancel_runtime_goal(runtime, old_goal_id)
        prior["status"] = "SUPERSEDED"
        prior["semantic_status"] = "SUPERSEDED"
        prior["superseded_by_proposal_id"] = proposal_id
        prior["superseded_at"] = _utc_iso()
        self._write_proposal(prior)
        activated["supersedes_proposal_id"] = str(prior.get("proposal_id") or "")
        activated["superseded_goal_id"] = old_goal_id
        activated["superseded_goal_cancelled"] = bool(cancelled)
        self._write_proposal(activated)
        runtime.persist()
        self.append(
            "system",
            "Goal semantics reconciled: the degraded intake goal was superseded by the canonical interpreted goal.",
            metadata={
                "proposal_id": proposal_id,
                "supersedes_proposal_id": prior.get("proposal_id"),
                "goal_id": activated.get("goal_id"),
                "mission_id": activated.get("mission_id"),
            },
        )
        return activated

    def active_canonical_goal_refs(self, runtime: Any) -> tuple[str, ...]:
        """Return durable owner-goal provenance for canonical goals still active."""
        active_goal_ids = {
            str(getattr(goal, "goal_id", "") or "")
            for goal in (getattr(runtime, "active_goals", ()) or ())
            if str(getattr(goal, "goal_id", "") or "")
        }
        active_mission_ids = {
            str(getattr(mission, "mission_id", "") or "")
            for mission in (getattr(runtime, "external_missions", ()) or ())
            if str(getattr(mission, "mission_id", "") or "")
            and _enum_text(getattr(mission, "status", "")) == "ACTIVE"
        }
        refs: list[str] = []
        for row in self.proposals(limit=100):
            if str(row.get("status") or "") != "ACTIVATED":
                continue
            if str(row.get("semantic_status") or "CANONICAL") != "CANONICAL":
                continue
            goal_id = str(row.get("goal_id") or "").strip()
            mission_id = str(row.get("mission_id") or "").strip()
            kind = str(row.get("kind") or "GENERAL").upper()
            if kind == "EXTERNAL_MISSION":
                if not mission_id or mission_id not in active_mission_ids:
                    continue
            elif not goal_id or goal_id not in active_goal_ids:
                continue
            for value in (goal_id, mission_id, str(row.get("proposal_id") or "").strip()):
                if value and value not in refs:
                    refs.append(value)
        return tuple(refs)

    def reconcile_pending_semantic_upgrades(self, runtime: Any) -> list[dict[str, Any]]:
        """Reconcile persisted interpreted drafts against previously activated degraded intake."""
        reconciled: list[dict[str, Any]] = []
        for candidate in list(self.proposals(limit=100)):
            if str(candidate.get("status") or "") != "DRAFT":
                continue
            proposal_id = str(candidate.get("proposal_id") or "").strip()
            if not proposal_id:
                continue
            updated = self.reconcile_semantic_upgrade(proposal_id, runtime)
            if (
                str(updated.get("status") or "") == "ACTIVATED"
                and str(updated.get("inherited_activation_from") or "").strip()
            ):
                reconciled.append(updated)
        return reconciled

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


__all__ = ["OWNER_ENVELOPE_SCHEMA", "OperatorConversationStore", "ReplyJSONStreamDecoder", "parse_model_json", "parse_owner_envelope"]
