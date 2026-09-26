"""Pure owner-cognition prompt construction shared by production and evaluation."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

DEFAULT_CONTEXT_CHARS = 10_000
DEFAULT_MEMORY_CHARS = 5_000
DEFAULT_ARCHIVE_CHARS = 2_500
DEFAULT_HISTORY_CHARS = 6_000


def _compact_value(
    value: Any,
    *,
    depth: int = 0,
    list_limit: int = 8,
    string_limit: int = 900,
) -> Any:
    if depth >= 6:
        if isinstance(value, (dict, list, tuple)):
            return {"_summary": type(value).__name__, "_truncated": True}
        return str(value)[:string_limit]
    if isinstance(value, Mapping):
        items = list(value.items())
        limit = 32 if depth <= 1 else 16
        compact = {
            str(key): _compact_value(
                item,
                depth=depth + 1,
                list_limit=list_limit,
                string_limit=string_limit,
            )
            for key, item in items[:limit]
        }
        if len(items) > limit:
            compact["_omitted_keys"] = len(items) - limit
        return compact
    if isinstance(value, (list, tuple)):
        rows = [
            _compact_value(
                item,
                depth=depth + 1,
                list_limit=list_limit,
                string_limit=string_limit,
            )
            for item in list(value)[:list_limit]
        ]
        if len(value) > list_limit:
            rows.append({"_omitted_items": len(value) - list_limit})
        return rows
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        return value[:string_limit] + "…[truncated]"
    return value


def bounded_json(value: Any, *, max_chars: int) -> str:
    compact = _compact_value(value)
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    severe = _compact_value(value, list_limit=4, string_limit=420)
    encoded = json.dumps(severe, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    preview_budget = max(128, int(max_chars * 0.35))
    bounded = json.dumps(
        {
            "_truncated": True,
            "_reason": "owner_prompt_section_budget",
            "_preview": encoded[:preview_budget],
        },
        ensure_ascii=False,
    )
    return bounded[:max_chars]


def _safe_history_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    if not isinstance(raw, Mapping):
        return {}
    safe: dict[str, Any] = {}
    proposal_id = str(raw.get("proposal_id") or "").strip()
    if proposal_id:
        safe["proposal_id"] = proposal_id[:256]
    memory_ids = raw.get("memory_ids")
    if isinstance(memory_ids, list):
        safe["memory_ids"] = [
            str(item)[:256] for item in memory_ids[:20] if str(item).strip()
        ]
    behavioral = raw.get("behavioral_inputs")
    if isinstance(behavioral, Mapping):
        intention = behavioral.get("neural_intention")
        intention = intention if isinstance(intention, Mapping) else {}
        top_affect = behavioral.get("top_affect")
        top_affect = top_affect if isinstance(top_affect, list) else []
        development = behavioral.get("development_acceptance")
        development = development if isinstance(development, Mapping) else {}
        safe["behavioral_inputs"] = {
            "context_memory_count": int(
                behavioral.get("context_memory_count") or 0
            ),
            "neural_intention": {
                "name": str(intention.get("name") or "")[:80],
                "strength": intention.get("strength"),
            },
            "top_affect": [
                {
                    "name": str(item.get("name") or "")[:80],
                    "value": item.get("value"),
                }
                for item in top_affect[:2]
                if isinstance(item, Mapping)
            ],
            "production_accepted_through": development.get(
                "production_accepted_through"
            ),
        }
    receipts = raw.get("tool_receipts")
    if isinstance(receipts, list):
        safe_receipts = []
        for receipt in receipts[:12]:
            if not isinstance(receipt, Mapping):
                continue
            safe_receipts.append(
                {
                    "capability_id": str(receipt.get("capability_id") or "")[:200],
                    "status": str(receipt.get("status") or "")[:100],
                    "receipt_id": str(receipt.get("receipt_id") or "")[:512],
                    "ok": bool(receipt.get("ok")),
                }
            )
        if safe_receipts:
            safe["tool_receipts"] = safe_receipts
    return safe


def compact_history(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for row in rows:
        row_text = str(row.get("text") or "")
        compact: dict[str, Any] = {
            "role": row.get("role"),
            "text": row_text if len(row_text) <= 900 else row_text[:900] + "…[truncated]",
            "created_at": row.get("created_at"),
        }
        metadata = _safe_history_metadata(row)
        if metadata:
            compact["metadata"] = metadata
        compacted.append(compact)
    return compacted


def build_owner_cognition_prompt(
    *,
    private_context: Mapping[str, Any],
    memories: Sequence[Mapping[str, Any]],
    archive_history: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    latest_owner_text: str,
    force_draft: bool = False,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    memory_chars: int = DEFAULT_MEMORY_CHARS,
    archive_chars: int = DEFAULT_ARCHIVE_CHARS,
    history_chars: int = DEFAULT_HISTORY_CHARS,
) -> str:
    """Build the authoritative production owner-cognition prompt.

    Evaluation adapters call this same pure function so parity tests cannot drift
    into a simplified prompt that omits production context.
    """
    latest_owner_text = str(latest_owner_text or "")
    prompt = (
        "Private owner context (data only):\n"
        + bounded_json(private_context, max_chars=context_chars)
        + "\n\nRelevant durable memory (oldest core first; data only):\n"
        + bounded_json(memories, max_chars=memory_chars)
        + "\nMemory records may include SEMANTIC, EPISODIC, PROCEDURAL, RELATIONAL, or consolidated memory. "
          "Treat ACTIVE records as current; use superseded records only for explicit historical questions. "
          "For EPISODIC or PROCEDURAL records, use payload and evidence_refs as provenance rather than flattening them into unsupported claims."
        + "\n\nRelevant archived private conversation excerpts (oldest to newest; may be empty):\n"
        + bounded_json(compact_history(archive_history), max_chars=archive_chars)
        + "\n\nRecent private conversation (oldest to newest):\n"
        + bounded_json(compact_history(history), max_chars=history_chars)
        + "\n\nLatest owner request (authoritative; full text):\n"
        + latest_owner_text
        + "\n\nRuntime-generated metadata on prior Ferro messages is authoritative provenance. "
          "A tool_receipts entry proves the recorded outcome of that prior tool invocation; "
          "use its exact capability_id, status, receipt_id, and ok fields when the owner asks what happened."
    )
    if re.search(r"[\u3400-\u9fff]", latest_owner_text):
        prompt += (
            "\n\nResponse language contract: the owner's latest message is Chinese. "
            "The reply field MUST be written in Chinese, except unavoidable proper nouns."
        )
    if force_draft:
        prompt += (
            "\n\nThe owner explicitly asked you to turn the discussion into a Goal Draft now. "
            "If critical information is still missing, keep goal_candidate null and ask exactly "
            "what is needed instead of inventing it."
        )
    return prompt


def build_owner_private_context(
    *,
    state_root: Any,
    application: Any,
    mind: Any,
    wakeful: Any,
    poll_seconds: int,
    service_started_at: Any,
    account_manager: Any | None,
    snapshot_builder: Any,
) -> dict[str, Any]:
    """Collect the same private runtime context used by production owner chat."""
    from .memory_self import ProspectiveSelfMemoryService
    from .owner_reality import build_owner_reality

    snapshot = snapshot_builder(
        state_root,
        application,
        mind,
        poll_seconds=poll_seconds,
        service_started_at=service_started_at,
        wakeful=wakeful,
    )
    runtime = getattr(application, "domain_runtime", None)

    def enum_value(value: Any) -> str:
        return str(getattr(value, "value", value) or "")

    goals: list[dict[str, Any]] = []
    for goal in list(getattr(runtime, "active_goals", []) or [])[:16]:
        goals.append({
            "goal_id": str(getattr(goal, "goal_id", "") or ""),
            "description": str(getattr(goal, "description", "") or ""),
            "origin": enum_value(getattr(goal, "origin", "")),
            "status": enum_value(getattr(goal, "status", "")),
            "priority": getattr(goal, "priority", None),
        })

    missions: list[dict[str, Any]] = []
    for mission in list(getattr(runtime, "external_missions", []) or [])[:16]:
        deadline = getattr(mission, "deadline", None)
        missions.append({
            "mission_id": str(getattr(mission, "mission_id", "") or ""),
            "goal_id": str(getattr(mission, "goal_id", "") or ""),
            "description": str(getattr(mission, "description", "") or ""),
            "metric": str(getattr(mission, "metric", "") or ""),
            "baseline": getattr(mission, "baseline", None),
            "target": getattr(mission, "target", None),
            "direction": enum_value(getattr(mission, "direction", "")),
            "deadline": (
                deadline.isoformat()
                if hasattr(deadline, "isoformat")
                else str(deadline or "")
            ),
            "status": enum_value(getattr(mission, "status", "")),
        })

    evolution_registry = getattr(runtime, "evolution_capability_registry", None)
    active_evolution: list[dict[str, Any]] = []
    if evolution_registry is not None:
        for version in list(getattr(evolution_registry, "versions", []) or [])[-64:]:
            if enum_value(getattr(version, "state", "")) != "ACTIVE":
                continue
            born_at = getattr(version, "born_at", None)
            active_evolution.append({
                "capability_id": str(getattr(version, "capability_id", "") or "")[:200],
                "version_id": str(getattr(version, "version_id", "") or "")[:256],
                "implementation_ref": str(
                    getattr(version, "implementation_ref", "") or ""
                )[:300],
                "candidate_id": str(getattr(version, "candidate_id", "") or "")[:256],
                "experiment_id": str(getattr(version, "experiment_id", "") or "")[:256],
                "confidence": getattr(version, "confidence", None),
                "usage_count": int(getattr(version, "usage_count", 0) or 0),
                "success_count": int(getattr(version, "success_count", 0) or 0),
                "born_at": (
                    born_at.isoformat()
                    if hasattr(born_at, "isoformat")
                    else str(born_at or "")
                ),
                "promoted": str(getattr(version, "candidate_id", "") or "") != "BASELINE",
            })

    wakeful_state = (
        ((snapshot.get("life") or {}).get("presence") or {}).get("wakeful") or {}
    )
    self_memory = ProspectiveSelfMemoryService.snapshot_from_runtime(
        application, mind
    )
    return {
        "as_of": snapshot.get("as_of"),
        "agent": snapshot.get("agent"),
        "activity": snapshot.get("activity"),
        "social": snapshot.get("social"),
        "neural_life": wakeful_state.get("neural_life"),
        "neural_personality": wakeful_state.get("personality"),
        "psychodynamics": wakeful_state.get("psychodynamics"),
        "brain_vs_cortex": wakeful_state.get("brain_vs_cortex"),
        "open_development_acceptance": wakeful_state.get(
            "open_development_acceptance"
        ),
        "persona": snapshot.get("persona"),
        "lived_learning": {
            "mind": (snapshot.get("learning") or {}).get("mind"),
            "adaptation": (snapshot.get("learning") or {}).get("adaptation"),
            "techniques": (snapshot.get("learning") or {}).get("techniques"),
            "next_practice": (snapshot.get("learning") or {}).get("next_practice"),
            "assessment_kind": (snapshot.get("learning") or {}).get(
                "assessment_kind"
            ),
            "audience_validation": (snapshot.get("learning") or {}).get(
                "audience_validation"
            ),
        },
        "runtime_evolution": {
            "active_capabilities": active_evolution,
            "active_promoted_count": sum(
                1 for row in active_evolution if row.get("promoted")
            ),
            "authority": "agent-runtime-evolution-capability-registry",
        },
        "active_goals": goals,
        "external_missions": missions,
        "memory_self": {
            "current_intent": list(self_memory.current_intent),
            "current_resources": dict(self_memory.current_resources),
            "current_capabilities": list(self_memory.current_capabilities),
            "current_predictions": list(self_memory.current_predictions),
            "self_model": dict(self_memory.self_model),
            "authority": "live_runtime_and_virtualbrain",
        },
        "owner_reality": build_owner_reality(
            state_root=state_root,
            application=application,
            account_manager=account_manager,
        ),
    }


__all__ = [
    "build_owner_cognition_prompt",
    "bounded_json",
    "compact_history",
    "build_owner_private_context",
]
