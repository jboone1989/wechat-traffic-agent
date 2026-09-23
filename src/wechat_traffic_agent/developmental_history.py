"""Path-dependent developmental history for Ferro."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "ferro-developmental-history.v1"
MAX_EVENTS = 1024
MAX_DOMAINS = 64


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class DevelopmentalHistory:
    """Evidence-bound life history that can change later policy without resetting identity."""

    MILESTONES = frozenset({
        "FIRST_EXTERNAL_POST",
        "FIRST_SELF_REPAIR",
        "FIRST_PROVIDER_RECOVERY",
        "FIRST_WORLD_REWARD",
        "FIRST_INDEPENDENT_GOAL",
        "FIRST_FAILED_REPAIR",
        "FIRST_RESOURCE_CRISIS",
        "FIRST_SUCCESSFUL_MIGRATION",
    })

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "developmental_history.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "event_seq": 0,
            "events": [],
            "milestones": {},
            "domains": {},
            "transitions": [],
        }

    def record(
        self,
        *,
        kind: str,
        domain: str,
        outcome: float | None = None,
        evidence_refs: Iterable[str] = (),
        now: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        kind = str(kind or "").strip().upper()
        domain = str(domain or "").strip().lower()
        if not kind or not domain:
            raise ValueError("development event requires kind and domain")
        refs = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x)))
        if not refs:
            raise ValueError("development event requires evidence")
        normalized = None if outcome is None else max(-1.0, min(1.0, float(outcome)))
        fingerprint = sha256(
            ("|".join((kind, domain, str(normalized), *refs))).encode()
        ).hexdigest()[:24]
        existing = next(
            (row for row in self.state.get("events") or () if row.get("fingerprint") == fingerprint),
            None,
        )
        if isinstance(existing, Mapping):
            return dict(existing)

        seq = int(self.state.get("event_seq") or 0) + 1
        event = {
            "event_id": f"development:{seq}:{fingerprint[:12]}",
            "fingerprint": fingerprint,
            "seq": seq,
            "observed_at": now.isoformat(),
            "kind": kind,
            "domain": domain,
            "outcome": normalized,
            "evidence_refs": list(refs),
            "metadata": dict(metadata or {}),
        }
        events = list(self.state.get("events") or ())
        events.append(event)

        domains = dict(self.state.get("domains") or {})
        row = dict(domains.get(domain) or {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "utility_sum": 0.0,
            "competence": 0.5,
            "stability": 0.5,
            "last_event_at": None,
            "important_events": [],
        })
        row["attempts"] = int(row.get("attempts") or 0) + 1
        if normalized is not None:
            row["utility_sum"] = round(float(row.get("utility_sum") or 0.0) + normalized, 6)
            if normalized > 0.15:
                row["successes"] = int(row.get("successes") or 0) + 1
            elif normalized < -0.15:
                row["failures"] = int(row.get("failures") or 0) + 1
        attempts = max(1, int(row["attempts"]))
        empirical = _clamp(
            0.5
            + 0.45 * (
                int(row.get("successes") or 0) - int(row.get("failures") or 0)
            ) / attempts
        )
        prior_competence = float(row.get("competence") or 0.5)
        row["competence"] = round(_clamp(prior_competence * 0.82 + empirical * 0.18), 6)
        volatility = abs((normalized or 0.0) - float(row.get("last_outcome") or 0.0))
        row["stability"] = round(_clamp(float(row.get("stability") or 0.5) * 0.90 + (1.0 - min(1.0, volatility)) * 0.10), 6)
        row["last_outcome"] = normalized
        row["last_event_at"] = now.isoformat()
        important = list(row.get("important_events") or ())
        if kind in self.MILESTONES or (normalized is not None and abs(normalized) >= 0.75):
            important.append(event["event_id"])
        row["important_events"] = important[-32:]
        domains[domain] = row

        milestones = dict(self.state.get("milestones") or {})
        if kind in self.MILESTONES and kind not in milestones:
            milestones[kind] = {
                "event_id": event["event_id"],
                "observed_at": now.isoformat(),
                "domain": domain,
                "evidence_refs": list(refs),
            }

        transitions = list(self.state.get("transitions") or ())
        repeated_failure = int(row.get("failures") or 0) >= 3 and int(row.get("failures") or 0) > int(row.get("successes") or 0)
        if repeated_failure and not any(
            item.get("domain") == domain and item.get("kind") == "MASTERY_NEED"
            for item in transitions[-16:]
        ):
            transitions.append({
                "transition_id": "transition:" + sha256(f"{domain}|{seq}|mastery".encode()).hexdigest()[:20],
                "observed_at": now.isoformat(),
                "kind": "MASTERY_NEED",
                "domain": domain,
                "reason": "repeated evidence-bound failures",
                "trigger_event_id": event["event_id"],
                "status": "OPEN",
            })

        if float(row.get("competence") or 0.0) >= 0.72 and int(row.get("successes") or 0) >= 3:
            open_need = next(
                (
                    item for item in reversed(transitions)
                    if item.get("domain") == domain
                    and item.get("kind") == "MASTERY_NEED"
                    and item.get("status") == "OPEN"
                ),
                None,
            )
            if open_need is not None:
                open_need["status"] = "RESOLVED"
                open_need["resolved_at"] = now.isoformat()
                open_need["resolution_event_id"] = event["event_id"]

        self.state.update({
            "updated_at": now.isoformat(),
            "event_seq": seq,
            "events": events[-MAX_EVENTS:],
            "milestones": milestones,
            "domains": dict(list(sorted(domains.items()))[:MAX_DOMAINS]),
            "transitions": transitions[-256:],
        })
        _write(self.path, self.state)
        return event

    def projection(self) -> dict[str, Any]:
        domains = {
            name: {
                "attempts": int(row.get("attempts") or 0),
                "successes": int(row.get("successes") or 0),
                "failures": int(row.get("failures") or 0),
                "competence": float(row.get("competence") or 0.0),
                "stability": float(row.get("stability") or 0.0),
                "last_event_at": row.get("last_event_at"),
            }
            for name, row in (self.state.get("domains") or {}).items()
            if isinstance(row, Mapping)
        }
        return {
            "schema_version": SCHEMA,
            "created_at": self.state.get("created_at"),
            "updated_at": self.state.get("updated_at"),
            "event_count": len(self.state.get("events") or ()),
            "milestones": dict(self.state.get("milestones") or {}),
            "domains": domains,
            "open_developmental_needs": [
                dict(row)
                for row in self.state.get("transitions") or ()
                if isinstance(row, Mapping) and row.get("status") == "OPEN"
            ],
            "path_dependent": True,
        }


__all__ = ["DevelopmentalHistory", "SCHEMA"]
